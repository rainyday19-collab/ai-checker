import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
from grading import AssessmentResponse, AssessmentResult
from main import app


class AssignmentCsvExportTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.enterContext(patch("database.DB_PATH", Path(temporary) / "test.db"))
        self.enterContext(patch.dict("os.environ", {"GRADING_MODE": "mock"}, clear=True))
        self.enterContext(patch("assessment_service.load_dotenv"))
        self.enterContext(patch("ai_grading.load_dotenv"))
        self.ai_client = self.enterContext(patch("ai_grading.AsyncOpenAI"))
        self.client = self.enterContext(TestClient(app))
        parent = self.client.post("/api/classes", json={
            "name": '+Year 11, "Компьютеры"', "subject": "Computer Science",
        }).json()
        self.assignment = self.client.post(f"/api/classes/{parent['id']}/assignments", json={
            "title": '=SUM(1,2) "Quiz"', "mark_scheme_text": "Award up to 20 marks.",
        }).json()

    def tearDown(self):
        self.ai_client.assert_not_called()

    def save_result(self, name, filename, score, maximum):
        result = AssessmentResult(
            total_score=score, max_score=maximum, summary="Saved fixture",
            questions=[{"question": "1", "score": score, "max_score": maximum, "feedback": "Saved"}],
        )
        return database.save_submission(
            self.assignment["id"], name, filename,
            AssessmentResponse(grading_mode="mock", result=result),
        )

    @staticmethod
    def rows(response):
        text = response.content.decode("utf-8-sig")
        return list(csv.reader(StringIO(text))), text

    def test_multiple_results_headers_values_escaping_unicode_and_formula_safety(self):
        self.save_result('@Álice, "А"', '-work,"one".pdf', 17.5, 20)
        self.save_result("Bob Smith", "bob.jpg", 14, 20)

        response = self.client.get(f"/api/assignments/{self.assignment['id']}/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(response.headers["content-type"].startswith("text/csv"))
        rows, raw = self.rows(response)
        self.assertEqual(rows[0], ["Student Name", "Score", "Maximum Score", "Percentage", "Assignment",
                                   "Class", "Student Work Filename", "Graded At"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][:7], ["'@Álice, \"А\"", "17.5", "20", "87.5%", "'=SUM(1,2) \"Quiz\"",
                                      "'+Year 11, \"Компьютеры\"", "'-work,\"one\".pdf"])
        self.assertTrue(rows[1][7])
        self.assertEqual(rows[2][0:4], ["Bob Smith", "14", "20", "70%"])
        self.assertIn('""А""', raw)

    def test_empty_export_missing_assignment_and_download_filename(self):
        response = self.client.get(f"/api/assignments/{self.assignment['id']}/export.csv")
        rows, _ = self.rows(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            response.headers["content-disposition"],
            'attachment; filename="sum-1-2-quiz-results.csv"',
        )
        self.assertEqual(self.client.get("/api/assignments/999/export.csv").status_code, 404)

    def test_corrupt_saved_result_fails_safely(self):
        submission_id = self.save_result("Alice", "work.pdf", 1, 2)
        with database.connection() as connection:
            assessment_id = connection.execute(
                "SELECT assessment_id FROM submissions WHERE id = ?", (submission_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE assessments SET result_json = ? WHERE id = ?",
                ('{"total_score": 3, "max_score": 2}', assessment_id),
            )
        response = self.client.get(f"/api/assignments/{self.assignment['id']}/export.csv")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "A saved grading result is invalid and cannot be exported.")


if __name__ == "__main__":
    unittest.main()
