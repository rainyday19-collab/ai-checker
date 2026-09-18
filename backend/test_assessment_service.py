from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from grading import AssessmentResult
from main import app


class MockAssessmentTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database_patch = patch("database.DB_PATH", Path(temporary.name) / "ai_checker.db")
        database_patch.start()
        self.addCleanup(database_patch.stop)
        output = BytesIO()
        Image.new("RGB", (10, 10), "white").save(output, format="PNG")
        self.image = output.getvalue()

    def assess(self, client, **kwargs):
        return client.post("/api/assess", files={"student_work": ("work.png", self.image, "image/png")}, **kwargs)

    def test_default_and_explicit_mock_need_no_key_or_ai(self):
        for environment in [{}, {"GRADING_MODE": "mock"}]:
            with patch.dict(os.environ, environment, clear=True), patch("assessment_service.load_dotenv"), patch("ai_grading.AsyncOpenAI") as ai_client, TestClient(app) as client:
                response = self.assess(client, data={"criteria_text": "Award marks for reasoning."})
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(set(payload), {"id", "grading_mode", "result"})
                self.assertEqual(payload["grading_mode"], "mock")
                percentage = payload["result"].pop("percentage")
                result = AssessmentResult.model_validate(payload["result"])
                self.assertEqual((result.total_score, result.max_score, percentage), (17, 20, 85))
                self.assertEqual([question.score for question in result.questions], [4, 5, 3, 5])
                self.assertTrue(all(question.evidence for question in result.questions))
                ai_client.assert_not_called()

    def test_openai_and_unknown_modes_are_blocked(self):
        for mode in ["openai", "invalid"]:
            with patch.dict(os.environ, {"GRADING_MODE": mode, "OPENAI_API_KEY": "test-key-not-real"}, clear=True), patch("assessment_service.load_dotenv"), patch("ai_grading.AsyncOpenAI") as ai_client, TestClient(app) as client:
                response = self.assess(client, data={"criteria_text": "Criteria"})
                self.assertEqual(response.status_code, 503)
                self.assertIn("GRADING_MODE=mock", response.json()["detail"])
                ai_client.assert_not_called()

    def test_validation_still_runs_before_demo_result(self):
        with patch.dict(os.environ, {}, clear=True), patch("assessment_service.load_dotenv"), TestClient(app) as client:
            self.assertEqual(self.assess(client).status_code, 422)
            invalid = client.post("/api/assess", files={"student_work": ("work.png", b"invalid", "image/png")}, data={"criteria_text": "Criteria"})
            self.assertEqual(invalid.status_code, 422)
            unsupported = client.post("/api/assess", files={"student_work": ("work.txt", b"text", "text/plain")}, data={"criteria_text": "Criteria"})
            self.assertEqual(unsupported.status_code, 415)
            uploaded_scheme = client.post("/api/assess", files={"student_work": ("work.png", self.image, "image/png"), "mark_scheme": ("scheme.png", self.image, "image/png")})
            self.assertEqual(uploaded_scheme.status_code, 200)


if __name__ == "__main__":
    unittest.main()
