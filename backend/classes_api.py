from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

import database
from document_processing import process_document
from mark_scheme_storage import delete_file, save_document
from rubric import CanonicalRubric
from rubric_service import prepare_assignment_rubric


class ClassCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=150)
    subject: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=5000)


class ClassResponse(ClassCreate):
    id: int
    created_at: str
    assignment_count: int


class AssignmentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    total_marks: float | None = Field(default=None, gt=0)
    mark_scheme_text: str | None = Field(default=None, max_length=50000)


class MarkSchemeMetadata(BaseModel):
    source: str
    type: str | None
    original_filename: str | None
    has_text: bool
    has_file: bool


class AssignmentResponse(AssignmentCreate):
    id: int
    class_id: int
    created_at: str
    mark_scheme: MarkSchemeMetadata
    rubric: "RubricSummary"


class RubricSummary(BaseModel):
    status: str
    rubric_version: int | None
    total_marks: float | None
    question_count: int
    marking_point_count: int
    error: str | None


def public_assignment(values):
    has_text = bool((values["mark_scheme_text"] or "").strip())
    has_file = bool(values.get("mark_scheme_key"))
    # Explicit response fields keep storage keys and local paths private.
    rubric = None
    if values.get("rubric_status") == "ready" and values.get("rubric_json"):
        try:
            rubric = CanonicalRubric.model_validate_json(values["rubric_json"])
        except ValidationError:
            rubric = None
    rubric_status = values.get("rubric_status") or "unavailable"
    if rubric_status == "ready" and rubric is None:
        rubric_status = "failed"
    return {key: values[key] for key in (*AssignmentCreate.model_fields, "id", "class_id", "created_at")} | {
        "mark_scheme": {"source": "both" if has_file and has_text else "file" if has_file else "text" if has_text else "none",
                        "type": values.get("mark_scheme_type") if has_file else "text" if has_text else None,
                        "original_filename": values.get("mark_scheme_filename"), "has_text": has_text, "has_file": has_file},
        "rubric": {
            "status": rubric_status,
            "rubric_version": rubric.rubric_version if rubric else None,
            "total_marks": rubric.total_marks if rubric else None,
            "question_count": len(rubric.questions) if rubric else 0,
            "marking_point_count": rubric.marking_point_count if rubric else 0,
            "error": values.get("rubric_error") if rubric_status == "failed" else None,
        },
    }


router = APIRouter(prefix="/api", tags=["Classes and assignments"])


@router.get("/classes", response_model=list[ClassResponse])
def list_classes():
    return database.list_classes()


@router.post("/classes", response_model=ClassResponse, status_code=201)
def create_class(values: ClassCreate):
    return database.create_class(values.model_dump())


@router.get("/classes/{class_id}", response_model=ClassResponse)
def get_class(class_id: int):
    result = database.get_class(class_id)
    if result is None:
        raise HTTPException(404, "Class not found.")
    return result


@router.delete("/classes/{class_id}")
def delete_class(class_id: int):
    if not database.delete_class(class_id):
        raise HTTPException(404, "Class not found.")
    return {"status": "deleted", "id": class_id}


@router.get("/classes/{class_id}/assignments", response_model=list[AssignmentResponse])
def list_assignments(class_id: int):
    get_class(class_id)
    return [public_assignment(values) for values in database.list_assignments(class_id)]


@router.post("/classes/{class_id}/assignments", response_model=AssignmentResponse, status_code=201)
async def create_assignment(class_id: int, request: Request):
    form = None
    stored = None
    document = None
    saved = False
    try:
        if await run_in_threadpool(database.get_class, class_id) is None:
            raise HTTPException(404, "Class not found.")
        if request.headers.get("content-type", "").startswith("application/json"):
            # Retain compatibility with earlier text-only API clients.
            values = AssignmentCreate.model_validate(await request.json())
            upload = None
        else:
            form = await request.form(max_files=1, max_fields=6)
            data = dict(form)
            upload = data.pop("mark_scheme_file", None)
            if upload is not None and not isinstance(upload, UploadFile):
                raise HTTPException(422, "Mark scheme file must be an uploaded PDF, DOCX, or image.")
            values = AssignmentCreate.model_validate(data)
        if not (values.mark_scheme_text or "").strip() and upload is None:
            raise HTTPException(422, "Upload a mark scheme file or paste non-empty grading criteria.")
        fields = values.model_dump()
        if upload:
            document = await process_document(upload, "Mark scheme")
            stored = await run_in_threadpool(save_document, document)
            fields.update(stored)
        result = await run_in_threadpool(database.create_assignment, class_id, fields)
        if result is None:
            raise HTTPException(404, "Class not found.")
        saved = True
        try:
            await prepare_assignment_rubric(result["id"], mark_scheme_document=document)
        except HTTPException:
            # The assignment and mark scheme remain valid. The response exposes the
            # failed state and the teacher can explicitly retry without an auto-loop.
            pass
        return public_assignment(await run_in_threadpool(database.get_assignment, result["id"]))
    except (ValidationError, ValueError) as error:
        raise HTTPException(422, "Provide a valid assignment title, criteria and optional description or legacy total marks.") from error
    finally:
        if form is not None:
            await form.close()
        if stored and not saved:
            await run_in_threadpool(delete_file, stored["mark_scheme_key"])


@router.get("/assignments/{assignment_id}", response_model=AssignmentResponse)
def get_assignment(assignment_id: int):
    result = database.get_assignment(assignment_id)
    if result is None:
        raise HTTPException(404, "Assignment not found.")
    return public_assignment(result)


@router.get("/assignments/{assignment_id}/rubric", response_model=CanonicalRubric)
def get_assignment_rubric(assignment_id: int):
    assignment = database.get_assignment(assignment_id)
    if assignment is None:
        raise HTTPException(404, "Assignment not found.")
    rubric = database.get_assignment_rubric(assignment_id)
    if rubric is None:
        detail = "Rubric preparation failed. Retry it manually before grading." \
            if assignment.get("rubric_status") == "failed" else "The grading rubric is not ready."
        raise HTTPException(409, detail)
    return rubric


@router.post("/assignments/{assignment_id}/rubric/retry", response_model=CanonicalRubric)
async def retry_assignment_rubric(assignment_id: int):
    return await prepare_assignment_rubric(assignment_id, retry_failed=True)


@router.delete("/assignments/{assignment_id}")
def delete_assignment(assignment_id: int):
    if not database.delete_assignment(assignment_id):
        raise HTTPException(404, "Assignment not found.")
    return {"status": "deleted", "id": assignment_id}
