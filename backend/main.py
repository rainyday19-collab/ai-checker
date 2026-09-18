from contextlib import asynccontextmanager
import sqlite3
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import database
from ai_grading import AIConfigurationError, AIGradingError
from document_processing import process_document
from assessment_service import GradingModeError, run_assessment
from grading import AssessmentHistoryItem, AssessmentResponse, AssessmentStatistics, SavedAssessment, prepare_grading_input


@asynccontextmanager
async def lifespan(app):
    database.initialize_database()
    yield


app = FastAPI(title="AI Checker", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(sqlite3.Error)
async def database_error(request, error):
    return JSONResponse(status_code=503, content={"detail": "Assessment storage is unavailable. Please try again."})

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/assess", response_model=AssessmentResponse)
async def assess(
    student_work: Annotated[UploadFile, File()],
    mark_scheme: Annotated[UploadFile | None, File()] = None,
    criteria_text: Annotated[str | None, Form()] = None,
):
    try:
        has_criteria = bool(criteria_text and criteria_text.strip())
        if mark_scheme is None and not has_criteria:
            raise HTTPException(
                status_code=422,
                detail="Provide a mark scheme file or non-empty grading criteria.",
            )
        student_document = await process_document(student_work, "Student work")
        scheme_document = await process_document(mark_scheme, "Mark scheme") if mark_scheme else None
        grading_input = prepare_grading_input(student_document, scheme_document, criteria_text)
        assessment = await run_assessment(grading_input)
        assessment_id = await run_in_threadpool(
            database.save_assessment, assessment, student_document.filename,
            scheme_document.filename if scheme_document else None, grading_input.criteria_text is not None,
        )
        return assessment.model_copy(update={"id": assessment_id})
    except GradingModeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AIConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AIGradingError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    finally:
        await student_work.close()
        if mark_scheme:
            await mark_scheme.close()


@app.get("/api/assessments", response_model=list[AssessmentHistoryItem])
def assessment_history():
    return database.list_assessments()


@app.get("/api/statistics", response_model=AssessmentStatistics)
def assessment_statistics():
    return database.get_statistics()


@app.get("/api/assessments/{assessment_id}", response_model=SavedAssessment)
def saved_assessment(assessment_id: int):
    assessment = database.get_assessment(assessment_id)
    if assessment is None:
        raise HTTPException(404, "Assessment not found.")
    return assessment


@app.delete("/api/assessments/{assessment_id}")
def remove_assessment(assessment_id: int):
    if not database.delete_assessment(assessment_id):
        raise HTTPException(404, "Assessment not found.")
    return {"status": "deleted", "id": assessment_id}
