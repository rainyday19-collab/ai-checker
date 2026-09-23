import os
from pathlib import Path

from dotenv import load_dotenv
from ai_grading import grade_assessment, normalize_rubric

from grading import AssessmentResponse, AssessmentResult, GradingInput, PreparedContent, QuestionResult
from rubric import CanonicalRubric, demo_rubric


class GradingModeError(RuntimeError):
    pass


async def mock_grade_assessment(prepared: GradingInput) -> AssessmentResult:
    """Fixed development data. Uploaded answers and criteria are not analyzed."""
    if prepared.rubric is None:
        raise GradingModeError("Prepare a canonical rubric before grading student work.")
    questions = []
    for canonical_question in prepared.rubric.questions:
        points = [{
            "marking_point_id": point.id,
            "criterion": point.criterion,
            "criterion_type": point.criterion_type,
            "guidance": point.guidance,
            "status": "partially_met",
            "awarded_marks": point.max_marks * 0.85,
            "max_marks": point.max_marks,
            "rationale": "Fixed illustrative evaluation for development mode.",
            "evidence_status": "found",
            "evidence": "Illustrative demo evidence; uploaded work is not analyzed in mock mode.",
            "source_location": "Demo fixture",
        } for point in canonical_question.marking_points]
        questions.append(QuestionResult.from_marking_points(
            question_id=canonical_question.question_id,
            question=canonical_question.question_text,
            max_score=canonical_question.max_marks,
            feedback="Fixed illustrative marking-point feedback for development mode.",
            marking_points=points,
        ))
    return AssessmentResult(
        total_score=sum(question.score for question in questions),
        max_score=sum(question.max_score for question in questions),
        summary="Example performance: a strong method with a few missing details. This fixed demo does not evaluate your uploaded work.",
        questions=questions,
    )


def grading_mode() -> str:
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    mode = os.getenv("GRADING_MODE", "mock").strip().lower()
    if mode not in {"mock", "openai"}:
        raise GradingModeError("Invalid grading mode. Use GRADING_MODE=mock or openai.")
    return mode


async def run_rubric_normalization(mark_scheme: PreparedContent | None, criteria_text: str | None,
                                   total_marks: float | None = None) -> tuple[CanonicalRubric, str]:
    mode = grading_mode()
    if mode == "mock":
        return demo_rubric(total_marks), mode
    return await normalize_rubric(
        mark_scheme, criteria_text, total_marks, allow_api_request=True,
    ), mode


async def run_assessment(prepared: GradingInput) -> AssessmentResponse:
    mode = grading_mode()
    if mode == "mock":
        return AssessmentResponse(grading_mode="mock", result=await mock_grade_assessment(prepared))
    if mode == "openai":
        return await grade_assessment(prepared, allow_api_request=True)
    raise GradingModeError("Invalid grading mode. Use GRADING_MODE=mock or openai.")
