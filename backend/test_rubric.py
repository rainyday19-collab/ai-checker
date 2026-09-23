from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

import database
from ai_grading import AIGradingError, AssessmentDraft, RubricAlignmentError
from assessment_service import mock_grade_assessment
from grading import AssessmentResponse
from main import app
from rubric import CanonicalRubric, CanonicalRubricDraft


def rubric_fixture() -> CanonicalRubric:
    return CanonicalRubric(
        title="Fixture rubric", total_marks=4, questions=[{
            "question_id": "q1", "question_text": "Explain both concepts", "max_marks": 4,
            "marking_points": [
                {"id": "q1_p1", "criterion": "Explain the first concept", "max_marks": 2,
                 "criterion_type": "semantic", "guidance": "Accept valid paraphrases."},
                {"id": "q1_p2", "criterion": "Write the required syntax", "max_marks": 2,
                 "criterion_type": "exact", "guidance": "The tokens must be technically correct."},
            ],
        }],
    )


def evaluation(point_ids=("q1_p1", "q1_p2"), question_id="q1") -> dict:
    return {"summary": "Fixture summary", "questions": [{
        "question_id": question_id, "feedback": "Point-based feedback.",
        "marking_points": [{
            "marking_point_id": point_id, "awarded_marks": 2,
            "rationale": "The required meaning or syntax is present.", "evidence_status": "found",
            "evidence": "Visible student response", "source_location": "Page 1",
        } for point_id in point_ids],
    }]}


class CanonicalRubricModelTests(unittest.TestCase):
    def test_normalized_rubric_gets_stable_backend_ids(self):
        draft = CanonicalRubricDraft.model_validate({
            "title": "Test", "total_marks": 4, "questions": [{
                "question_text": "Question 1", "max_marks": 4, "marking_points": [
                    {"criterion": "Concept", "max_marks": 1, "criterion_type": "semantic", "guidance": None},
                    {"criterion": "Syntax", "max_marks": 3, "criterion_type": "exact", "guidance": "Exact form."},
                ],
            }],
        })
        rubric = draft.to_canonical(4)
        self.assertEqual(rubric.questions[0].question_id, "q1")
        self.assertEqual([point.id for point in rubric.questions[0].marking_points], ["q1_p1", "q1_p2"])
        self.assertEqual(rubric.marking_point_count, 2)

    def test_invalid_question_or_total_maxima_are_rejected(self):
        invalid = [
            {"title": None, "total_marks": 4, "questions": [{"question_text": "Q", "max_marks": 4,
             "marking_points": [{"criterion": "P", "max_marks": 3, "criterion_type": "semantic", "guidance": None}]}]},
            {"title": None, "total_marks": 5, "questions": [{"question_text": "Q", "max_marks": 4,
             "marking_points": [{"criterion": "P", "max_marks": 4, "criterion_type": "semantic", "guidance": None}]}]},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                CanonicalRubricDraft.model_validate(payload).to_canonical()

    def test_grading_maps_canonical_ids_and_preserves_types(self):
        result = AssessmentDraft.model_validate(evaluation()).to_result(rubric_fixture())
        self.assertEqual((result.total_score, result.max_score), (4, 4))
        self.assertEqual(result.questions[0].question_id, "q1")
        self.assertEqual(
            [(point.marking_point_id, point.criterion_type) for point in result.questions[0].marking_points],
            [("q1_p1", "semantic"), ("q1_p2", "exact")],
        )

    def test_status_is_derived_from_award_and_evidence(self):
        payload = evaluation()
        payload["questions"][0]["marking_points"][0]["awarded_marks"] = 1
        payload["questions"][0]["marking_points"][1].update({
            "awarded_marks": 0, "evidence_status": "unclear",
            "evidence": "Unreadable fixture", "source_location": "Page 1",
        })
        result = AssessmentDraft.model_validate(payload).to_result(rubric_fixture())
        self.assertEqual(
            [point.status for point in result.questions[0].marking_points],
            ["partially_met", "unclear"],
        )

    def test_tenth_question_semantic_failure_has_safe_exact_diagnostics(self):
        rubric = CanonicalRubric(total_marks=10, questions=[{
            "question_id": f"q{index}", "question_text": f"Question {index}", "max_marks": 1,
            "marking_points": [{
                "id": f"q{index}_p1", "criterion": "Fixture criterion", "max_marks": 1,
                "criterion_type": "semantic", "guidance": None,
            }],
        } for index in range(1, 11)])
        payload = {"summary": "Fixture", "questions": [{
            "question_id": f"q{index}", "feedback": "Fixture", "marking_points": [{
                "marking_point_id": f"q{index}_p1", "awarded_marks": 1,
                "rationale": "Fixture", "evidence_status": "found",
                "evidence": "Fixture evidence", "source_location": "Page 1",
            }],
        } for index in range(1, 11)]}
        payload["questions"][9]["marking_points"][0].update({
            "evidence_status": "not_found", "evidence": None, "source_location": None,
        })
        with self.assertRaises(RubricAlignmentError) as raised:
            AssessmentDraft.model_validate(payload).to_result(rubric)
        self.assertEqual(raised.exception.fields, ("questions.9.marking_points.0",))
        self.assertEqual(raised.exception.details, {
            "question_id": "q10", "marking_point_id": "q10_p1", "status": "not_met",
            "evidence_status": "not_found", "awarded_marks": 1.0, "max_marks": 1.0,
            "rule": "not_found_requires_zero",
        })

    def test_unknown_duplicate_and_missing_ids_are_rejected(self):
        cases = [
            evaluation(("q1_p1", "q1_p9")),
            evaluation(("q1_p1", "q1_p1")),
            evaluation(("q1_p1",)),
            evaluation(question_id="q9"),
        ]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(RubricAlignmentError):
                AssessmentDraft.model_validate(payload).to_result(rubric_fixture())

    def test_grader_cannot_supply_or_change_max_marks(self):
        payload = evaluation()
        payload["questions"][0]["marking_points"][0]["max_marks"] = 99
        with self.assertRaises(ValidationError):
            AssessmentDraft.model_validate(payload)


class AssignmentRubricLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.enterContext(patch("database.DB_PATH", Path(temporary) / "test.db"))
        self.enterContext(patch.dict("os.environ", {"GRADING_MODE": "mock"}, clear=True))
        self.enterContext(patch("assessment_service.load_dotenv"))
        self.enterContext(patch("ai_grading.load_dotenv"))
        self.client = self.enterContext(TestClient(app))
        self.parent = self.client.post("/api/classes", json={"name": "Test", "subject": "CS"}).json()["id"]
        image = BytesIO()
        Image.new("RGB", (10, 10), "white").save(image, format="PNG")
        self.image = image.getvalue()

    def create(self):
        return self.client.post(f"/api/classes/{self.parent}/assignments", data={
            "title": "Canonical", "total_marks": "4", "mark_scheme_text": "Award four marks.",
        })

    def grade(self, assignment_id, name="Student"):
        return self.client.post(f"/api/assignments/{assignment_id}/submissions/grade",
            data={"student_name": name}, files={"student_work": ("work.png", self.image, "image/png")})

    def test_rubric_persists_and_is_reused_for_students(self):
        normalizer = AsyncMock(return_value=(rubric_fixture(), "mock"))
        with patch("rubric_service.run_rubric_normalization", normalizer):
            created = self.create()
            self.assertEqual(created.status_code, 201)
            assignment_id = created.json()["id"]
            self.assertEqual(created.json()["rubric"]["status"], "ready")
            first = self.grade(assignment_id, "Alice")
            second = self.grade(assignment_id, "Bob")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(normalizer.await_count, 1)
        stored = database.get_assignment_rubric(assignment_id)
        self.assertEqual(stored, rubric_fixture())
        self.assertEqual(
            first.json()["assessment"]["result"]["questions"][0]["question_id"],
            second.json()["assessment"]["result"]["questions"][0]["question_id"],
        )

    def test_legacy_assignment_generates_once_on_first_use(self):
        assignment = database.create_assignment(self.parent, {
            "title": "Legacy", "description": None, "total_marks": 4,
            "mark_scheme_text": "Award four marks.",
        })
        normalizer = AsyncMock(return_value=(rubric_fixture(), "mock"))
        with patch("rubric_service.run_rubric_normalization", normalizer):
            self.assertEqual(self.grade(assignment["id"], "Alice").status_code, 201)
            self.assertEqual(self.grade(assignment["id"], "Bob").status_code, 201)
        self.assertEqual(normalizer.await_count, 1)

    def test_failed_generation_requires_one_manual_retry(self):
        failure = AIGradingError("Safe rubric failure", category="rubric_fixture_failure")
        with patch("rubric_service.run_rubric_normalization", new=AsyncMock(side_effect=failure)) as normalizer:
            created = self.create()
        assignment_id = created.json()["id"]
        self.assertEqual(created.json()["rubric"]["status"], "failed")
        self.assertEqual(self.grade(assignment_id).status_code, 409)
        self.assertEqual(normalizer.await_count, 1)
        with patch("rubric_service.run_rubric_normalization",
                   new=AsyncMock(return_value=(rubric_fixture(), "mock"))) as retry:
            response = self.client.post(f"/api/assignments/{assignment_id}/rubric/retry")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(retry.await_count, 1)
        self.assertEqual(self.grade(assignment_id).status_code, 201)

    def test_batch_style_calls_receive_identical_rubric(self):
        assignment_id = self.create().json()["id"]
        rubrics = []

        async def capture(prepared):
            rubrics.append(prepared.rubric.model_dump())
            return AssessmentResponse(
                grading_mode="mock",
                result=await mock_grade_assessment(prepared),
            )

        with patch("submissions_api.run_assessment", side_effect=capture):
            self.assertEqual(self.grade(assignment_id, "One").status_code, 201)
            self.assertEqual(self.grade(assignment_id, "Two").status_code, 201)
        self.assertEqual(rubrics[0], rubrics[1])


if __name__ == "__main__":
    unittest.main()
