import unittest

from pydantic import ValidationError

from document_processing import ProcessedDocument
from grading import AssessmentResult, QuestionResult, prepare_grading_input


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
            scheme = ProcessedDocument("scheme.pdf", "application/pdf", "pdf", b"pdf fixture", text="Partial or full criteria", requires_visual_processing=visual)
            prepared = prepare_grading_input(work, scheme, "Additional criteria")
            self.assertEqual(prepared.student_work.original_bytes, b"image fixture")
            self.assertEqual(prepared.student_work.kind, "image")
            self.assertEqual(prepared.mark_scheme.kind, "pdf_visual" if visual else "text")
            self.assertEqual(prepared.mark_scheme.text, scheme.text)
            if visual:
                self.assertEqual(prepared.mark_scheme.original_bytes, b"pdf fixture")
        image_scheme = prepare_grading_input(work, work)
        self.assertEqual(image_scheme.mark_scheme.kind, "image")


if __name__ == "__main__":
    unittest.main()
