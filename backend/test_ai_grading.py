import base64
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

from ai_grading import AIConfigurationError, AIGradingError, build_model_input, grade_assessment
from grading import AssessmentResult, GradingInput, PreparedContent
from main import app


def prepared_input():
    return GradingInput(PreparedContent("text", "work.pdf", "application/pdf", "Student answer"), None, "Award up to 4 marks.")


class AIGradingTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_disabled_even_with_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-not-real"}), patch("ai_grading.AsyncOpenAI") as client:
            with self.assertRaisesRegex(AIConfigurationError, "disabled"):
                await grade_assessment(prepared_input())
            client.assert_not_called()

    async def test_missing_or_placeholder_key(self):
        for key in ["", "your_api_key_here"]:
            with patch.dict(os.environ, {"OPENAI_API_KEY": key}, clear=True), patch("ai_grading.load_dotenv"), patch("ai_grading.AsyncOpenAI") as client:
                with self.assertRaisesRegex(AIConfigurationError, "OPENAI_API_KEY"):
                    await grade_assessment(prepared_input(), allow_api_request=True)
                client.assert_not_called()

    async def test_sdk_structured_parse_with_mock_transport(self):
        # Fixed fixture exercises SDK parsing; it is never returned by application endpoints.
        fixture = {"total_score": 2.5, "max_score": 4, "summary": "Test fixture", "questions": [{"question": "1", "score": 2.5, "max_score": 4, "feedback": "Test feedback", "evidence": None}]}

        def handler(request):
            body = json.loads(request.content)
            self.assertFalse(body["store"])
            self.assertEqual(body["model"], "test-model")
            schema = body["text"]["format"]["schema"]
            self.assertTrue(body["text"]["format"]["strict"])
            self.assertNotIn("percentage", schema["properties"])
            self.assertFalse(schema["additionalProperties"])
            self.assertIn("evidence", schema["$defs"]["QuestionResult"]["required"])
            return httpx.Response(200, json={
                "id": "resp_mock", "object": "response", "created_at": 0,
                "status": "completed", "model": "test-model",
                "output": [{"id": "msg_mock", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": json.dumps(fixture), "annotations": []}]}],
            })

        client = AsyncOpenAI(api_key="test-key-not-real", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with patch("ai_grading.get_configuration", return_value=("test-key-not-real", "test-model")), patch("ai_grading.AsyncOpenAI", return_value=client):
            result = await grade_assessment(prepared_input(), allow_api_request=True)
        self.assertIsInstance(result, AssessmentResult)
        self.assertEqual(result.percentage, 62.5)

    async def test_refusal_is_not_an_assessment(self):
        def handler(request):
            return httpx.Response(200, json={"id": "resp_mock", "object": "response", "created_at": 0, "status": "completed", "model": "test-model", "output": [{"id": "msg_mock", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "refusal", "refusal": "Test refusal"}]}]})

        client = AsyncOpenAI(api_key="test-key-not-real", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with patch("ai_grading.get_configuration", return_value=("test-key-not-real", "test-model")), patch("ai_grading.AsyncOpenAI", return_value=client):
            with self.assertRaises(AIGradingError):
                await grade_assessment(prepared_input(), allow_api_request=True)


class InputAndEndpointTests(unittest.TestCase):
    def test_visual_payload_preserves_bytes(self):
        for kind, mime, filename, expected_type in [("image", "image/png", "work.png", "input_image"), ("pdf_visual", "application/pdf", "work.pdf", "input_file")]:
            content = PreparedContent(kind, filename, mime, original_bytes=b"binary fixture")
            parts = build_model_input(GradingInput(content, content, "Additional criteria"))[0]["content"]
            visual_parts = [part for part in parts if part["type"] == expected_type]
            self.assertEqual(len(visual_parts), 2)
            for part in visual_parts:
                url = part.get("image_url") or part["file_data"]
                self.assertEqual(base64.b64decode(url.split(",", 1)[1]), b"binary fixture")
            self.assertIn("MANUAL GRADING CRITERIA", parts[-1]["text"])

    def test_endpoints_work_without_key_and_never_create_ai_client(self):
        image = BytesIO()
        Image.new("RGB", (10, 10), "white").save(image, format="PNG")
        with TemporaryDirectory() as temporary, patch("database.DB_PATH", Path(temporary) / "ai_checker.db"), patch.dict(os.environ, {}, clear=True), patch("assessment_service.load_dotenv"), patch("ai_grading.AsyncOpenAI") as ai_client, TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            response = client.post("/api/assess", files={"student_work": ("work.png", image.getvalue(), "image/png")}, data={"criteria_text": "Award 4 marks."})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["grading_mode"], "mock")
            self.assertEqual(response.json()["result"]["total_score"], 17)
            ai_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
