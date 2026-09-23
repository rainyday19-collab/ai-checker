from fastapi import HTTPException
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

import database
from ai_grading import AIConfigurationError, AIGradingError, log_invalid_assessment
from assessment_service import GradingModeError, run_rubric_normalization
from grading import prepare_document
from mark_scheme_storage import load_document
from rubric import CanonicalRubric


RUBRIC_FAILURE_MESSAGE = "Rubric preparation failed. Retry it manually before grading."


async def prepare_assignment_rubric(assignment_id: int, *, retry_failed: bool = False) -> CanonicalRubric:
    assignment = await run_in_threadpool(database.get_assignment, assignment_id)
    if assignment is None:
        raise HTTPException(404, "Assignment not found.")
    claim = await run_in_threadpool(
        lambda: database.claim_rubric_generation(assignment_id, retry_failed=retry_failed)
    )
    if claim == "ready":
        rubric = await run_in_threadpool(database.get_assignment_rubric, assignment_id)
        if rubric is None:
            raise HTTPException(503, "The saved grading rubric is invalid or unavailable.")
        return rubric
    if claim == "failed":
        raise HTTPException(409, RUBRIC_FAILURE_MESSAGE)
    if claim == "preparing":
        raise HTTPException(409, "Rubric preparation is already in progress.")
    if claim == "missing":
        raise HTTPException(404, "Assignment not found.")
    if claim != "claimed":
        raise HTTPException(409, "The grading rubric is not ready.")

    try:
        scheme_document = await run_in_threadpool(load_document, assignment)
        mark_scheme = prepare_document(scheme_document) if scheme_document else None
        criteria = (assignment.get("mark_scheme_text") or "").strip() or None
        if mark_scheme is None and criteria is None:
            raise HTTPException(422, "This assignment has no mark scheme to normalize.")
        rubric, mode = await run_rubric_normalization(mark_scheme, criteria, assignment.get("total_marks"))
        saved = await run_in_threadpool(database.save_assignment_rubric, assignment_id, rubric, mode)
        if not saved:
            raise HTTPException(409, "The assignment changed while its rubric was being prepared.")
        return rubric
    except (AIConfigurationError, GradingModeError) as error:
        await run_in_threadpool(database.fail_assignment_rubric, assignment_id, RUBRIC_FAILURE_MESSAGE)
        raise HTTPException(503, str(error)) from error
    except AIGradingError as error:
        log_invalid_assessment(error, "rubric_normalization")
        await run_in_threadpool(database.fail_assignment_rubric, assignment_id, RUBRIC_FAILURE_MESSAGE)
        raise HTTPException(error.status_code, str(error)) from error
    except (ValidationError, ValueError) as error:
        await run_in_threadpool(database.fail_assignment_rubric, assignment_id, RUBRIC_FAILURE_MESSAGE)
        raise HTTPException(502, RUBRIC_FAILURE_MESSAGE) from error
    except HTTPException:
        await run_in_threadpool(database.fail_assignment_rubric, assignment_id, RUBRIC_FAILURE_MESSAGE)
        raise
