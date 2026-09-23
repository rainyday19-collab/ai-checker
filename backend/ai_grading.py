import base64
import logging
from math import isfinite
import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from openai import (AsyncOpenAI, AuthenticationError, RateLimitError, APITimeoutError,
                    APIConnectionError, OpenAIError)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from openai.types.responses import ResponseInputContentParam, ResponseInputParam

from grading import (AIUsage, AssessmentResponse, AssessmentResult, GradingInput,
                     PreparedContent, QuestionResult)
from rubric import CanonicalRubric, CanonicalRubricDraft


logger = logging.getLogger(__name__)
OUTPUT_TOKEN_LIMIT = 6000
DEFAULT_OPENAI_TIMEOUT_SECONDS = 120.0
MAX_OPENAI_TIMEOUT_SECONDS = 600.0

SYSTEM_INSTRUCTION = (
    "Grade only against the supplied canonical rubric, which is authoritative. Return every canonical question_id and every "
    "marking_point_id exactly once. Do not add, remove, rename, merge, split, reorder conceptually, or change any rubric "
    "requirement or maximum. Award marks only for identifiable marking points, never for a generally knowledgeable, close, implied, or "
    "unrelated answer. Apply the same standard every run. Grade for demonstrated understanding, not wording similarity, and "
    "do not look for reasons to deduct marks from an otherwise correct answer. Use the rubric to define the required knowledge, "
    "not to demand verbatim reproduction of a reference answer. For conceptual criteria, accept semantically equivalent wording and valid paraphrases, "
    "including synonyms. Where multiple correct formulations or examples are possible, accept any technically valid "
    "equivalent; an example shown in the rubric is not automatically the only acceptable answer. For an open-ended request for a "
    "valid example, assess whether the student's example satisfies the requested concept and syntax, not whether it matches the "
    "reference example. For example, if likes(mary, food). is shown as a Prolog fact, male(ali). is also a valid Prolog fact and "
    "must be accepted. Do not require exact words unless the question or scheme explicitly requires a technical term, "
    "formula, syntax, token, keyword, command, identifier, result, or notation. For exact technical criteria, require the "
    "specified technical correctness; invalid syntax remains incorrect, but exactness does not make one valid example exclusive "
    "unless the question explicitly requires that exact example, token, identifier, or notation. Withhold marks only when required "
    "knowledge is absent, incorrect, contradictory, irrelevant, or technically wrong where exactness matters. For each marking "
    "point, compare the required meaning with actual student evidence. "
    "Return awarded_marks and evidence_status for each point; the backend derives status deterministically, so do not return "
    "a status field. Use partial marks only when the scheme or clearly divisible semantics justify them. Never guess, infer, "
    "or reconstruct hidden content. evidence_status='not_found' or 'unclear' receives exactly zero marks; any positive marks "
    "require evidence_status='found'. Evidence must briefly transcribe or "
    "describe the student's actual response and cite the supplied page/image/document label, never copy an ideal answer from "
    "the scheme. Respect the scheme's scoring increments and do not invent precision. Write point rationales and question "
    "feedback from these decisions, then write an overall summary consistent with them. Student identity must not influence "
    "grading. Question scores, aggregate totals, and percentage are derived by the backend."
)

RUBRIC_INSTRUCTION = (
    "Normalize only the supplied mark scheme into a canonical rubric; do not grade student work. Preserve every explicit "
    "question, requirement, and mark allocation. Distinguish REQUIRED MEANING—the knowledge or skill the student must "
    "demonstrate—from ACCEPTABLE EXAMPLES or reference answers that merely illustrate a correct response. Do not turn every "
    "phrase or example in a reference answer into a mandatory marking point. Treat examples as non-exclusive unless the question "
    "or scheme says must, requires, specifically, or exactly, or otherwise makes a particular technical value mandatory. For an "
    "open-ended example question, make the criterion require any example satisfying the requested concept and syntax; put a shown "
    "reference example in guidance as non-exclusive. Thus a request for a valid Prolog fact may use likes(mary, food). as guidance, "
    "but the criterion must also permit another syntactically valid fact such as male(ali). Do not invent or remove available marks. If one broad criterion is worth "
    "multiple marks, keep it as one point unless the scheme clearly defines separable points. Classify conceptual explanation, "
    "description, comparison, reason, or concept criteria as semantic. Classify required syntax, formula, command, token, "
    "identifier, numeric result, or notation as exact. Semantic criteria accept valid paraphrases; exact criteria require "
    "technical correctness. Each question's marking-point maxima must equal its maximum, and question maxima must equal the "
    "rubric total. Return only the structured rubric. IDs are assigned deterministically by the backend."
)


class AIConfigurationError(RuntimeError):
    pass


class AIGradingError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, *, category: str | None = None,
                 fields: tuple[str, ...] = (), response_status: str | None = None,
                 incomplete_reason: str | None = None, output_tokens: int | None = None,
                 parsed_exists: bool | None = None,
                 semantic_details: dict[str, str | int | float | None] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.fields = fields
        self.response_status = response_status
        self.incomplete_reason = incomplete_reason
        self.output_tokens = output_tokens
        self.parsed_exists = parsed_exists
        self.semantic_details = semantic_details or {}


class RubricAlignmentError(ValueError):
    def __init__(self, message: str, fields: tuple[str, ...] = ("questions",), *,
                 details: dict[str, str | int | float | None] | None = None):
        super().__init__(message)
        self.fields = fields
        self.details = details or {}


class MarkingPointEvaluationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    marking_point_id: str = Field(pattern=r"^q[1-9][0-9]*_p[1-9][0-9]*$")
    awarded_marks: float = Field(ge=0)
    rationale: str = Field(min_length=1)
    evidence_status: Literal["found", "not_found", "unclear"]
    evidence: str | None
    source_location: str | None


class QuestionEvaluationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    question_id: str = Field(pattern=r"^q[1-9][0-9]*$")
    marking_points: list[MarkingPointEvaluationDraft] = Field(min_length=1)
    feedback: str = Field(min_length=1)


class AssessmentDraft(BaseModel):
    """AI-authored fields only; aggregate scores are authoritative derived data."""
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    questions: list[QuestionEvaluationDraft] = Field(min_length=1)

    @staticmethod
    def _status(awarded_marks: float, max_marks: float, evidence_status: str) -> str:
        if evidence_status == "unclear":
            return "unclear"
        if evidence_status == "not_found":
            return "not_met"
        if awarded_marks == 0:
            return "not_met"
        if awarded_marks == max_marks:
            return "met"
        return "partially_met"

    @classmethod
    def _point_result(cls, *, question_index: int, question_id: str, point_index: int,
                      canonical_point, evaluation: MarkingPointEvaluationDraft) -> dict[str, Any]:
        field = f"questions.{question_index}.marking_points.{point_index}"
        details: dict[str, str | int | float | None] = {
            "question_id": question_id,
            "marking_point_id": canonical_point.id,
            "status": cls._status(
                evaluation.awarded_marks, canonical_point.max_marks, evaluation.evidence_status,
            ),
            "evidence_status": evaluation.evidence_status,
            "awarded_marks": evaluation.awarded_marks,
            "max_marks": canonical_point.max_marks,
        }

        def reject(rule: str, message: str):
            raise RubricAlignmentError(message, (field,), details={**details, "rule": rule})

        if evaluation.awarded_marks > canonical_point.max_marks:
            reject("awarded_marks_exceeds_max", "Awarded marking-point marks exceed the canonical maximum.")
        if evaluation.evidence_status == "not_found" and evaluation.awarded_marks != 0:
            reject("not_found_requires_zero", "A marking point with no evidence found must receive zero marks.")
        if evaluation.evidence_status == "unclear" and evaluation.awarded_marks != 0:
            reject("positive_marks_require_found_evidence", "Awarded marking-point marks require found evidence.")
        if evaluation.evidence_status in {"found", "unclear"} and not (evaluation.evidence or "").strip():
            reject("evidence_text_required", "Found or unclear marking-point evidence must be described.")
        if evaluation.evidence_status in {"found", "unclear"} and not (evaluation.source_location or "").strip():
            reject("source_location_required", "Found or unclear evidence requires a source location.")
        return {
            "marking_point_id": canonical_point.id,
            "criterion": canonical_point.criterion,
            "criterion_type": canonical_point.criterion_type,
            "guidance": canonical_point.guidance,
            "max_marks": canonical_point.max_marks,
            "status": details["status"],
            **evaluation.model_dump(exclude={"marking_point_id"}),
        }

    def to_result(self, rubric: CanonicalRubric) -> AssessmentResult:
        question_ids = [question.question_id for question in self.questions]
        expected_question_ids = [question.question_id for question in rubric.questions]
        if len(question_ids) != len(set(question_ids)):
            duplicate = next(question_id for question_id in question_ids if question_ids.count(question_id) > 1)
            raise RubricAlignmentError(
                "The grading result contains duplicate question IDs.",
                details={"question_id": duplicate, "rule": "duplicate_question_id"},
            )
        if set(question_ids) != set(expected_question_ids):
            unknown = next((question_id for question_id in question_ids if question_id not in expected_question_ids), None)
            missing = next((question_id for question_id in expected_question_ids if question_id not in question_ids), None)
            raise RubricAlignmentError(
                "The grading result must contain every canonical question exactly once.",
                details={"question_id": unknown or missing,
                         "rule": "unknown_question_id" if unknown else "missing_question_id"},
            )
        evaluations = {question.question_id: question for question in self.questions}
        results = []
        for question_index, canonical_question in enumerate(rubric.questions):
            evaluation = evaluations[canonical_question.question_id]
            point_ids = [point.marking_point_id for point in evaluation.marking_points]
            expected_point_ids = [point.id for point in canonical_question.marking_points]
            if len(point_ids) != len(set(point_ids)):
                duplicate = next(point_id for point_id in point_ids if point_ids.count(point_id) > 1)
                raise RubricAlignmentError(
                    "The grading result contains duplicate marking-point IDs.",
                    (f"questions.{question_index}.marking_points",),
                    details={"question_id": canonical_question.question_id,
                             "marking_point_id": duplicate, "rule": "duplicate_marking_point_id",
                             "expected_point_count": len(expected_point_ids),
                             "received_point_count": len(point_ids)},
                )
            if set(point_ids) != set(expected_point_ids):
                unknown = next((point_id for point_id in point_ids if point_id not in expected_point_ids), None)
                missing = next((point_id for point_id in expected_point_ids if point_id not in point_ids), None)
                raise RubricAlignmentError(
                    "The grading result must contain every canonical marking point exactly once.",
                    (f"questions.{question_index}.marking_points",),
                    details={"question_id": canonical_question.question_id,
                             "marking_point_id": unknown or missing,
                             "rule": "unknown_marking_point_id" if unknown else "missing_marking_point_id",
                             "expected_point_count": len(expected_point_ids),
                             "received_point_count": len(point_ids)},
                )
            point_evaluations = {point.marking_point_id: point for point in evaluation.marking_points}
            points = []
            for point_index, canonical_point in enumerate(canonical_question.marking_points):
                point = point_evaluations[canonical_point.id]
                points.append(self._point_result(
                    question_index=question_index, question_id=canonical_question.question_id,
                    point_index=point_index, canonical_point=canonical_point, evaluation=point,
                ))
            try:
                results.append(QuestionResult.from_marking_points(
                    question_id=canonical_question.question_id,
                    question=canonical_question.question_text,
                    max_score=canonical_question.max_marks,
                    feedback=evaluation.feedback,
                    marking_points=points,
                ))
            except ValidationError as error:
                entries = error.errors(include_url=False)
                for entry in entries:
                    entry["loc"] = ("questions", question_index, *entry.get("loc", ()))
                raise ValidationError.from_exception_data("AssessmentDraft", entries) from error
        return AssessmentResult(
            total_score=sum(question.score for question in results),
            max_score=sum(question.max_score for question in results),
            summary=self.summary,
            questions=results,
        )


MODEL_TEXT_FORMAT = {
    "type": "json_schema",
    "name": "assessment_draft",
    "strict": True,
    "schema": AssessmentDraft.model_json_schema(),
}

RUBRIC_TEXT_FORMAT = {
    "type": "json_schema",
    "name": "canonical_rubric_draft",
    "strict": True,
    "schema": CanonicalRubricDraft.model_json_schema(),
}


def validation_diagnostics(error: ValidationError) -> tuple[str, tuple[str, ...]]:
    """Return schema locations only; never include values or uploaded content."""
    entries = error.errors(include_url=False, include_context=False, include_input=False)
    categories = sorted({str(entry.get("type", "validation_error")) for entry in entries})
    fields = tuple(sorted({".".join(str(part) for part in entry.get("loc", ())) or "result"
                           for entry in entries}))
    return ",".join(categories) or "validation_error", fields


def log_invalid_assessment(error: AIGradingError, grading_path: str):
    if error.category:
        base = (
            "Grading failure path=%s category=%s fields=%s response_status=%s "
            "incomplete_reason=%s parsed_exists=%s output_tokens=%s output_token_limit=%s"
        )
        values = (
            grading_path, error.category, ",".join(error.fields) or "result",
            error.response_status or "unknown", error.incomplete_reason or "none",
            error.parsed_exists if error.parsed_exists is not None else "unknown",
            error.output_tokens if error.output_tokens is not None else "unknown", OUTPUT_TOKEN_LIMIT,
        )
        if error.category != "semantic_result_invalid":
            logger.warning(base, *values)
            return
        details = error.semantic_details
        logger.warning(
            base + " question_id=%s marking_point_id=%s rule=%s status=%s evidence_status=%s "
            "awarded_marks=%s max_marks=%s expected_point_count=%s received_point_count=%s",
            *values, details.get("question_id") or "none", details.get("marking_point_id") or "none",
            details.get("rule") or "none", details.get("status") or "none",
            details.get("evidence_status") or "none",
            details.get("awarded_marks") if details.get("awarded_marks") is not None else "none",
            details.get("max_marks") if details.get("max_marks") is not None else "none",
            details.get("expected_point_count")
            if details.get("expected_point_count") is not None else "none",
            details.get("received_point_count")
            if details.get("received_point_count") is not None else "none",
        )


def response_metadata(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    incomplete = getattr(response, "incomplete_details", None)
    return {
        "response_status": getattr(response, "status", None),
        "incomplete_reason": getattr(incomplete, "reason", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "parsed_exists": False,
    }


def output_content(response: Any) -> tuple[list[str], bool]:
    texts: list[str] = []
    refused = False
    for output in getattr(response, "output", ()) or ():
        if getattr(output, "type", None) != "message":
            continue
        for content in getattr(output, "content", ()) or ():
            if getattr(content, "type", None) == "refusal":
                refused = True
            elif getattr(content, "type", None) == "output_text" and isinstance(getattr(content, "text", None), str):
                texts.append(content.text)
    return texts, refused


def get_timeout_seconds() -> float:
    configured = os.getenv("OPENAI_TIMEOUT_SECONDS", "").strip()
    if not configured:
        return DEFAULT_OPENAI_TIMEOUT_SECONDS
    try:
        timeout = float(configured)
    except ValueError as error:
        raise AIConfigurationError(
            f"OPENAI_TIMEOUT_SECONDS must be greater than 0 and at most {MAX_OPENAI_TIMEOUT_SECONDS:g}."
        ) from error
    if not isfinite(timeout) or timeout <= 0 or timeout > MAX_OPENAI_TIMEOUT_SECONDS:
        raise AIConfigurationError(
            f"OPENAI_TIMEOUT_SECONDS must be greater than 0 and at most {MAX_OPENAI_TIMEOUT_SECONDS:g}."
        )
    return timeout


def get_configuration() -> tuple[str, str, float]:
    # Load only the backend .env, without overriding environment variables.
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == "your_api_key_here":
        raise AIConfigurationError("Set OPENAI_API_KEY in the backend environment before enabling AI grading.")
    model = os.getenv("OPENAI_MODEL", "").strip()
    if not model:
        raise AIConfigurationError("OPENAI_MODEL must not be empty.")
    return api_key, model, get_timeout_seconds()


def content_parts(document: PreparedContent, label: str) -> list[ResponseInputContentParam]:
    parts: list[ResponseInputContentParam] = [{"type": "input_text", "text": label}]
    if document.kind in {"text", "docx"}:
        if not document.text.strip():
            if not document.embedded_images:
                raise ValueError("Text inputs require document content.")
        else:
            parts.append({"type": "input_text", "text": document.text})
        for index, embedded in enumerate(document.embedded_images, start=1):
            encoded = base64.b64encode(embedded.original_bytes).decode("ascii")
            parts.append({"type": "input_text", "text": (
                f"{label} — DOCX EMBEDDED IMAGE {index} OF {len(document.embedded_images)} — {embedded.filename}"
            )})
            parts.append({"type": "input_image", "image_url": f"data:{embedded.content_type};base64,{encoded}", "detail": "auto"})
    else:
        if not document.original_bytes:
            raise ValueError("Visual inputs require original file bytes.")
        encoded = base64.b64encode(document.original_bytes).decode("ascii")
        data_url = f"data:{document.content_type};base64,{encoded}"
        if document.kind == "image":
            parts.append({"type": "input_image", "image_url": data_url, "detail": "auto"})
        elif document.kind == "pdf_visual":
            # Send the original PDF directly; no OCR, rendering, or separate file upload.
            parts.append({"type": "input_file", "filename": document.filename, "file_data": data_url})
        else:
            raise ValueError("Unsupported prepared content kind.")
    return parts


def build_model_input(prepared: GradingInput) -> ResponseInputParam:
    if prepared.rubric is None:
        raise ValueError("A canonical rubric is required for grading.")
    pages = prepared.ordered_student_work
    if len(pages) == 1:
        page = pages[0]
        if page.kind == "image":
            label = f"STUDENT WORK — IMAGE 1 — {page.filename}"
        elif page.kind == "docx":
            label = f"STUDENT WORK — DOCUMENT — {page.filename}"
        else:
            label = f"STUDENT WORK — {page.filename}"
        parts = content_parts(page, label)
    else:
        parts = [{"type": "input_text", "text": (
            f"STUDENT WORK — {len(pages)} ordered image pages. Grade all pages together as one submission."
        )}]
        for index, page in enumerate(pages, start=1):
            parts.extend(content_parts(page, f"STUDENT WORK — PAGE {index} OF {len(pages)} — {page.filename}"))
    parts.append({"type": "input_text", "text": (
        "CANONICAL RUBRIC — evaluate these IDs exactly as provided; do not rebuild the rubric.\n"
        f"{prepared.rubric.model_dump_json()}"
    )})
    return [{"role": "user", "content": parts}]


def build_rubric_input(mark_scheme: PreparedContent | None, criteria_text: str | None,
                       expected_total_marks: float | None = None) -> ResponseInputParam:
    criteria = (criteria_text or "").strip()
    if mark_scheme is None and not criteria:
        raise ValueError("A mark scheme or manual criteria is required for rubric normalization.")
    parts: list[ResponseInputContentParam] = []
    if mark_scheme:
        parts.extend(content_parts(mark_scheme, "MARK SCHEME TO NORMALIZE"))
    if criteria:
        parts.append({"type": "input_text", "text": f"ADDITIONAL MARK-SCHEME CRITERIA\n{criteria}"})
    if expected_total_marks is not None:
        parts.append({"type": "input_text", "text": f"DECLARED ASSIGNMENT TOTAL: {expected_total_marks:g} marks"})
    return [{"role": "user", "content": parts}]


async def normalize_rubric(mark_scheme: PreparedContent | None, criteria_text: str | None,
                           expected_total_marks: float | None = None, *,
                           allow_api_request: bool = False) -> CanonicalRubric:
    """One rubric-only structured request. Student work is never accepted here."""
    if not allow_api_request:
        raise AIConfigurationError("AI requests are disabled. Explicit opt-in is required to normalize a rubric.")
    api_key, model, timeout_seconds = get_configuration()
    model_input = build_rubric_input(mark_scheme, criteria_text, expected_total_marks)
    try:
        async with AsyncOpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0) as client:
            response = await client.responses.create(
                model=model,
                instructions=RUBRIC_INSTRUCTION,
                input=model_input,
                text={"format": RUBRIC_TEXT_FORMAT},
                max_output_tokens=OUTPUT_TOKEN_LIMIT,
                store=False,
            )
        metadata = response_metadata(response)
        if response.status != "completed":
            category = "rubric_output_truncated" if metadata["incomplete_reason"] == "max_output_tokens" \
                else "rubric_response_incomplete"
            raise AIGradingError("AI did not return a complete canonical rubric.", category=category, **metadata)
        texts, refused = output_content(response)
        if refused:
            raise AIGradingError("AI did not return a canonical rubric.", category="rubric_model_refusal", **metadata)
        if len(texts) != 1:
            category = "rubric_output_missing" if not texts else "rubric_output_ambiguous"
            raise AIGradingError("AI did not return a canonical rubric.", category=category, **metadata)
        try:
            draft = CanonicalRubricDraft.model_validate_json(texts[0])
        except ValidationError as error:
            validation_types = {entry.get("type") for entry in error.errors(include_url=False)}
            _, fields = validation_diagnostics(error)
            raise AIGradingError(
                "AI returned an invalid canonical rubric.",
                category="rubric_json_invalid" if "json_invalid" in validation_types else "rubric_schema_invalid",
                fields=fields, **metadata,
            ) from error
        try:
            return draft.to_canonical(expected_total_marks)
        except (ValidationError, ValueError) as error:
            fields = validation_diagnostics(error)[1] if isinstance(error, ValidationError) else ("rubric",)
            raise AIGradingError(
                "AI returned an inconsistent canonical rubric.", category="rubric_semantic_invalid",
                fields=fields, parsed_exists=True,
                **{key: value for key, value in metadata.items() if key != "parsed_exists"},
            ) from error
    except AuthenticationError as error:
        raise AIGradingError("OpenAI authentication failed while preparing the rubric.", 503,
                             category="rubric_authentication_failure") from error
    except RateLimitError as error:
        status = 402 if error.code == "insufficient_quota" else 429
        category = "rubric_insufficient_quota" if status == 402 else "rubric_rate_limit"
        raise AIGradingError("OpenAI could not prepare the rubric because its quota or rate limit was reached.", status,
                             category=category) from error
    except APITimeoutError as error:
        raise AIGradingError("Rubric preparation timed out. No rubric was saved.", 504,
                             category="rubric_request_timeout") from error
    except APIConnectionError as error:
        raise AIGradingError("Could not connect to OpenAI while preparing the rubric. No rubric was saved.",
                             category="rubric_connection_failure") from error
    except AIGradingError:
        raise
    except OpenAIError as error:
        raise AIGradingError("OpenAI could not prepare the rubric.", category="rubric_api_failure") from error


async def grade_assessment(prepared: GradingInput, *, allow_api_request: bool = False) -> AssessmentResponse:
    """One structured request, with retries disabled to avoid duplicate charges."""
    if not allow_api_request:
        raise AIConfigurationError("AI requests are disabled. Explicit opt-in is required to enable grading.")
    api_key, model, timeout_seconds = get_configuration()
    if prepared.rubric is None:
        raise AIConfigurationError("Prepare a canonical rubric before grading student work.")
    documents = [*prepared.ordered_student_work]
    if any(document and document.kind == "pdf_visual" for document in documents):
        raise AIGradingError("Scanned or low-text PDFs are not supported for AI grading yet. Upload readable PNG/JPG pages or a PDF with selectable text.", 422)
    model_input = build_model_input(prepared)
    try:
        async with AsyncOpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0) as client:
            response = await client.responses.create(
                model=model,
                instructions=SYSTEM_INSTRUCTION,
                input=model_input,
                text={"format": MODEL_TEXT_FORMAT},
                max_output_tokens=OUTPUT_TOKEN_LIMIT,
                store=False,
            )
        metadata = response_metadata(response)
        if response.status != "completed":
            category = "output_truncated" if metadata["incomplete_reason"] == "max_output_tokens" else "response_incomplete"
            raise AIGradingError("AI did not return a complete assessment. No result was saved.",
                                 category=category, **metadata)
        texts, refused = output_content(response)
        if refused:
            raise AIGradingError("AI did not return a complete assessment. No result was saved.",
                                 category="model_refusal", **metadata)
        if len(texts) != 1:
            category = "structured_output_missing" if not texts else "structured_output_ambiguous"
            raise AIGradingError("AI did not return a complete assessment. No result was saved.",
                                 category=category, **metadata)
        try:
            draft = AssessmentDraft.model_validate_json(texts[0])
        except ValidationError as error:
            validation_types = {entry.get("type") for entry in error.errors(include_url=False)}
            category, fields = validation_diagnostics(error)
            raise AIGradingError("AI returned an invalid assessment. No result was saved.",
                                 category="structured_json_invalid" if "json_invalid" in validation_types
                                 else "structured_schema_invalid",
                                 fields=fields, **metadata) from error
        try:
            result = draft.to_result(prepared.rubric)
        except ValidationError as error:
            _, fields = validation_diagnostics(error)
            raise AIGradingError("AI returned an invalid assessment. No result was saved.",
                                 category="semantic_result_invalid", fields=fields,
                                 parsed_exists=True, **{key: value for key, value in metadata.items()
                                                        if key != "parsed_exists"}) from error
        except RubricAlignmentError as error:
            raise AIGradingError("AI returned grading that does not match the canonical rubric. No result was saved.",
                                 category="semantic_result_invalid", fields=error.fields,
                                 semantic_details=error.details,
                                 parsed_exists=True, **{key: value for key, value in metadata.items()
                                                        if key != "parsed_exists"}) from error
        usage = response.usage
        return AssessmentResponse(
            grading_mode="openai", result=result,
            usage=AIUsage(model=response.model or model,
                          input_tokens=usage.input_tokens if usage else None,
                          output_tokens=usage.output_tokens if usage else None,
                          total_tokens=usage.total_tokens if usage else None),
        )
    except AuthenticationError as error:
        raise AIGradingError("OpenAI authentication failed. Check the backend API key configuration.", 503,
                             category="authentication_failure") from error
    except RateLimitError as error:
        if error.code == "insufficient_quota":
            raise AIGradingError("OpenAI credits or quota are insufficient. Check your API billing before retrying.", 402,
                                 category="insufficient_quota") from error
        raise AIGradingError("OpenAI rate limit reached. Wait before trying again.", 429,
                             category="rate_limit") from error
    except APITimeoutError as error:
        raise AIGradingError("AI grading timed out. No result was saved; check API usage before retrying.", 504,
                             category="request_timeout") from error
    except APIConnectionError as error:
        raise AIGradingError("Could not connect to OpenAI. No result was saved; check your connection and API usage before retrying.",
                             category="connection_failure") from error
    except ValidationError as error:
        category, fields = validation_diagnostics(error)
        raise AIGradingError("AI returned an invalid assessment. No result was saved.",
                             category=category, fields=fields) from error
    except (ValueError, AttributeError) as error:
        raise AIGradingError("AI returned an invalid assessment. No result was saved.",
                             category=type(error).__name__) from error
    except OpenAIError as error:
        raise AIGradingError("OpenAI could not complete grading. Check your model configuration and try again later.",
                             category="api_failure") from error
