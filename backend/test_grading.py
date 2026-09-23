import unittest

from pydantic import ValidationError

from document_processing import EmbeddedImage, PDFPageContent, ProcessedDocument
from grading import AssessmentResult, MarkingPointResult, QuestionResult, prepare_grading_input


class GradingTests(unittest.TestCase):
    def result(self, **overrides):
        data = {
            "total_score": 2.5,
            "max_score": 4,
            "summary": "Validation fixture, not generated grading.",
            "questions": [{"question": "1", "score": 2.5, "max_score": 4, "feedback": "Test feedback."}],
        }
        return AssessmentResult(**(data | overrides))

    def test_decimal_marks_and_derived_percentage(self):
        result = self.result()
        self.assertEqual(result.percentage, 62.5)
        self.assertEqual(result.model_dump()["percentage"], 62.5)
        with self.assertRaises(ValidationError):
            self.result(percentage=99)

    def test_invalid_question_scores(self):
        for score, maximum in [(-1, 4), (5, 4), (0, 0), (float("nan"), 4), (1, float("inf"))]:
            with self.subTest(score=score, maximum=maximum), self.assertRaises(ValidationError):
                QuestionResult(question="1", score=score, max_score=maximum, feedback="Test.")

    def test_found_evidence_rules(self):
        valid = QuestionResult(
            question="1", score=2, max_score=3, feedback="Two points are present.",
            evidence_status="found", evidence="RAM stores data temporarily", source_location="Page 1",
        )
        self.assertEqual(valid.evidence_status, "found")
        for evidence, source in [(None, "Page 1"), ("  ", "Page 1"), ("Student answer", None)]:
            with self.subTest(evidence=evidence, source=source), self.assertRaises(ValidationError):
                QuestionResult(
                    question="1", score=2, max_score=3, feedback="Test.",
                    evidence_status="found", evidence=evidence, source_location=source,
                )
        with self.assertRaises(ValidationError):
            QuestionResult(
                question="1", score=0, max_score=3, feedback="Test.",
                evidence_status="maybe", evidence=None, source_location=None,
            )

    def test_not_found_evidence_requires_zero(self):
        result = QuestionResult(
            question="1", score=0, max_score=2, feedback="No response was visible.",
            evidence_status="not_found", evidence=None, source_location=None,
        )
        self.assertEqual(result.score, 0)
        with self.assertRaises(ValidationError):
            QuestionResult(
                question="1", score=1, max_score=2, feedback="No response was visible.",
                evidence_status="not_found", evidence=None, source_location=None,
            )

    def test_unclear_evidence_cannot_receive_full_credit(self):
        result = QuestionResult(
            question="1", score=0, max_score=2, feedback="The handwriting cannot be read reliably.",
            evidence_status="unclear", evidence="Partially illegible handwriting", source_location="Image 2",
        )
        self.assertEqual(result.evidence_status, "unclear")
        with self.assertRaises(ValidationError):
            QuestionResult(
                question="1", score=2, max_score=2, feedback="Test.", evidence_status="unclear",
                evidence="Partially illegible handwriting", source_location="Image 2",
            )

    def test_legacy_question_without_grounding_fields_remains_valid(self):
        result = self.result()
        self.assertIsNone(result.questions[0].evidence_status)
        self.assertIsNone(result.questions[0].source_location)

    def point(self, **overrides):
        data = {
            "criterion": "Explain that declarative programming specifies the desired result.",
            "status": "met", "awarded_marks": 1, "max_marks": 1,
            "rationale": "The required meaning is present.", "evidence_status": "found",
            "evidence": "It focuses on what you want the program to achieve.", "source_location": "Page 1",
        }
        return MarkingPointResult(**(data | overrides))

    def test_semantic_paraphrase_does_not_require_exact_wording(self):
        point = self.point()
        self.assertEqual(point.status, "met")
        self.assertNotIn("specifies the desired result", point.evidence)

    def test_vague_answer_and_wrong_exact_syntax_are_not_met(self):
        vague = self.point(
            status="not_met", awarded_marks=0, evidence="Declarative programming is easier.",
            rationale="The response does not express the required meaning.",
        )
        syntax = self.point(
            criterion="Write exactly busy(fred, tuesday, 1).", status="not_met", awarded_marks=0,
            evidence="busy(fred, monday, 1).", rationale="The required day token is incorrect.",
        )
        self.assertEqual((vague.awarded_marks, syntax.awarded_marks), (0, 0))

    def test_question_score_is_derived_from_four_marking_points(self):
        points = [self.point(criterion=f"Point {index}") for index in range(1, 4)]
        points.append(self.point(
            criterion="Point 4", status="not_met", awarded_marks=0,
            evidence_status="not_found", evidence=None, source_location=None,
            rationale="No supporting response was present.",
        ))
        question = QuestionResult.from_marking_points(
            question="1", max_score=4, feedback="Three of four required points were demonstrated.",
            marking_points=points,
        )
        self.assertEqual((question.score, question.max_score), (3, 4))

    def test_marking_point_status_and_evidence_rules(self):
        invalid = [
            {"status": "not_met", "awarded_marks": 1},
            {"status": "met", "awarded_marks": 2, "max_marks": 1},
            {"status": "met", "awarded_marks": 1, "evidence_status": "not_found",
             "evidence": None, "source_location": None},
            {"status": "unclear", "awarded_marks": 1, "max_marks": 1,
             "evidence_status": "unclear", "evidence": "Illegible text"},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.point(**overrides)
        unclear = self.point(
            status="unclear", awarded_marks=0, evidence_status="unclear",
            evidence="The final token is illegible.", rationale="The syntax cannot be read reliably.",
        )
        self.assertEqual(unclear.awarded_marks, 0)

    def test_question_marking_point_sums_must_match(self):
        point = self.point()
        for score, maximum in [(0, 1), (1, 2)]:
            with self.subTest(score=score, maximum=maximum), self.assertRaises(ValidationError):
                QuestionResult(
                    question="1", score=score, max_score=maximum, feedback="Fixture",
                    evidence_status="found", evidence=point.evidence, source_location=point.source_location,
                    marking_points=[point],
                )

    def test_invalid_assessment_totals(self):
        for overrides in [{"total_score": -1}, {"total_score": 5}, {"max_score": 0}, {"total_score": 2}, {"max_score": 5}]:
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.result(**overrides)

    def test_pdf_text_and_manual_criteria(self):
        work = ProcessedDocument("work.pdf", "application/pdf", "pdf", b"pdf fixture", text="Student answer")
        prepared = prepare_grading_input(work, criteria_text="  Award marks for reasoning.  ")
        self.assertEqual(prepared.student_work.kind, "text")
        self.assertEqual(prepared.student_work.text, "Student answer")
        self.assertIsNone(prepared.student_work.original_bytes)
        self.assertEqual(prepared.criteria_text, "Award marks for reasoning.")
        with self.assertRaises(ValueError):
            prepare_grading_input(work, criteria_text="   ")

    def test_visual_and_uploaded_scheme_inputs(self):
        work = ProcessedDocument("work.png", "image/png", "image", b"image fixture", requires_visual_processing=True)
        for visual in [False, True]:
            rendered = EmbeddedImage("scheme-page-2.jpg", "image/jpeg", b"page fixture", 100, 100)
            pdf_pages = (PDFPageContent(1, text="Partial or full criteria"),
                         PDFPageContent(2, rendered_image=rendered)) if visual else ()
            scheme = ProcessedDocument("scheme.pdf", "application/pdf", "pdf", b"pdf fixture",
                                       text="Partial or full criteria", requires_visual_processing=visual,
                                       pdf_pages=pdf_pages)
            prepared = prepare_grading_input(work, scheme, "Additional criteria")
            self.assertEqual(prepared.student_work.original_bytes, b"image fixture")
            self.assertEqual(prepared.student_work.kind, "image")
            self.assertEqual(prepared.mark_scheme.kind, "pdf_visual" if visual else "text")
            self.assertEqual(prepared.mark_scheme.text, scheme.text)
            if visual:
                self.assertIsNone(prepared.mark_scheme.original_bytes)
                self.assertEqual(prepared.mark_scheme.pdf_pages, pdf_pages)
        image_scheme = prepare_grading_input(work, work)
        self.assertEqual(image_scheme.mark_scheme.kind, "image")


if __name__ == "__main__":
    unittest.main()
