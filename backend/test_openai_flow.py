"""Endpoint coverage through the real SDK with an entirely local HTTP transport."""
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from PIL import Image
from pypdf import PdfWriter

from main import app


FIXTURE = {"summary": "Fixture summary", "questions": [{"question_id": "q1",
    "feedback": "Fixture feedback", "marking_points": [{
        "marking_point_id": "q1_p1",
        "awarded_marks": 2.5, "rationale": "The visible answer partly meets the point.",
        "evidence_status": "found", "evidence": "Fixture answer", "source_location": "Image 1",
    }]}]}

RUBRIC_FIXTURE = {"title": "Fixture rubric", "total_marks": 4, "questions": [{
    "question_text": "Question 1", "max_marks": 4, "marking_points": [{
        "criterion": "Explain the fixture concept", "max_marks": 4,
        "criterion_type": "semantic", "guidance": None,
    }],
}]}


class OpenAIFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.enterContext(patch("database.DB_PATH", Path(temporary) / "test.db"))
        self.enterContext(patch.dict(os.environ, {"GRADING_MODE": "openai",
            "OPENAI_API_KEY": "test-key-not-real", "OPENAI_MODEL": "test-model"}, clear=True))
        # Never load the developer's real credentials, even when testing configuration errors.
        self.enterContext(patch("assessment_service.load_dotenv"))
        self.enterContext(patch("ai_grading.load_dotenv"))
        self.requests = []
        self.rubric_requests = []
        self.transport_handler = lambda request: self.response()

        def handler(request):
            body = json.loads(request.content)
            if body["text"]["format"]["name"] == "canonical_rubric_draft":
                self.rubric_requests.append(body)
                return self.response(RUBRIC_FIXTURE)
            self.requests.append(body)
            return self.transport_handler(request)

        def create_client(**configuration):
            self.assertEqual(configuration["max_retries"], 0)
            self.assertEqual(configuration["timeout"], 120.0)
            return AsyncOpenAI(**configuration,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

        self.ai_client = self.enterContext(patch("ai_grading.AsyncOpenAI", side_effect=create_client))
        self.client = self.enterContext(TestClient(app))
        image = BytesIO()
        Image.new("RGB", (10, 10), "white").save(image, format="PNG")
        self.image = image.getvalue()

    def response(self, fixture=FIXTURE, usage=True):
        data = {"id": "resp_fixture", "object": "response", "created_at": 0,
                "status": "completed", "model": "test-model",
                "output": [{"id": "msg_fixture", "type": "message", "role": "assistant",
                            "status": "completed", "content": [{"type": "output_text",
                            "text": json.dumps(fixture), "annotations": []}]}]}
        if usage:
            data["usage"] = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                             "input_tokens_details": {"cached_tokens": 0},
                             "output_tokens_details": {"reasoning_tokens": 0}}
        return httpx.Response(200, json=data)

    def response_text(self, text, *, status="completed", reason=None, output=True, refusal=False, output_tokens=50):
        content = [{"type": "refusal", "refusal": "Sensitive refusal"}] if refusal else \
            [{"type": "output_text", "text": text, "annotations": []}]
        data = {"id": "resp_fixture", "object": "response", "created_at": 0,
                "status": status, "model": "test-model", "output": []}
        if output:
            data["output"] = [{"id": "msg_fixture", "type": "message", "role": "assistant",
                               "status": status, "content": content}]
        if reason:
            data["incomplete_details"] = {"reason": reason}
        data["usage"] = {"input_tokens": 100, "output_tokens": output_tokens,
                         "total_tokens": 100 + output_tokens,
                         "input_tokens_details": {"cached_tokens": 0},
                         "output_tokens_details": {"reasoning_tokens": 0}}
        return httpx.Response(200, json=data)

    def assess(self, with_scheme=False):
        files = {"student_work": ("work.png", self.image, "image/png")}
        if with_scheme:
            files["mark_scheme"] = ("scheme.png", self.image, "image/png")
        return self.client.post("/api/assess", files=files,
                                data={} if with_scheme else {"criteria_text": "Award 4 marks."})

    def test_success_saved_with_usage_and_both_images_in_one_request(self):
        response = self.assess(with_scheme=True)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["grading_mode"], "openai")
        self.assertEqual(data["result"]["percentage"], 62.5)
        self.assertEqual(data["usage"], {"model": "test-model", "input_tokens": 100,
                                       "output_tokens": 50, "total_tokens": 150})
        self.assertEqual(len(self.requests), 1)
        parts = self.requests[0]["input"][0]["content"]
        self.assertEqual(sum(part["type"] == "input_image" for part in parts), 1)
        self.assertEqual(len(self.rubric_requests), 1)
        self.assertEqual(sum(part["type"] == "input_image" for part in self.rubric_requests[0]["input"][0]["content"]), 1)
        self.assertEqual(self.requests[0]["model"], "test-model")
        saved = self.client.get(f'/api/assessments/{data["id"]}').json()
        self.assertEqual(saved["result"], data["result"])
        self.assertEqual(saved["grading_mode"], "openai")

    def test_usage_can_be_unavailable(self):
        self.transport_handler = lambda request: self.response(usage=False)
        response = self.assess()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["usage"]["input_tokens"])
        self.assertEqual(len(self.requests), 1)

    def test_api_errors_are_safe_not_retried_and_not_saved(self):
        for status, code, expected in [(401, "invalid_api_key", 503),
                                       (429, "insufficient_quota", 402),
                                       (429, "rate_limit_exceeded", 429), (400, "bad_request", 502)]:
            with self.subTest(code=code):
                self.requests.clear()
                self.transport_handler = lambda request: httpx.Response(status, json={"error": {
                    "message": "Sensitive fixture detail must not escape", "type": code, "code": code}})
                response = self.assess()
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("Sensitive fixture", response.text)
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(self.client.get("/api/assessments").json(), [])

    def test_network_failures_are_not_retried_or_saved(self):
        for error, expected in [(httpx.ReadTimeout, 504), (httpx.ConnectError, 502)]:
            with self.subTest(error=error):
                self.requests.clear()
                def fail(request):
                    raise error("Sensitive fixture detail", request=request)
                self.transport_handler = fail
                response = self.assess()
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("Sensitive fixture", response.text)
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(self.client.get("/api/assessments").json(), [])

    def test_malformed_or_invalid_result_is_not_saved(self):
        for fixture in [{"unexpected": "invalid"}, {**FIXTURE, "questions": [{
                "question_id": "q1", "feedback": "Fixture feedback",
                "marking_points": []}]}, "not an assessment"]:
            with self.subTest(fixture=fixture):
                self.requests.clear()
                self.transport_handler = lambda request: self.response(fixture)
                response = self.assess()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(self.client.get("/api/assessments").json(), [])

    def test_invalid_result_logs_only_safe_validation_metadata_and_path(self):
        sensitive = "private student answer must never be logged"
        self.transport_handler = lambda request: self.response({"summary": sensitive, "questions": [{
            "question_id": "q1", "feedback": sensitive, "marking_points": [{
                "marking_point_id": "q1_p1", "awarded_marks": 8,
                "rationale": sensitive, "evidence_status": "found", "evidence": sensitive,
                "source_location": "Image 1",
            }],
        }]})
        with self.assertLogs("ai_grading", level="WARNING") as captured:
            response = self.assess()
        self.assertEqual(response.status_code, 502)
        message = "\n".join(captured.output)
        self.assertIn("path=new_assessment", message)
        self.assertIn("category=semantic_result_invalid", message)
        self.assertIn("fields=questions.0.marking_points.0", message)
        self.assertIn("parsed_exists=True", message)
        self.assertIn("question_id=q1", message)
        self.assertIn("marking_point_id=q1_p1", message)
        self.assertIn("rule=awarded_marks_exceeds_max", message)
        self.assertIn("status=partially_met", message)
        self.assertIn("evidence_status=found", message)
        self.assertIn("awarded_marks=8.0", message)
        self.assertIn("max_marks=4.0", message)
        self.assertNotIn(sensitive, message)

    def test_output_failures_are_classified_before_json_validation(self):
        cases = [
            (lambda: self.response_text('{"summary":', status="incomplete", reason="max_output_tokens",
                                        output_tokens=6000), "output_truncated", "max_output_tokens"),
            (lambda: self.response_text("{}", output=False), "structured_output_missing", "none"),
            (lambda: self.response_text("{}", refusal=True), "model_refusal", "none"),
        ]
        for response_factory, category, reason in cases:
            with self.subTest(category=category):
                self.requests.clear()
                self.transport_handler = lambda request, factory=response_factory: factory()
                with self.assertLogs("ai_grading", level="WARNING") as captured:
                    response = self.assess()
                self.assertEqual(response.status_code, 502)
                message = "\n".join(captured.output)
                self.assertIn(f"category={category}", message)
                self.assertIn(f"incomplete_reason={reason}", message)
                self.assertIn("output_token_limit=6000", message)
                self.assertEqual(self.client.get("/api/assessments").json(), [])

    def test_json_schema_and_semantic_failures_have_distinct_categories(self):
        cases = [
            ("{", "structured_json_invalid"),
            (json.dumps({"summary": "Fixture", "questions": []}), "structured_schema_invalid"),
            (json.dumps({"summary": "Fixture", "questions": [{"question_id": "q1",
                "feedback": "Fixture", "marking_points": [{"marking_point_id": "q1_p1",
                "awarded_marks": 5, "rationale": "Fixture", "evidence_status": "found",
                "evidence": "Fixture answer", "source_location": "Image 1"}]}]}), "semantic_result_invalid"),
        ]
        for text, category in cases:
            with self.subTest(category=category):
                self.requests.clear()
                self.transport_handler = lambda request, value=text: self.response_text(value)
                with self.assertLogs("ai_grading", level="WARNING") as captured:
                    response = self.assess()
                self.assertEqual(response.status_code, 502)
                self.assertIn(f"category={category}", "\n".join(captured.output))
                self.assertEqual(self.client.get("/api/assessments").json(), [])

    def test_missing_key_makes_no_request(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(self.assess().status_code, 503)
        self.ai_client.assert_not_called()
        self.assertEqual(self.client.get("/api/assessments").json(), [])

    def test_visual_pdf_rejected_before_request(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        pdf = BytesIO()
        writer.write(pdf)
        response = self.client.post("/api/assess", files={"student_work":
            ("scan.pdf", pdf.getvalue(), "application/pdf")}, data={"criteria_text": "Award 4 marks."})
        self.assertEqual(response.status_code, 422)
        self.assertIn("selectable text", response.json()["detail"])
        self.assertEqual(len(self.rubric_requests), 1)
        self.assertEqual(len(self.requests), 0)
        self.assertEqual(self.client.get("/api/assessments").json(), [])

    def test_mock_mode_needs_no_key_and_no_request(self):
        with patch.dict(os.environ, {"GRADING_MODE": "mock", "OPENAI_API_KEY": ""}):
            self.assertEqual(self.assess().json()["grading_mode"], "mock")
        self.ai_client.assert_not_called()
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
