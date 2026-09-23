from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
from openai import AsyncOpenAI
from PIL import Image, ImageDraw
import pymupdf
from pypdf import PdfReader, PdfWriter

import database
from ai_grading import build_model_input, build_rubric_input
from document_processing import (MAX_PDF_PAGES, PDF_RENDER_DPI, SUPPORTED_TYPES,
                                 extract_content, render_sparse_pdf_pages)
from grading import GradingInput, prepare_document, prepare_grading_input
from main import app
from rubric import demo_rubric


PDF_MIME = SUPPORTED_TYPES[".pdf"]


def page_image(text="Scanned answer: four", size=(900, 1200)) -> bytes:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 120), text, fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def pdf_bytes(page_kinds=("image",), *, page_size=(612, 792)) -> bytes:
    document = pymupdf.open()
    for index, kind in enumerate(page_kinds, start=1):
        page = document.new_page(width=page_size[0], height=page_size[1])
        if kind == "text":
            page.insert_text((72, 100), f"PDF page {index} contains enough selectable text for grading.", fontsize=12)
        elif kind == "image":
            page.insert_image(page.rect, stream=page_image(f"Scanned page {index}"))
        elif kind != "blank":
            raise ValueError(f"Unknown test page kind: {kind}")
    data = document.tobytes()
    document.close()
    return data


def encrypted_pdf() -> bytes:
    reader = PdfReader(BytesIO(pdf_bytes(("text",))))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("fixture-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class PDFProcessingTests(unittest.TestCase):
    def test_normal_text_pdf_uses_text_without_rendering(self):
        with patch("document_processing.render_sparse_pdf_pages") as renderer:
            document = extract_content(pdf_bytes(("text",)), "text.pdf", PDF_MIME, "Student work")
        renderer.assert_not_called()
        self.assertFalse(document.requires_visual_processing)
        self.assertIn("[PDF Page 1 - extracted text]", document.text)
        prepared = prepare_document(document)
        self.assertEqual(prepared.kind, "text")
        self.assertFalse(prepared.pdf_pages)

    def test_scanned_page_is_detected_and_rendered_in_memory(self):
        document = extract_content(pdf_bytes(("image",)), "scan.pdf", PDF_MIME, "Student work")
        self.assertTrue(document.requires_visual_processing)
        self.assertEqual(document.text, "")
        self.assertEqual(len(document.pdf_pages), 1)
        image = document.pdf_pages[0].rendered_image
        self.assertIsNotNone(image)
        self.assertEqual(image.content_type, "image/jpeg")
        self.assertGreater(len(image.original_bytes), 0)
        self.assertEqual(round(image.width / 612 * 72), PDF_RENDER_DPI)

    def test_mixed_pdf_preserves_text_and_rendered_page_order(self):
        document = extract_content(
            pdf_bytes(("text", "image", "text")), "mixed.pdf", PDF_MIME, "Student work",
        )
        self.assertEqual(
            [(page.page_number, bool(page.text), page.rendered_image is not None)
             for page in document.pdf_pages],
            [(1, True, False), (2, False, True), (3, True, False)],
        )
        prepared = prepare_grading_input(document, rubric=demo_rubric(4))
        parts = build_model_input(prepared)[0]["content"]
        page_parts = [part for part in parts if "SOURCE: mixed.pdf, page" in part.get("text", "")]
        self.assertEqual([part["text"].split(" — ")[0] for part in page_parts], [
            "SOURCE: mixed.pdf, page 1", "SOURCE: mixed.pdf, page 2", "SOURCE: mixed.pdf, page 3",
        ])
        self.assertEqual(sum(part["type"] == "input_image" for part in parts), 1)

    def test_prepared_scanned_pdf_uses_images_not_original_pdf_bytes(self):
        original = pdf_bytes(("image", "image"))
        document = extract_content(original, "answers.pdf", PDF_MIME, "Student work")
        prepared = prepare_document(document)
        self.assertEqual(prepared.kind, "pdf_visual")
        self.assertIsNone(prepared.original_bytes)
        parts = build_model_input(GradingInput(prepared, None, None, rubric=demo_rubric(4)))[0]["content"]
        self.assertEqual(sum(part["type"] == "input_image" for part in parts), 2)
        self.assertFalse(any(part["type"] == "input_file" for part in parts))

    def test_scanned_mark_scheme_uses_same_multimodal_builder(self):
        scheme = prepare_document(extract_content(
            pdf_bytes(("image", "text")), "scheme.pdf", PDF_MIME, "Mark scheme",
        ))
        parts = build_rubric_input(scheme, None, 4)[0]["content"]
        self.assertEqual(sum(part["type"] == "input_image" for part in parts), 1)
        self.assertTrue(any("SOURCE: scheme.pdf, page 1" in part.get("text", "") for part in parts))
        self.assertTrue(any("SOURCE: scheme.pdf, page 2" in part.get("text", "") for part in parts))

    def test_scanned_student_and_scheme_share_existing_one_request_architecture(self):
        student = extract_content(pdf_bytes(("image",)), "student.pdf", PDF_MIME, "Student work")
        scheme = extract_content(pdf_bytes(("image",)), "scheme.pdf", PDF_MIME, "Mark scheme")
        grading_input = build_model_input(prepare_grading_input(student, rubric=demo_rubric(4)))
        rubric_input = build_rubric_input(prepare_document(scheme), None, 4)
        self.assertEqual(len(grading_input), 1)
        self.assertEqual(len(rubric_input), 1)
        self.assertEqual(sum(part["type"] == "input_image" for part in grading_input[0]["content"]), 1)
        self.assertEqual(sum(part["type"] == "input_image" for part in rubric_input[0]["content"]), 1)

    def test_corrupt_and_encrypted_pdfs_are_rejected_safely(self):
        cases = [(b"%PDF-corrupt", "corrupted"), (encrypted_pdf(), "encrypted")]
        for data, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(Exception) as raised:
                extract_content(data, "unsafe.pdf", PDF_MIME, "Student work")
            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn(expected, raised.exception.detail.lower())
            self.assertNotIn("traceback", raised.exception.detail.lower())

    def test_pdf_page_and_visual_page_limits_are_enforced(self):
        with self.assertRaises(Exception) as raised:
            extract_content(pdf_bytes(("blank",) * (MAX_PDF_PAGES + 1)),
                            "too-many.pdf", PDF_MIME, "Student work")
        self.assertEqual(raised.exception.status_code, 413)
        self.assertIn(str(MAX_PDF_PAGES), raised.exception.detail)

        with self.assertRaises(Exception) as raised:
            extract_content(pdf_bytes(("blank",) * 11), "too-visual.pdf", PDF_MIME, "Student work")
        self.assertEqual(raised.exception.status_code, 413)
        self.assertIn("visual fallback", raised.exception.detail)

    def test_extreme_dimensions_and_rendered_payload_are_rejected(self):
        with self.assertRaises(Exception) as raised:
            extract_content(pdf_bytes(("blank",), page_size=(2000, 2000)),
                            "huge-page.pdf", PDF_MIME, "Student work")
        self.assertEqual(raised.exception.status_code, 413)
        self.assertIn("dimensions", raised.exception.detail)

        with patch("document_processing.MAX_RENDERED_PAGE_BYTES", 1):
            with self.assertRaises(Exception) as raised:
                extract_content(pdf_bytes(("image",)), "large-render.pdf", PDF_MIME, "Student work")
        self.assertEqual(raised.exception.status_code, 413)
        self.assertIn("rendered image", raised.exception.detail)

        with patch("document_processing.MAX_RENDERED_PDF_BYTES", 1):
            with self.assertRaises(Exception) as raised:
                extract_content(pdf_bytes(("image",)), "large-total.pdf", PDF_MIME, "Student work")
        self.assertEqual(raised.exception.status_code, 413)
        self.assertIn("total 20 MB", raised.exception.detail)


class ScannedPDFEndpointTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.root = Path(temporary)
        self.enterContext(patch("database.DB_PATH", self.root / "test.db"))
        self.enterContext(patch.dict("os.environ", {"GRADING_MODE": "mock"}, clear=True))
        self.enterContext(patch("assessment_service.load_dotenv"))
        self.enterContext(patch("ai_grading.load_dotenv"))
        self.ai_client = self.enterContext(patch("ai_grading.AsyncOpenAI"))
        self.client = self.enterContext(TestClient(app))
        parent = self.client.post("/api/classes", json={"name": "PDF", "subject": "CS"}).json()["id"]
        self.assignment = self.client.post(f"/api/classes/{parent}/assignments", json={
            "title": "Scanned PDF", "mark_scheme_text": "Award four marks.",
        }).json()["id"]

    def tearDown(self):
        self.ai_client.assert_not_called()

    def test_scanned_student_pdf_is_accepted_in_mock_mode_and_not_persisted(self):
        scanned = pdf_bytes(("image", "image"))
        response = self.client.post(f"/api/assignments/{self.assignment}/submissions/grade",
            data={"student_name": "Scanned student"},
            files={"student_work": ("student.pdf", scanned, PDF_MIME)})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["page_count"], 1)
        with database.connection() as connection:
            rows = [dict(row) for table in ("submissions", "assessments")
                    for row in connection.execute(f"SELECT * FROM {table}")]
        self.assertFalse(any(isinstance(value, bytes) for row in rows for value in row.values()))
        self.assertNotIn(scanned[:50].hex(), str(rows))
        self.assertEqual(list(self.root.rglob("*student*")), [])

    def test_scanned_mark_scheme_pdf_prepares_assignment_rubric(self):
        parent = self.client.post("/api/classes", json={"name": "Scheme", "subject": "CS"}).json()["id"]
        with patch("document_processing.render_sparse_pdf_pages", wraps=render_sparse_pdf_pages) as renderer:
            response = self.client.post(f"/api/classes/{parent}/assignments", data={"title": "Scanned scheme"},
                files={"mark_scheme_file": ("scheme.pdf", pdf_bytes(("image",)), PDF_MIME)})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["rubric"]["status"], "ready")
        self.assertEqual(response.json()["mark_scheme"]["type"], "pdf")
        renderer.assert_called_once()


class ScannedPDFOpenAIArchitectureTests(unittest.TestCase):
    def test_scanned_student_and_mark_scheme_use_one_request_each(self):
        requests = []
        rubric_fixture = {"title": "Scanned fixture", "total_marks": 4, "questions": [{
            "question_text": "Question 1", "max_marks": 4, "marking_points": [{
                "criterion": "Demonstrate the answer", "max_marks": 4,
                "criterion_type": "semantic", "guidance": None,
            }],
        }]}
        grade_fixture = {"summary": "Fixture", "questions": [{
            "question_id": "q1", "feedback": "Fixture", "marking_points": [{
                "marking_point_id": "q1_p1", "awarded_marks": 4,
                "rationale": "Visible answer", "evidence_status": "found",
                "evidence": "Scanned answer", "source_location": "student.pdf, page 1",
            }],
        }]}

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            fixture = rubric_fixture if body["text"]["format"]["name"] == "canonical_rubric_draft" else grade_fixture
            return httpx.Response(200, json={
                "id": "resp_fixture", "object": "response", "created_at": 0,
                "status": "completed", "model": "test-model",
                "output": [{"id": "msg_fixture", "type": "message", "role": "assistant",
                            "status": "completed", "content": [{"type": "output_text",
                            "text": json.dumps(fixture), "annotations": []}]}],
            })

        def create_client(**configuration):
            return AsyncOpenAI(**configuration,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

        with TemporaryDirectory() as temporary, \
                patch("database.DB_PATH", Path(temporary) / "test.db"), \
                patch.dict(os.environ, {"GRADING_MODE": "openai", "OPENAI_API_KEY": "test-key-not-real",
                                        "OPENAI_MODEL": "test-model"}, clear=True), \
                patch("assessment_service.load_dotenv"), patch("ai_grading.load_dotenv"), \
                patch("ai_grading.AsyncOpenAI", side_effect=create_client), TestClient(app) as client:
            response = client.post("/api/assess", files={
                "student_work": ("student.pdf", pdf_bytes(("image",)), PDF_MIME),
                "mark_scheme": ("scheme.pdf", pdf_bytes(("image",)), PDF_MIME),
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(requests), 2)
        rubric_request = next(item for item in requests if item["text"]["format"]["name"] == "canonical_rubric_draft")
        grade_request = next(item for item in requests if item["text"]["format"]["name"] == "assessment_draft")
        self.assertEqual(sum(part["type"] == "input_image" for part in rubric_request["input"][0]["content"]), 1)
        self.assertEqual(sum(part["type"] == "input_image" for part in grade_request["input"][0]["content"]), 1)


if __name__ == "__main__":
    unittest.main()
