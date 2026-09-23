import csv
from io import StringIO
import json
import re
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

import database
from ai_grading import AIConfigurationError, AIGradingError, log_invalid_assessment
from assessment_service import GradingModeError, run_assessment
from document_processing import process_student_work_files
from grading import AssessmentResult, SavedAssessment, prepare_grading_input
from rubric_service import prepare_assignment_rubric


class StudentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    student_name: str = Field(min_length=1, max_length=150)


class SubmissionSummary(BaseModel):
    id: int
    assignment_id: int
    student_name: str
    original_filename: str
    original_filenames: list[str]
    page_count: int = Field(ge=1)
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


CSV_HEADERS = [
    "Student Name", "Score", "Maximum Score", "Percentage", "Assignment",
    "Class", "Student Work Filename", "Graded At",
]


def spreadsheet_safe(value: str | None) -> str:
    """Keep user text readable while preventing spreadsheet formula execution."""
    text = value or ""
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def csv_number(value: float) -> str:
    return format(value, ".15g")


def export_filename(title: str, assignment_id: int) -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", title.casefold()))[:80].strip("-")
    return f"{slug or f'assignment-{assignment_id}'}-results.csv"


@router.get("/assignments/{assignment_id}/export.csv")
def export_assignment_results(assignment_id: int):
    assignment = database.get_assignment(assignment_id)
    if assignment is None:
        raise HTTPException(404, "Assignment not found.")
    parent = database.get_class(assignment["class_id"])
    if parent is None:
        raise HTTPException(404, "Class not found.")

    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for submission in reversed(database.list_submissions(assignment_id)):
        if submission["status"] != "graded" or submission["assessment_id"] is None:
            continue
        try:
            saved = database.get_assessment(submission["assessment_id"])
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError, KeyError) as error:
            raise HTTPException(500, "A saved grading result is invalid and cannot be exported.") from error
        if saved is None:
            continue
        result = saved.result
        writer.writerow([
            spreadsheet_safe(submission["student_name"]),
            csv_number(result.total_score),
            csv_number(result.max_score),
            f"{csv_number(result.percentage)}%",
            spreadsheet_safe(assignment["title"]),
            spreadsheet_safe(parent["name"]),
            spreadsheet_safe(submission["original_filename"]),
            submission["graded_at"] or "",
        ])

    filename = export_filename(assignment["title"], assignment_id)
    return Response(
        content=output.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/assignments/{assignment_id}/submissions/grade", response_model=SubmissionDetail, status_code=201)
async def grade_submission(
    assignment_id: int,
    student_name: Annotated[str, Form()],
    student_work: Annotated[list[UploadFile] | None, File()] = None,
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
        rubric = await prepare_assignment_rubric(assignment_id)
        documents = await process_student_work_files(student_work)
        prepared = prepare_grading_input(documents, rubric=rubric)
        assessment = await run_assessment(prepared)
        # Validate again before either database row is written.
        result = AssessmentResult.model_validate(assessment.result.model_dump(exclude={"percentage"}))
        assessment = assessment.model_copy(update={"result": result})
        submission_id = await run_in_threadpool(
            database.save_submission, assignment_id, name, [document.filename for document in documents], assessment,
        )
        if submission_id is None:
            raise HTTPException(409, "The assignment was deleted while grading. No result was saved.")
        return await run_in_threadpool(database.get_submission, submission_id)
    except (AIConfigurationError, GradingModeError) as error:
        raise HTTPException(503, str(error)) from error
    except AIGradingError as error:
        log_invalid_assessment(error, "assignment")
        raise HTTPException(error.status_code, str(error)) from error
    except ValidationError as error:
        raise HTTPException(502, "The grader returned an invalid assessment. No result was saved.") from error
    finally:
        for upload in student_work or []:
            await upload.close()


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
