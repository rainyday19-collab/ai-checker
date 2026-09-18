from io import BytesIO
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import database
from main import app


class ClassesTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.enterContext(patch("database.DB_PATH", Path(temporary) / "test.db"))
        self.enterContext(patch.dict("os.environ", {"GRADING_MODE": "mock"}, clear=True))
        self.enterContext(patch("assessment_service.load_dotenv"))
        self.ai_client = self.enterContext(patch("ai_grading.AsyncOpenAI"))
        self.client = self.enterContext(TestClient(app))

    def tearDown(self):
        self.ai_client.assert_not_called()

    def create_class(self, name="Year 11"):
        response = self.client.post("/api/classes", json={"name": name, "subject": "Computer Science", "description": "IGCSE"})
        self.assertEqual(response.status_code, 201)
        return response.json()

    def create_assignment(self, class_id, title="Programming test"):
        response = self.client.post(f"/api/classes/{class_id}/assignments", json={
            "title": title, "description": "Fundamentals", "total_marks": 20.5,
            "mark_scheme_text": "Award marks for correct reasoning."})
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_class_create_list_get_delete(self):
        self.assertEqual(self.client.get("/api/classes").json(), [])
        first = self.create_class()
        second = self.create_class("Year 12")
        self.assertEqual(first["assignment_count"], 0)
        self.assertEqual(self.client.get(f'/api/classes/{first["id"]}').json(), first)
        self.assertEqual([item["id"] for item in self.client.get("/api/classes").json()], [second["id"], first["id"]])
        self.assertEqual(self.client.delete(f'/api/classes/{first["id"]}').json()["status"], "deleted")
        self.assertEqual(self.client.get(f'/api/classes/{first["id"]}').status_code, 404)

    def test_required_class_fields_and_whitespace(self):
        for values in [{}, {"name": "Year 11"}, {"subject": "CS"},
                       {"name": " ", "subject": "CS"}, {"name": "Test", "subject": " "}]:
            self.assertEqual(self.client.post("/api/classes", json=values).status_code, 422)
        response = self.client.post("/api/classes", json={"name": " Year 11 ", "subject": " CS "})
        self.assertEqual(response.json()["name"], "Year 11")

    def test_assignment_create_list_get_delete(self):
        parent = self.create_class()["id"]
        self.assertEqual(self.client.get(f"/api/classes/{parent}/assignments").json(), [])
        first = self.create_assignment(parent)
        second = self.create_assignment(parent, "Second test")
        self.assertEqual(self.client.get(f'/api/assignments/{first["id"]}').json(), first)
        self.assertEqual(first["total_marks"], 20.5)
        self.assertEqual(first["class_id"], parent)
        self.assertEqual([item["id"] for item in self.client.get(f"/api/classes/{parent}/assignments").json()], [second["id"], first["id"]])
        self.assertEqual(self.client.get(f"/api/classes/{parent}").json()["assignment_count"], 2)
        self.assertEqual(self.client.delete(f'/api/assignments/{first["id"]}').status_code, 200)
        self.assertEqual(self.client.get(f"/api/classes/{parent}").json()["assignment_count"], 1)

    def test_optional_assignment_fields(self):
        parent = self.create_class()["id"]
        response = self.client.post(f"/api/classes/{parent}/assignments", json={"title": " Minimal ", "mark_scheme_text": "Award 2 marks."})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["title"], "Minimal")
        self.assertIsNone(response.json()["total_marks"])
        self.assertEqual(response.json()["mark_scheme_text"], "Award 2 marks.")

    def test_invalid_assignment_and_total_marks(self):
        parent = self.create_class()["id"]
        for values in [{}, {"title": " "}, {"title": "Test", "total_marks": 0},
                       {"title": "Test", "total_marks": -1}, {"title": "Test", "total_marks": "NaN"},
                       {"title": "Test", "total_marks": "Infinity"}, {"title": "Test", "total_marks": "invalid"}]:
            self.assertEqual(self.client.post(f"/api/classes/{parent}/assignments", json={**values, "mark_scheme_text": "Award marks."}).status_code, 422)
        self.assertEqual(self.client.get(f"/api/classes/{parent}/assignments").json(), [])

    def test_missing_classes_and_assignments(self):
        for path in ["/api/classes/999", "/api/assignments/999"]:
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.delete(path).status_code, 404)
        self.assertEqual(self.client.get("/api/classes/999/assignments").status_code, 404)
        self.assertEqual(self.client.post("/api/classes/999/assignments", json={"title": "Test"}).status_code, 404)
        self.assertEqual(self.client.get("/api/assignments/not-an-id").status_code, 422)

    def test_class_cascade_preserves_other_class_and_assessments(self):
        parent = self.create_class()["id"]
        assignment = self.create_assignment(parent)
        other = self.create_class("Other")["id"]
        other_assignment = self.create_assignment(other)
        image = BytesIO()
        Image.new("RGB", (10, 10), "white").save(image, format="PNG")
        assessed = self.client.post("/api/assess", files={"student_work": ("fixture.png", image.getvalue(), "image/png")}, data={"criteria_text": "Award marks."})
        self.assertEqual(assessed.status_code, 200)
        saved = assessed.json()
        database.initialize_database()
        self.assertEqual(self.client.delete(f"/api/classes/{parent}").status_code, 200)
        self.assertEqual(self.client.get(f'/api/assignments/{assignment["id"]}').status_code, 404)
        self.assertEqual(self.client.get(f'/api/assignments/{other_assignment["id"]}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/assessments/{saved["id"]}').json()["result"], saved["result"])
        self.assertEqual(self.client.get("/api/statistics").json()["total_assessments"], 1)
        self.assertEqual(len(self.client.get("/api/assessments").json()), 1)

    def test_foreign_keys_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError), database.connection() as connection:
            connection.execute("INSERT INTO assignments (class_id, title, created_at) VALUES (999, 'Test', 'today')")

    def test_database_error_is_safe(self):
        with patch("database.list_classes", side_effect=sqlite3.OperationalError("private fixture")):
            response = self.client.get("/api/classes")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private fixture", response.text)


if __name__ == "__main__":
    unittest.main()
