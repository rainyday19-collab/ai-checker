from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
from grading import AssessmentResponse, AssessmentResult
from main import app


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database_patch = patch("database.DB_PATH", Path(temporary.name) / "ai_checker.db")
        database_patch.start()
        self.addCleanup(database_patch.stop)
        self.client = self.enterContext(TestClient(app))

    def save(self, score, maximum, filename="work.pdf"):
        result = AssessmentResult(total_score=score, max_score=maximum, summary="Statistics test fixture", questions=[{"question": "1", "score": score, "max_score": maximum, "feedback": "Test"}])
        return database.save_assessment(AssessmentResponse(grading_mode="mock", result=result), filename, None, True)

    def statistics(self):
        response = self.client.get("/api/statistics")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_empty_database(self):
        self.assertEqual(self.statistics(), {
            "total_assessments": 0, "average_percentage": None, "average_score": None,
            "average_max_score": None, "highest_percentage": None, "recent_assessments": [],
        })

    def test_averages_and_highest_with_different_maxima(self):
        for score, maximum in [(3.5, 5), (9, 10), (2, 4)]:
            self.save(score, maximum)
        stats = self.statistics()
        self.assertEqual(stats["total_assessments"], 3)
        self.assertEqual(stats["average_score"], 4.83)
        self.assertEqual(stats["average_max_score"], 6.33)
        self.assertEqual(stats["average_percentage"], 70)
        self.assertEqual(stats["highest_percentage"], 90)

    def test_recent_five_ordering_and_lightweight_response(self):
        ids = [self.save(index, 6, f"work-{index}.pdf") for index in range(6)]
        with database.connection() as connection:
            connection.execute("UPDATE assessments SET created_at = ?", ("2026-09-18T10:00:00+00:00",))
            connection.execute("UPDATE assessments SET created_at = ? WHERE id = ?", ("2026-09-19T10:00:00+00:00", ids[0]))
        recent = self.statistics()["recent_assessments"]
        self.assertEqual([item["id"] for item in recent], [ids[0], ids[5], ids[4], ids[3], ids[2]])
        self.assertEqual(set(recent[0]), {"id", "created_at", "student_filename", "total_score", "max_score", "percentage"})

    def test_statistics_update_after_deletion(self):
        low = self.save(2, 4)
        high = self.save(4, 4)
        self.assertEqual(self.statistics()["average_percentage"], 75)
        self.client.delete(f"/api/assessments/{high}")
        stats = self.statistics()
        self.assertEqual((stats["total_assessments"], stats["average_percentage"], stats["highest_percentage"]), (1, 50, 50))
        self.assertEqual(stats["recent_assessments"][0]["id"], low)


if __name__ == "__main__":
    unittest.main()
