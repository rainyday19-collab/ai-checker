from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import database
from main import app


class AssessmentPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for replacement in [patch("database.DB_PATH", Path(temporary.name) / "ai_checker.db"), patch.dict("os.environ", {"GRADING_MODE": "mock"}, clear=True), patch("assessment_service.load_dotenv"), patch("ai_grading.load_dotenv")]:
            replacement.start()
            self.addCleanup(replacement.stop)
        ai_patch = patch("ai_grading.AsyncOpenAI")
        self.ai_client = ai_patch.start()
        self.addCleanup(ai_patch.stop)
        self.client = self.enterContext(TestClient(app))
        output = BytesIO()
        Image.new("RGB", (10, 10), "white").save(output, format="PNG")
        self.image = output.getvalue()

    def tearDown(self):
        self.ai_client.assert_not_called()

    def save(self, name="work.png", with_scheme=False):
        files = {"student_work": (name, self.image, "image/png")}
        if with_scheme:
            files["mark_scheme"] = ("scheme.png", self.image, "image/png")
        response = self.client.post("/api/assess", files=files, data={} if with_scheme else {"criteria_text": "Private manual criteria"})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_save_list_retrieve_delete(self):
        first = self.save("first.png")
        second = self.save("second.png", with_scheme=True)
        self.assertIsInstance(first["id"], int)
        history = self.client.get("/api/assessments").json()
        self.assertEqual([item["id"] for item in history], [second["id"], first["id"]])
        self.assertEqual(set(history[0]), {"id", "created_at", "student_filename", "total_score", "max_score", "percentage"})
        saved = self.client.get(f'/api/assessments/{first["id"]}').json()
        self.assertEqual(saved["result"], first["result"])
        self.assertTrue(saved["used_manual_criteria"])
        self.assertIsNone(saved["mark_scheme_filename"])
        self.assertEqual(saved["grading_mode"], "mock")
        saved_scheme = self.client.get(f'/api/assessments/{second["id"]}').json()
        self.assertEqual(saved_scheme["mark_scheme_filename"], "scheme.png")
        self.assertFalse(saved_scheme["used_manual_criteria"])
        with database.connection() as connection:
            row = connection.execute("SELECT * FROM assessments WHERE id = ?", (first["id"],)).fetchone()
            self.assertEqual(json.loads(row["result_json"]), first["result"])
            self.assertNotIn("Private manual criteria", str(dict(row)))
            self.assertFalse(any(isinstance(value, bytes) for value in row))
        deleted = self.client.delete(f'/api/assessments/{first["id"]}')
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["status"], "deleted")
        self.assertEqual(self.client.get(f'/api/assessments/{first["id"]}').status_code, 404)
        self.assertEqual(len(self.client.get("/api/assessments").json()), 1)

    def test_missing_assessment_returns_404(self):
        for method in [self.client.get, self.client.delete]:
            response = method("/api/assessments/999")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["detail"], "Assessment not found.")

    def test_failed_assessment_is_not_saved(self):
        for data, files in [({}, {"student_work": ("work.png", self.image, "image/png")}), ({"criteria_text": "Criteria"}, {"student_work": ("bad.png", b"invalid", "image/png")}), ({"criteria_text": "Criteria"}, {"student_work": ("bad.txt", b"invalid", "text/plain")})]:
            self.assertGreaterEqual(self.client.post("/api/assess", files=files, data=data).status_code, 400)
        with patch.dict("os.environ", {"GRADING_MODE": "openai"}):
            response = self.client.post("/api/assess", files={"student_work": ("work.png", self.image, "image/png")}, data={"criteria_text": "Criteria"})
            self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get("/api/assessments").json(), [])

    def test_records_survive_reinitialization(self):
        saved = self.save()
        database.initialize_database()
        self.assertEqual(database.get_assessment(saved["id"]).result.total_score, 17)


if __name__ == "__main__":
    unittest.main()
