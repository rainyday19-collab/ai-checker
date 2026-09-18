import os
from pathlib import Path

from dotenv import load_dotenv

from grading import AssessmentResponse, AssessmentResult, GradingInput


class GradingModeError(RuntimeError):
    pass


async def mock_grade_assessment(prepared: GradingInput) -> AssessmentResult:
    """Fixed development data. Uploaded answers and criteria are not analyzed."""
    return AssessmentResult(
        total_score=17,
        max_score=20,
        summary="Example performance: a strong method with a few missing details. This fixed demo does not evaluate your uploaded work.",
        questions=[
            {"question": "Question 1 — Understanding the problem", "score": 4, "max_score": 5,
             "feedback": "Example: the key requirements are identified, but one constraint is missing.",
             "evidence": "Illustrative reasoning: identifies the inputs and outputs, but omits the input range."},
            {"question": "Question 2 — Method and reasoning", "score": 5, "max_score": 5,
             "feedback": "Example: an appropriate method is explained with complete working.",
             "evidence": "Illustrative reasoning: each calculation follows logically from the previous step."},
            {"question": "Question 3 — Applying the solution", "score": 3, "max_score": 4,
             "feedback": "Example: the calculation is correct, but its assumptions are not stated.",
             "evidence": "Illustrative reasoning: substitutes the correct values without explaining an assumption."},
            {"question": "Question 4 — Final answer", "score": 5, "max_score": 6,
             "feedback": "Example: the result is correct, but the final answer needs units.",
             "evidence": "Illustrative reasoning: gives the expected numerical value without a unit."},
        ],
    )


async def run_assessment(prepared: GradingInput) -> AssessmentResponse:
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    mode = os.getenv("GRADING_MODE", "mock").strip().lower()
    if mode == "mock":
        return AssessmentResponse(grading_mode="mock", result=await mock_grade_assessment(prepared))
    if mode == "openai":
        # Future connection point. No AI client is imported or called here.
        raise GradingModeError("OpenAI grading is not enabled. Use GRADING_MODE=mock.")
    raise GradingModeError("Invalid grading mode. Use GRADING_MODE=mock.")
