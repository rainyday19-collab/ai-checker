from io import BytesIO
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

import database
from assessment_service import mock_grade_assessment
from main import app
from mark_scheme_storage import cleanup_unreferenced, managed_path, storage_directory


def text_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 250 Td (Award four marks for identifying inputs and outputs.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class AssignmentMarkSchemeTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.root = Path(temporary)
        self.enterContext(patch('database.DB_PATH', self.root / 'test.db'))
        self.enterContext(patch.dict('os.environ', {'GRADING_MODE': 'mock'}, clear=True))
        self.enterContext(patch('assessment_service.load_dotenv'))
        self.enterContext(patch('ai_grading.load_dotenv'))
        self.ai_client = self.enterContext(patch('ai_grading.AsyncOpenAI'))
        self.client = self.enterContext(TestClient(app))
        self.parent = self.client.post('/api/classes', json={'name': 'Fixture', 'subject': 'CS'}).json()['id']
        image = BytesIO()
        Image.new('RGB', (10, 10), 'white').save(image, format='PNG')
        self.image = image.getvalue()

    def tearDown(self):
        self.ai_client.assert_not_called()

    def create(self, file=None, text=None, title='Assignment'):
        data = {'title': title}
        if text is not None:
            data['mark_scheme_text'] = text
        return self.client.post(f'/api/classes/{self.parent}/assignments', data=data,
            files={'mark_scheme_file': file} if file else None)

    def grade(self, assignment_id):
        return self.client.post(f'/api/assignments/{assignment_id}/submissions/grade',
            data={'student_name': 'Fixture student'}, files={'student_work': ('work.png', self.image, 'image/png')})

    def saved_path(self, assignment_id):
        return managed_path(database.get_assignment(assignment_id)['mark_scheme_key'])

    def test_text_only_without_manual_marks(self):
        response = self.create(text=' Award 4 marks. ')
        self.assertEqual(response.status_code, 201)
        values = response.json()
        self.assertIsNone(values['total_marks'])
        self.assertEqual(values['mark_scheme']['source'], 'text')
        self.assertFalse(values['mark_scheme']['has_file'])
        self.assertEqual(self.grade(values['id']).status_code, 201)

    def test_image_persists_with_safe_metadata_and_loads_for_grading(self):
        response = self.create(('scheme.png', self.image, 'image/png'))
        self.assertEqual(response.status_code, 201)
        values = response.json()
        path = self.saved_path(values['id'])
        self.assertEqual(path.read_bytes(), self.image)
        self.assertEqual(values['mark_scheme'], {'source': 'file', 'type': 'image',
            'original_filename': 'scheme.png', 'has_text': False, 'has_file': True})
        for payload in [values, self.client.get(f'/api/assignments/{values["id"]}').json(),
                        self.client.get(f'/api/classes/{self.parent}/assignments').json()]:
            self.assertNotIn(str(self.root), str(payload))
            self.assertNotIn('mark_scheme_key', str(payload))
            self.assertNotIn(path.name, str(payload))
        with patch('assessment_service.mock_grade_assessment', wraps=mock_grade_assessment) as grader:
            self.assertEqual(self.grade(values['id']).status_code, 201)
        grader.assert_awaited_once()
        prepared = grader.call_args.args[0]
        self.assertIsNone(prepared.mark_scheme)
        self.assertIsNone(prepared.criteria_text)
        self.assertEqual(prepared.rubric.total_marks, 20)

    def test_pdf_persists_and_uses_extracted_text(self):
        pdf = text_pdf()
        response = self.create(('scheme.pdf', pdf, 'application/pdf'))
        self.assertEqual(response.status_code, 201)
        values = response.json()
        self.assertEqual(values['mark_scheme']['type'], 'pdf')
        self.assertEqual(self.saved_path(values['id']).read_bytes(), pdf)
        with patch('assessment_service.mock_grade_assessment', wraps=mock_grade_assessment) as grader:
            self.assertEqual(self.grade(values['id']).status_code, 201)
        prepared = grader.call_args.args[0]
        self.assertIsNone(prepared.mark_scheme)
        self.assertEqual(prepared.rubric.rubric_version, 1)

    def test_both_sources_are_combined_once(self):
        values = self.create(('scheme.png', self.image, 'image/png'), 'Additional criteria.').json()
        self.assertEqual(values['mark_scheme']['source'], 'both')
        with patch('assessment_service.mock_grade_assessment', wraps=mock_grade_assessment) as grader:
            self.assertEqual(self.grade(values['id']).status_code, 201)
        grader.assert_awaited_once()
        self.assertIsNone(grader.call_args.args[0].criteria_text)
        self.assertIsNone(grader.call_args.args[0].mark_scheme)
        self.assertIsNotNone(grader.call_args.args[0].rubric)

    def test_no_scheme_and_invalid_title_rejected(self):
        for text in [None, '', '   ']:
            self.assertEqual(self.create(text=text).status_code, 422)
        self.assertEqual(self.create(('scheme.png', self.image, 'image/png'), title=' ').status_code, 422)
        self.assertEqual(database.list_assignments(self.parent), [])

    def test_invalid_and_oversized_files_rejected(self):
        for content, filename, mime, expected in [(b'text', 'bad.txt', 'text/plain', 415),
            (b'invalid', 'bad.png', 'image/png', 422), (b'broken', 'bad.pdf', 'application/pdf', 422),
            (self.image, 'bad.jpg', 'image/jpeg', 422),
            (b'x' * (10 * 1024 * 1024 + 1), 'large.png', 'image/png', 413)]:
            self.assertEqual(self.create((filename, content, mime)).status_code, expected)
        self.assertEqual(database.list_assignments(self.parent), [])
        self.assertFalse(storage_directory().exists())

    def test_filename_traversal_is_replaced_by_unique_keys(self):
        first = self.create(('../../private.png', self.image, 'image/png')).json()
        second = self.create(('..\\private.png', self.image, 'image/png')).json()
        self.assertEqual(first['mark_scheme']['original_filename'], 'private.png')
        paths = [self.saved_path(first['id']), self.saved_path(second['id'])]
        self.assertNotEqual(paths[0], paths[1])
        for path in paths:
            self.assertEqual(path.parent, storage_directory().resolve())
            self.assertNotEqual(path.name, 'private.png')
        self.assertFalse((self.root / 'private.png').exists())

    def test_assignment_cleanup_preserves_other_scheme_and_history(self):
        first = self.create(('first.png', self.image, 'image/png')).json()
        second = self.create(('second.png', self.image, 'image/png')).json()
        first_path, second_path = self.saved_path(first['id']), self.saved_path(second['id'])
        saved = self.grade(first['id']).json()
        self.assertEqual(self.client.delete(f'/api/assignments/{first["id"]}').status_code, 200)
        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.exists())
        self.assertEqual(self.client.get(f'/api/assessments/{saved["assessment_id"]}').status_code, 200)

    def test_class_cleanup_preserves_other_class_file(self):
        first = self.create(('first.png', self.image, 'image/png')).json()
        first_path = self.saved_path(first['id'])
        second_parent = self.client.post('/api/classes', json={'name': 'Other', 'subject': 'CS'}).json()['id']
        self.parent = second_parent
        second = self.create(('second.png', self.image, 'image/png')).json()
        second_path = self.saved_path(second['id'])
        original_parent = database.get_assignment(first['id'])['class_id']
        self.client.delete(f'/api/classes/{original_parent}')
        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.exists())

    def test_referenced_files_are_not_cleaned_and_keys_are_not_shared(self):
        first = self.create(('scheme.png', self.image, 'image/png')).json()
        row = database.get_assignment(first['id'])
        cleanup_unreferenced([row['mark_scheme_key']])
        self.assertTrue(self.saved_path(first['id']).exists())
        with self.assertRaises(sqlite3.IntegrityError):
            database.create_assignment(self.parent, {**row, 'title': 'Cannot share key'})

    def test_unsafe_storage_key_cannot_read_or_delete_outside(self):
        outside = self.root / 'private.png'
        outside.write_bytes(self.image)
        values = self.create(text='Award 4 marks.').json()
        with database.connection() as connection:
            connection.execute('UPDATE assignments SET mark_scheme_key = ?, mark_scheme_filename = ? WHERE id = ?',
                               ('../private.png', 'private.png', values['id']))
        self.assertEqual(self.grade(values['id']).status_code, 201)
        self.assertEqual(self.client.delete(f'/api/assignments/{values["id"]}').status_code, 503)
        self.assertEqual(self.client.delete(f'/api/classes/{self.parent}').status_code, 503)
        self.assertEqual(outside.read_bytes(), self.image)
        self.assertEqual(len(self.client.get('/api/assessments').json()), 1)

    def test_missing_or_corrupted_saved_scheme_does_not_save_result(self):
        values = self.create(('scheme.png', self.image, 'image/png')).json()
        path = self.saved_path(values['id'])
        path.write_bytes(b'corrupted')
        self.assertEqual(self.grade(values['id']).status_code, 201)
        path.unlink()
        self.assertEqual(self.grade(values['id']).status_code, 201)
        self.assertEqual(len(self.client.get('/api/assessments').json()), 2)

    def test_database_failure_cleans_new_file(self):
        with patch('database.create_assignment', side_effect=sqlite3.OperationalError('private fixture')):
            response = self.create(('scheme.png', self.image, 'image/png'))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private fixture', response.text)
        self.assertEqual(list(storage_directory().iterdir()), [])

    def test_low_text_pdf_mark_scheme_uses_persisted_rubric(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        output = BytesIO()
        writer.write(output)
        values = self.create(('scan.pdf', output.getvalue(), 'application/pdf')).json()
        response = self.grade(values['id'])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.client.get('/api/assessments').json()), 1)

    def test_additive_migration_preserves_legacy_assignment(self):
        legacy = self.root / 'legacy.db'
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute('''CREATE TABLE assignments (id INTEGER PRIMARY KEY, class_id INTEGER,
                title TEXT, description TEXT, total_marks REAL, mark_scheme_text TEXT, created_at TEXT)''')
            connection.execute("INSERT INTO assignments VALUES (1, 1, 'Legacy', NULL, 20, 'Award 20 marks.', '2026-09-01')")
        with patch('database.DB_PATH', legacy):
            database.initialize_database()
            database.initialize_database()
            row = self.client.get('/api/assignments/1').json()
            self.assertEqual(row['total_marks'], 20)
            self.assertEqual(row['mark_scheme']['source'], 'text')
            self.assertEqual(self.grade(1).status_code, 201)

    def test_openai_mock_uses_both_images_in_one_request_and_result_maximum(self):
        values = self.create(('scheme.png', self.image, 'image/png'), 'Additional criteria.').json()
        client = MagicMock()
        content = SimpleNamespace(type='output_text', text=json.dumps({'summary': 'Fixture', 'questions': [
            {'question_id': 'q1', 'feedback': 'Fixture', 'marking_points': [{
             'marking_point_id': 'q1_p1', 'awarded_marks': 3,
             'rationale': 'Most of the point is demonstrated.', 'evidence_status': 'found',
             'evidence': 'Visible fixture answer', 'source_location': 'Image 1'}]}]}))
        client.responses.create = AsyncMock(return_value=SimpleNamespace(status='completed', incomplete_details=None,
            output=[SimpleNamespace(type='message', content=[content])], model='test-model',
            usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150)))
        manager = MagicMock()
        manager.__aenter__.return_value = client
        with patch.dict('os.environ', {'GRADING_MODE': 'openai', 'OPENAI_API_KEY': 'test-key-not-real', 'OPENAI_MODEL': 'test-model'}), patch('ai_grading.AsyncOpenAI', return_value=manager) as factory:
            response = self.grade(values['id'])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['max_score'], 20)
        self.assertIsNone(database.get_assignment(values['id'])['total_marks'])
        factory.assert_called_once()
        self.assertEqual(factory.call_args.kwargs['max_retries'], 0)
        self.assertEqual(factory.call_args.kwargs['timeout'], 120.0)
        client.responses.create.assert_awaited_once()
        parts = client.responses.create.call_args.kwargs['input'][0]['content']
        self.assertEqual(sum(part['type'] == 'input_image' for part in parts), 1)
        self.assertEqual(sum('Additional criteria.' in part.get('text', '') for part in parts), 0)
        self.assertTrue(any('CANONICAL RUBRIC' in part.get('text', '') for part in parts))


if __name__ == '__main__':
    unittest.main()
