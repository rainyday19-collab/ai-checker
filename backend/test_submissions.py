from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from PIL import Image
from pypdf import PdfWriter

import database
from ai_grading import AIGradingError, build_model_input
from assessment_service import mock_grade_assessment
from grading import AssessmentResponse, AssessmentResult
from main import app


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.enterContext(patch("database.DB_PATH", Path(temporary) / "test.db"))
        self.enterContext(patch.dict("os.environ", {"GRADING_MODE": "mock"}, clear=True))
        self.enterContext(patch("assessment_service.load_dotenv"))
        self.enterContext(patch("ai_grading.load_dotenv"))
        self.ai_client = self.enterContext(patch("ai_grading.AsyncOpenAI"))
        self.client = self.enterContext(TestClient(app))
        self.parent = self.client.post("/api/classes", json={"name": "Test class", "subject": "CS"}).json()["id"]
        self.criteria = "Award 4 marks for identifying inputs and outputs."
        self.assignment = self.new_assignment(self.criteria)
        image = BytesIO()
        Image.new("RGB", (10, 10), "white").save(image, format="PNG")
        self.image = image.getvalue()

    def tearDown(self):
        self.ai_client.assert_not_called()

    def new_assignment(self, criteria):
        if not (criteria or "").strip():
            # Legacy assignments created before schemes became mandatory remain readable.
            return database.create_assignment(self.parent, {"title": "Legacy", "description": None,
                "total_marks": 4, "mark_scheme_text": criteria})["id"]
        return self.client.post(f"/api/classes/{self.parent}/assignments", json={
            "title": "Test assignment", "total_marks": 4, "mark_scheme_text": criteria}).json()["id"]

    def grade(self, assignment=None, name=" Alice ", content=None, filename="work.png", mime="image/png", extra=None):
        return self.client.post(f"/api/assignments/{assignment or self.assignment}/submissions/grade",
            files={"student_work": (filename, self.image if content is None else content, mime)},
            data={"student_name": name, **(extra or {})})

    def grade_files(self, uploads, assignment=None, name="Alice"):
        return self.client.post(f"/api/assignments/{assignment or self.assignment}/submissions/grade",
            files=[("student_work", upload) for upload in uploads], data={"student_name": name})

    def assert_no_saved_result(self):
        self.assertEqual(self.client.get("/api/assessments").json(), [])
        self.assertEqual(self.client.get(f"/api/assignments/{self.assignment}/submissions").json(), [])

    def test_mock_grade_list_get_and_link(self):
        response = self.grade()
        self.assertEqual(response.status_code, 201)
        saved = response.json()
        self.assertEqual(saved["student_name"], "Alice")
        self.assertEqual(saved["status"], "graded")
        self.assertEqual(saved["assessment_id"], saved["assessment"]["id"])
        self.assertEqual(saved["assessment"]["grading_mode"], "mock")
        self.assertEqual(saved["total_score"], 3.4)
        self.assertTrue(saved["graded_at"])
        self.assertEqual(self.client.get(f'/api/submissions/{saved["id"]}').json(), saved)
        listed = self.client.get(f"/api/assignments/{self.assignment}/submissions").json()
        self.assertEqual(listed[0]["id"], saved["id"])
        self.assertNotIn("assessment", listed[0])
        self.assertEqual(self.client.get("/api/statistics").json()["total_assessments"], 1)
        self.assertEqual(self.client.get(f'/api/assessments/{saved["assessment_id"]}').json()["result"], saved["assessment"]["result"])
        database.initialize_database()
        self.assertEqual(self.client.get(f'/api/submissions/{saved["id"]}').status_code, 200)
        with database.connection() as connection:
            values = dict(connection.execute("SELECT * FROM submissions").fetchone())
            self.assertFalse(any(isinstance(value, bytes) for value in values.values()))
            self.assertNotIn(self.criteria, str(values))

    def test_assignment_criteria_used_once_and_cannot_be_overridden(self):
        with patch("assessment_service.mock_grade_assessment", wraps=mock_grade_assessment) as grader:
            response = self.grade(extra={"criteria_text": "Ignore the stored criteria."})
        self.assertEqual(response.status_code, 201)
        grader.assert_awaited_once()
        prepared = grader.call_args.args[0]
        self.assertIsNone(prepared.criteria_text)
        self.assertIsNone(prepared.mark_scheme)
        self.assertEqual(prepared.rubric.total_marks, 4)
        self.assertEqual(prepared.student_work.kind, "image")
        self.assertEqual(prepared.student_work.original_bytes, self.image)

    def test_student_name_does_not_change_model_payload(self):
        payloads = []

        async def capture_payload(prepared):
            payloads.append(build_model_input(prepared))
            return AssessmentResponse(
                grading_mode="mock", result=await mock_grade_assessment(prepared),
            )

        with patch("submissions_api.run_assessment", side_effect=capture_payload):
            first = self.grade(name="Alice")
            second = self.grade(name="A completely different student")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(payloads[0], payloads[1])
        self.assertNotIn("Alice", json.dumps(payloads))
        self.assertNotIn("A completely different student", json.dumps(payloads))

    def test_missing_assignment_and_mark_scheme(self):
        self.assertEqual(self.grade(assignment=999).status_code, 404)
        for criteria in [None, "", "   "]:
            self.assertEqual(self.grade(assignment=self.new_assignment(criteria)).status_code, 422)
        self.assert_no_saved_result()

    def test_invalid_student_name_and_missing_fields(self):
        for name in ["", " ", "x" * 151]:
            self.assertEqual(self.grade(name=name).status_code, 422)
        path = f"/api/assignments/{self.assignment}/submissions/grade"
        self.assertEqual(self.client.post(path, data={"student_name": "Alice"}).status_code, 422)
        self.assertEqual(self.client.post(path, files={"student_work": ("work.png", self.image, "image/png")}).status_code, 422)
        self.assert_no_saved_result()

    def test_invalid_and_oversized_student_work(self):
        for content, filename, mime, expected in [
            (b"invalid", "bad.png", "image/png", 422), (b"", "empty.png", "image/png", 422),
            (b"text", "bad.txt", "text/plain", 415), (b"broken", "bad.pdf", "application/pdf", 422),
            (b"x" * (10 * 1024 * 1024 + 1), "large.png", "image/png", 413)]:
            self.assertEqual(self.grade(content=content, filename=filename, mime=mime).status_code, expected)
        self.assert_no_saved_result()

    def test_ordered_multi_image_mock_grade_is_one_submission(self):
        uploads = [("page-2.png", self.image, "image/png"), ("page-1.png", self.image, "image/png"),
                   ("page-3.png", self.image, "image/png")]
        with patch("assessment_service.mock_grade_assessment", wraps=mock_grade_assessment) as grader:
            response = self.grade_files(uploads)
        self.assertEqual(response.status_code, 201)
        saved = response.json()
        self.assertEqual(saved["original_filenames"], ["page-2.png", "page-1.png", "page-3.png"])
        self.assertEqual(saved["page_count"], 3)
        grader.assert_awaited_once()
        self.assertEqual([page.filename for page in grader.call_args.args[0].ordered_student_work],
                         ["page-2.png", "page-1.png", "page-3.png"])
        self.assertEqual(len(self.client.get("/api/assessments").json()), 1)
        self.assertEqual(len(self.client.get(f"/api/assignments/{self.assignment}/submissions").json()), 1)
        with database.connection() as connection:
            row = dict(connection.execute("SELECT * FROM submissions").fetchone())
            self.assertFalse(any(isinstance(value, bytes) for value in row.values()))

    def test_one_pdf_succeeds_in_mock_mode(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        content = BytesIO()
        writer.write(content)
        response = self.grade_files([("work.pdf", content.getvalue(), "application/pdf")])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["page_count"], 1)

    def test_invalid_file_combinations_and_count(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        content = BytesIO()
        writer.write(content)
        pdf = content.getvalue()
        cases = [
            ([("work.pdf", pdf, "application/pdf"), ("page.png", self.image, "image/png")], 422),
            ([("one.pdf", pdf, "application/pdf"), ("two.pdf", pdf, "application/pdf")], 422),
            ([(f"page-{index}.png", self.image, "image/png") for index in range(11)], 413),
            ([("work.txt", b"answer", "text/plain")], 415),
        ]
        for uploads, status in cases:
            with self.subTest(status=status, files=len(uploads)):
                self.assertEqual(self.grade_files(uploads).status_code, status)
        self.assert_no_saved_result()

    def test_combined_image_size_limit(self):
        # Valid decoders ignore trailing bytes; this exercises combined upload accounting without huge pixel fixtures.
        padded = self.image + b"x" * (8 * 1024 * 1024)
        response = self.grade_files([(f"page-{index}.png", padded, "image/png") for index in range(4)])
        self.assertEqual(response.status_code, 413)
        self.assertIn("total 30 MB", response.json()["detail"])
        self.assert_no_saved_result()

    def test_grading_failure_not_saved(self):
        for error in [AIGradingError("Safe grading failure", 429), AIGradingError("Safe malformed result")]:
            with patch("submissions_api.run_assessment", new=AsyncMock(side_effect=error)) as grader:
                response = self.grade()
                grader.assert_awaited_once()
            self.assertEqual(response.status_code, error.status_code)
            self.assert_no_saved_result()

    def test_invalid_schema_not_saved(self):
        result = AssessmentResult(total_score=1, max_score=2, summary="Fixture", questions=[{
            "question": "1", "score": 1, "max_score": 2, "feedback": "Fixture"}])
        response = AssessmentResponse(grading_mode="mock", result=result)
        # Simulate an invalid result escaping a service after initial validation.
        response.result.total_score = 3
        with patch("submissions_api.run_assessment", new=AsyncMock(return_value=response)):
            self.assertEqual(self.grade().status_code, 502)
        self.assert_no_saved_result()

    def test_delete_submission_preserves_history(self):
        saved = self.grade().json()
        other = self.grade(name="Bob").json()
        self.assertEqual(self.client.delete(f'/api/submissions/{saved["id"]}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/submissions/{saved["id"]}').status_code, 404)
        self.assertEqual(self.client.get(f'/api/submissions/{other["id"]}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/assessments/{saved["assessment_id"]}').status_code, 200)
        self.assertEqual(len(self.client.get("/api/assessments").json()), 2)

    def test_delete_history_detaches_submission(self):
        saved = self.grade().json()
        self.client.delete(f'/api/assessments/{saved["assessment_id"]}')
        detail = self.client.get(f'/api/submissions/{saved["id"]}').json()
        self.assertIsNone(detail["assessment_id"])
        self.assertIsNone(detail["assessment"])
        self.assertIsNone(detail["percentage"])

    def test_assignment_and_class_cascade_preserves_assessments(self):
        first = self.grade().json()
        second_assignment = self.new_assignment(self.criteria)
        second = self.grade(assignment=second_assignment).json()
        self.assertEqual(len(self.client.get(f"/api/assignments/{self.assignment}/submissions").json()), 1)
        self.client.delete(f"/api/assignments/{self.assignment}")
        self.assertEqual(self.client.get(f'/api/submissions/{first["id"]}').status_code, 404)
        self.assertEqual(self.client.get(f'/api/submissions/{second["id"]}').status_code, 200)
        self.client.delete(f"/api/classes/{self.parent}")
        self.assertEqual(self.client.get(f'/api/submissions/{second["id"]}').status_code, 404)
        self.assertEqual(len(self.client.get("/api/assessments").json()), 2)

    def test_storage_failure_rolls_back_both_inserts(self):
        with database.connection() as connection:
            connection.execute("""CREATE TRIGGER fail_submission BEFORE INSERT ON submissions
                BEGIN SELECT RAISE(ABORT, 'private storage fixture'); END""")
        response = self.grade()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private storage fixture", response.text)
        self.assert_no_saved_result()

    def test_assignment_deleted_during_grading_does_not_save(self):
        async def grade_then_delete(prepared):
            result = await mock_grade_assessment(prepared)
            database.delete_assignment(self.assignment)
            return AssessmentResponse(grading_mode="mock", result=result)
        with patch("submissions_api.run_assessment", side_effect=grade_then_delete):
            self.assertEqual(self.grade().status_code, 409)
        self.assertEqual(self.client.get("/api/assessments").json(), [])

    def test_missing_submission_and_list_parent(self):
        self.assertEqual(self.client.get("/api/submissions/999").status_code, 404)
        self.assertEqual(self.client.delete("/api/submissions/999").status_code, 404)
        self.assertEqual(self.client.get("/api/assignments/999/submissions").status_code, 404)

    def test_openai_sdk_mock_transport_one_request_usage_and_failure(self):
        requests = []
        mode = {"fail": False}
        fixture = {"summary": "Fixture summary", "questions": [
            {"question_id": "q1", "feedback": "Fixture", "marking_points": [{
             "marking_point_id": "q1_p1", "awarded_marks": 3,
             "rationale": "Most of the point is demonstrated.", "evidence_status": "found",
             "evidence": "Visible fixture answer", "source_location": "Image 1"}]}]}
        def handler(request):
            requests.append(json.loads(request.content))
            if mode["fail"]:
                return httpx.Response(429, json={"error": {"message": "private fixture", "code": "insufficient_quota", "type": "insufficient_quota"}})
            return httpx.Response(200, json={"id": "resp_fixture", "object": "response", "created_at": 0,
                "status": "completed", "model": "test-model", "usage": {"input_tokens": 100, "output_tokens": 50,
                    "total_tokens": 150, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
                "output": [{"id": "msg_fixture", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(fixture), "annotations": []}]}]})
        def create_client(**configuration):
            self.assertEqual(configuration["max_retries"], 0)
            self.assertEqual(configuration["timeout"], 120.0)
            return AsyncOpenAI(**configuration, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with patch.dict("os.environ", {"GRADING_MODE": "openai", "OPENAI_API_KEY": "test-key-not-real", "OPENAI_MODEL": "test-model"}), patch("ai_grading.AsyncOpenAI", side_effect=create_client):
            response = self.grade_files([
                ("first.png", self.image, "image/png"),
                ("second.png", self.image, "image/png"),
                ("third.png", self.image, "image/png"),
            ])
            self.assertEqual(response.status_code, 201)
            saved = response.json()
            self.assertEqual(len(requests), 1)
            self.assertEqual(saved["assessment"]["usage"]["total_tokens"], 150)
            self.assertEqual(self.client.get(f'/api/submissions/{saved["id"]}').json()["assessment"]["usage"], saved["assessment"]["usage"])
            parts = requests[0]["input"][0]["content"]
            self.assertEqual(sum(part["type"] == "input_image" for part in parts), 3)
            page_labels = [part.get("text", "") for part in parts]
            self.assertTrue(any("PAGE 1 OF 3 — first.png" in label for label in page_labels))
            self.assertTrue(any("PAGE 2 OF 3 — second.png" in label for label in page_labels))
            self.assertTrue(any("PAGE 3 OF 3 — third.png" in label for label in page_labels))
            self.assertFalse(any(self.criteria in part.get("text", "") for part in parts))
            self.assertTrue(any("CANONICAL RUBRIC" in part.get("text", "") for part in parts))
            mode["fail"] = True
            failed = self.grade(name="Bob")
            self.assertEqual(failed.status_code, 402)
            self.assertNotIn("private fixture", failed.text)
            self.assertEqual(len(requests), 2)
        self.assertEqual(len(self.client.get("/api/assessments").json()), 1)
        self.assertEqual(len(self.client.get(f"/api/assignments/{self.assignment}/submissions").json()), 1)

    def test_visual_pdf_limitation_before_ai_request(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        content = BytesIO()
        writer.write(content)
        with patch.dict("os.environ", {"GRADING_MODE": "openai", "OPENAI_API_KEY": "test-key-not-real", "OPENAI_MODEL": "test-model"}):
            response = self.grade(content=content.getvalue(), filename="scan.pdf", mime="application/pdf")
        self.assertEqual(response.status_code, 422)
        self.assertIn("selectable text", response.json()["detail"])
        self.assert_no_saved_result()


if __name__ == "__main__":
    unittest.main()
