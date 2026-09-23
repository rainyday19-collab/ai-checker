from dataclasses import dataclass, field
from math import isclose
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from document_processing import EmbeddedImage, PDFPageContent, ProcessedDocument
from rubric import CanonicalRubric


class MarkingPointResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    marking_point_id: str | None = None
    criterion: str = Field(min_length=1)
    status: Literal["met", "partially_met", "not_met", "unclear"]
    awarded_marks: float = Field(ge=0)
    max_marks: float = Field(gt=0)
    rationale: str = Field(min_length=1)
    evidence_status: Literal["found", "not_found", "unclear"]
    evidence: str | None = None
    source_location: str | None = None
    criterion_type: Literal["semantic", "exact"] | None = None
    guidance: str | None = None

    @model_validator(mode="after")
    def validate_marking_point(self):
        if self.awarded_marks > self.max_marks:
            raise ValueError("Marking-point marks cannot exceed their maximum.")
        if self.status == "met" and self.awarded_marks != self.max_marks:
            raise ValueError("A met marking point must receive its full marks.")
        if self.status == "partially_met" and not 0 < self.awarded_marks < self.max_marks:
            raise ValueError("A partially met marking point must receive partial marks.")
        if self.status == "not_met" and self.awarded_marks != 0:
            raise ValueError("A marking point that is not met must receive zero marks.")
        if self.status == "unclear" and self.awarded_marks == self.max_marks:
            raise ValueError("An unclear marking point cannot receive full marks.")
        if self.evidence_status == "not_found":
            if self.awarded_marks != 0:
                raise ValueError("A marking point with no evidence found must receive zero marks.")
            if self.status != "not_met":
                raise ValueError("Missing evidence must produce a not-met marking point.")
        if self.evidence_status == "unclear" and self.status != "unclear":
            raise ValueError("Unclear evidence must produce an unclear marking point.")
        if self.status == "unclear" and self.evidence_status != "unclear":
            raise ValueError("An unclear marking point requires unclear evidence.")
        evidence = (self.evidence or "").strip()
        source = (self.source_location or "").strip()
        if self.evidence_status in {"found", "unclear"}:
            if not evidence:
                raise ValueError("Found or unclear marking-point evidence must be described.")
            if not source:
                raise ValueError("Found or unclear marking-point evidence requires a source location.")
        if self.awarded_marks > 0 and self.evidence_status != "found":
            raise ValueError("Awarded marking-point marks require found evidence.")
        return self


class QuestionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    question_id: str | None = None
    question: str = Field(min_length=1)
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    feedback: str = Field(min_length=1)
    evidence: str | None = None
    evidence_status: Literal["found", "not_found", "unclear"] | None = None
    source_location: str | None = None
    marking_points: list[MarkingPointResult] | None = None

    @model_validator(mode="after")
    def validate_score_and_evidence(self):
        if self.score > self.max_score:
            raise ValueError("Question score cannot exceed its maximum score.")
        if self.marking_points is not None:
            if not self.marking_points:
                raise ValueError("Marking-point results cannot be empty.")
            if not isclose(sum(point.max_marks for point in self.marking_points), self.max_score, abs_tol=1e-9):
                raise ValueError("Marking-point maximum marks must sum to the question maximum.")
            if not isclose(sum(point.awarded_marks for point in self.marking_points), self.score, abs_tol=1e-9):
                raise ValueError("Question score must equal the sum of awarded marking-point marks.")
        # A missing status identifies a legacy saved result. New model output always
        # includes a status and is subject to the evidence-grounding rules below.
        if self.evidence_status is None:
            return self
        evidence = (self.evidence or "").strip()
        source = (self.source_location or "").strip()
        if self.evidence_status == "not_found" and self.score != 0:
            raise ValueError("A question with no evidence found must receive zero marks.")
        if self.evidence_status == "found":
            if not evidence:
                raise ValueError("Found evidence requires a concise evidence description.")
            if not source:
                raise ValueError("Found evidence requires a source location.")
        if self.evidence_status == "unclear":
            if self.score == self.max_score:
                raise ValueError("Unclear evidence cannot receive full marks.")
            if not evidence:
                raise ValueError("Unclear evidence requires a concise uncertainty description.")
            if not source:
                raise ValueError("Unclear evidence requires a source location.")
        return self

    @classmethod
    def from_marking_points(cls, *, question: str, max_score: float, feedback: str,
                            question_id: str | None = None,
                            marking_points: list[MarkingPointResult | dict]):
        points = [MarkingPointResult.model_validate(point) for point in marking_points]
        statuses = {point.evidence_status for point in points}
        evidence_status = "found" if "found" in statuses else "unclear" if "unclear" in statuses else "not_found"
        evidences = list(dict.fromkeys(
            point.evidence.strip() for point in points if point.evidence and point.evidence.strip()
        ))
        sources = list(dict.fromkeys(
            point.source_location.strip() for point in points if point.source_location and point.source_location.strip()
        ))
        return cls(
            question_id=question_id,
            question=question,
            score=sum(point.awarded_marks for point in points),
            max_score=max_score,
            feedback=feedback,
            evidence=" | ".join(evidences) or None,
            evidence_status=evidence_status,
            source_location=", ".join(sources) or None,
            marking_points=points,
        )


class AssessmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    total_score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    summary: str = Field(min_length=1)
    questions: list[QuestionResult] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_totals(self):
        if self.total_score > self.max_score:
            raise ValueError("Total score cannot exceed the maximum score.")
        if not isclose(self.total_score, sum(question.score for question in self.questions), abs_tol=1e-9):
            raise ValueError("Total score must match the sum of question scores.")
        if not isclose(self.max_score, sum(question.max_score for question in self.questions), abs_tol=1e-9):
            raise ValueError("Maximum score must match the sum of question maximum scores.")
        return self

    @computed_field
    @property
    def percentage(self) -> float:
        return round(self.total_score / self.max_score * 100, 2)


class AIUsage(BaseModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    model: str


class AssessmentResponse(BaseModel):
    id: int | None = None
    grading_mode: Literal["mock", "openai"]
    result: AssessmentResult
    usage: AIUsage | None = None


class AssessmentHistoryItem(BaseModel):
    id: int
    created_at: str
    student_filename: str
    total_score: float
    max_score: float
    percentage: float


class SavedAssessment(AssessmentResponse):
    id: int
    created_at: str
    student_filename: str
    mark_scheme_filename: str | None
    used_manual_criteria: bool


class AssessmentStatistics(BaseModel):
    total_assessments: int
    average_percentage: float | None
    average_score: float | None
    average_max_score: float | None
    highest_percentage: float | None
    recent_assessments: list[AssessmentHistoryItem]


@dataclass(frozen=True)
class PreparedContent:
    kind: Literal["text", "image", "pdf_visual", "docx"]
    filename: str
    content_type: str
    text: str = ""
    original_bytes: bytes | None = field(default=None, repr=False)
    embedded_images: tuple[EmbeddedImage, ...] = field(default=(), repr=False)
    pdf_pages: tuple[PDFPageContent, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class GradingInput:
    student_work: PreparedContent
    mark_scheme: PreparedContent | None
    criteria_text: str | None
    student_work_pages: tuple[PreparedContent, ...] = ()
    rubric: CanonicalRubric | None = None

    @property
    def ordered_student_work(self) -> tuple[PreparedContent, ...]:
        return self.student_work_pages or (self.student_work,)


def prepare_document(document: ProcessedDocument) -> PreparedContent:
    text = document.text.strip()
    if document.type == "docx":
        if not text and not document.embedded_images:
            raise ValueError("DOCX inputs require extracted content.")
        return PreparedContent(
            kind="docx", filename=document.filename, content_type=document.content_type,
            text=text, embedded_images=document.embedded_images,
        )
    if document.type == "image":
        if not document.original_bytes:
            raise ValueError("Visual inputs require the original file bytes.")
        return PreparedContent(
            kind="image",
            filename=document.filename,
            content_type=document.content_type,
            text=text,
            original_bytes=document.original_bytes,
        )
    if document.type == "pdf" and document.requires_visual_processing:
        if not document.pdf_pages or not any(page.rendered_image for page in document.pdf_pages):
            raise ValueError("Visual PDF inputs require rendered page images.")
        # Deliberately omit the original PDF bytes: only bounded, in-memory page
        # images and extracted text continue into the multimodal request.
        return PreparedContent(
            kind="pdf_visual", filename=document.filename, content_type=document.content_type,
            text=text, pdf_pages=document.pdf_pages,
        )
    if not text:
        raise ValueError("Text inputs require extracted document text.")
    return PreparedContent("text", document.filename, document.content_type, text)


def prepare_grading_input(
    student_work: ProcessedDocument | list[ProcessedDocument],
    mark_scheme: ProcessedDocument | None = None,
    criteria_text: str | None = None,
    rubric: CanonicalRubric | None = None,
) -> GradingInput:
    criteria = (criteria_text or "").strip() or None
    if mark_scheme is None and criteria is None and rubric is None:
        raise ValueError("Provide a mark scheme or non-empty grading criteria.")
    documents = student_work if isinstance(student_work, list) else [student_work]
    if not documents:
        raise ValueError("Student work requires at least one document.")
    prepared_pages = tuple(prepare_document(document) for document in documents)
    return GradingInput(
        student_work=prepared_pages[0],
        mark_scheme=prepare_document(mark_scheme) if mark_scheme else None,
        criteria_text=criteria,
        student_work_pages=prepared_pages,
        rubric=rubric,
    )
