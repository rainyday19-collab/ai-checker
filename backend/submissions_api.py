from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

import database
from ai_grading import AIConfigurationError, AIGradingError
from assessment_service import GradingModeError, run_assessment
from document_processing import process_document
from grading import AssessmentResult, SavedAssessment, prepare_grading_input
from mark_scheme_storage import load_document


class StudentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    student_name: str = Field(min_length=1, max_length=150)


class SubmissionSummary(BaseModel):
    id: int
    assignment_id: int
    student_name: str
    original_filename: str
    status: Literal["pending", "graded", "failed"]
    assessment_id: int | None
    created_at: str
    graded_at: str | None
    total_score: float | None
    max_score: float | None
    percentage: float | None


class SubmissionDetail(SubmissionSummary):
    assessment: SavedAssessment | None


router = APIRouter(prefix="/api", tags=["Student submissions"])


@router.get("/assignments/{assignment_id}/submissions", response_model=list[SubmissionSummary])
def list_submissions(assignment_id: int):
    if database.get_assignment(assignment_id) is None:
        raise HTTPException(404, "Assignment not found.")
    return database.list_submissions(assignment_id)


@router.post("/assignments/{assignment_id}/submissions/grade", response_model=SubmissionDetail, status_code=201)
async def grade_submission(
    assignment_id: int,
    student_name: Annotated[str, Form()],
    student_work: Annotated[UploadFile, File()],
):
    try:
        assignment = await run_in_threadpool(database.get_assignment, assignment_id)
        if assignment is None:
            raise HTTPException(404, "Assignment not found.")
        criteria = (assignment["mark_scheme_text"] or "").strip()
        if not criteria and not assignment.get("mark_scheme_key"):
            raise HTTPException(422, "This assignment needs a saved mark scheme before grading student work.")
        name = student_name.strip()
        if not name or len(name) > 150:
            raise HTTPException(422, "Student name is required and must be at most 150 characters.")
        name = StudentInput(student_name=name).student_name
        document = await process_document(student_work, "Student work")
        scheme = await run_in_threadpool(load_document, assignment)
        prepared = prepare_grading_input(document, scheme, criteria or None)
        assessment = await run_assessment(prepared)
        # Validate again before either database row is written.
        result = AssessmentResult.model_validate(assessment.result.model_dump(exclude={"percentage"}))
        assessment = assessment.model_copy(update={"result": result})
        submission_id = await run_in_threadpool(database.save_submission, assignment_id, name, document.filename, assessment)
        if submission_id is None:
            raise HTTPException(409, "The assignment was deleted while grading. No result was saved.")
        return await run_in_threadpool(database.get_submission, submission_id)
    except (AIConfigurationError, GradingModeError) as error:
        raise HTTPException(503, str(error)) from error
    except AIGradingError as error:
        raise HTTPException(error.status_code, str(error)) from error
    except ValidationError as error:
        raise HTTPException(502, "The grader returned an invalid assessment. No result was saved.") from error
    finally:
        await student_work.close()


@router.get("/submissions/{submission_id}", response_model=SubmissionDetail)
def get_submission(submission_id: int):
    result = database.get_submission(submission_id)
    if result is None:
        raise HTTPException(404, "Submission not found.")
    return result


@router.delete("/submissions/{submission_id}")
def delete_submission(submission_id: int):
    if not database.delete_submission(submission_id):
        raise HTTPException(404, "Submission not found.")
    return {"status": "deleted", "id": submission_id}
