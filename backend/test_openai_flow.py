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


FIXTURE = {"total_score": 2.5, "max_score": 4, "summary": "Fixture summary",
           "questions": [{"question": "1", "score": 2.5, "max_score": 4,
                          "feedback": "Fixture feedback", "evidence": "Fixture answer"}]}


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
        self.transport_handler = lambda request: self.response()

        def handler(request):
            self.requests.append(json.loads(request.content))
            return self.transport_handler(request)

        def create_client(**configuration):
            self.assertEqual(configuration["max_retries"], 0)
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
        self.assertEqual(sum(part["type"] == "input_image" for part in parts), 2)
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
        for fixture in [{"unexpected": "invalid"}, {**FIXTURE, "total_score": 8}, "not an assessment"]:
            with self.subTest(fixture=fixture):
                self.requests.clear()
                self.transport_handler = lambda request: self.response(fixture)
                response = self.assess()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(len(self.requests), 1)
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
        self.ai_client.assert_not_called()
        self.assertEqual(self.client.get("/api/assessments").json(), [])

    def test_mock_mode_needs_no_key_and_no_request(self):
        with patch.dict(os.environ, {"GRADING_MODE": "mock", "OPENAI_API_KEY": ""}):
            self.assertEqual(self.assess().json()["grading_mode"], "mock")
        self.ai_client.assert_not_called()
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
