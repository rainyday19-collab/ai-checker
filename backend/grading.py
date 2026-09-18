from dataclasses import dataclass, field
from math import isclose
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from document_processing import ProcessedDocument


class QuestionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    question: str = Field(min_length=1)
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    feedback: str = Field(min_length=1)
    evidence: str | None = None

    @model_validator(mode="after")
    def validate_score(self):
        if self.score > self.max_score:
            raise ValueError("Question score cannot exceed its maximum score.")
        return self


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
    kind: Literal["text", "image", "pdf_visual"]
    filename: str
    content_type: str
    text: str = ""
    original_bytes: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class GradingInput:
    student_work: PreparedContent
    mark_scheme: PreparedContent | None
    criteria_text: str | None


def prepare_document(document: ProcessedDocument) -> PreparedContent:
    text = document.text.strip()
    if document.type == "image" or document.requires_visual_processing:
        if not document.original_bytes:
            raise ValueError("Visual inputs require the original file bytes.")
        # Preserve mixed-PDF text as well as the file for future visual processing.
        return PreparedContent(
            kind="image" if document.type == "image" else "pdf_visual",
            filename=document.filename,
            content_type=document.content_type,
            text=text,
            original_bytes=document.original_bytes,
        )
    if not text:
        raise ValueError("Text inputs require extracted document text.")
    return PreparedContent("text", document.filename, document.content_type, text)


def prepare_grading_input(
    student_work: ProcessedDocument,
    mark_scheme: ProcessedDocument | None = None,
    criteria_text: str | None = None,
) -> GradingInput:
    criteria = (criteria_text or "").strip() or None
    if mark_scheme is None and criteria is None:
        raise ValueError("Provide a mark scheme or non-empty grading criteria.")
    return GradingInput(
        student_work=prepare_document(student_work),
        mark_scheme=prepare_document(mark_scheme) if mark_scheme else None,
        criteria_text=criteria,
    )
