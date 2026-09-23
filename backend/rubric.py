from math import isclose
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CanonicalMarkingPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    id: str = Field(pattern=r"^q[1-9][0-9]*_p[1-9][0-9]*$")
    criterion: str = Field(min_length=1)
    max_marks: float = Field(gt=0)
    criterion_type: Literal["semantic", "exact"]
    guidance: str | None = None


class CanonicalQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    question_id: str = Field(pattern=r"^q[1-9][0-9]*$")
    question_text: str = Field(min_length=1)
    max_marks: float = Field(gt=0)
    marking_points: list[CanonicalMarkingPoint] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_points(self):
        expected_prefix = f"{self.question_id}_p"
        point_ids = [point.id for point in self.marking_points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("Canonical marking-point IDs must be unique.")
        if any(not point_id.startswith(expected_prefix) for point_id in point_ids):
            raise ValueError("Canonical marking-point IDs must belong to their question.")
        if not isclose(sum(point.max_marks for point in self.marking_points), self.max_marks, abs_tol=1e-9):
            raise ValueError("Canonical marking-point maxima must sum to the question maximum.")
        return self


class CanonicalRubric(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    rubric_version: Literal[1] = 1
    title: str | None = None
    total_marks: float = Field(gt=0)
    questions: list[CanonicalQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_questions(self):
        question_ids = [question.question_id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Canonical question IDs must be unique.")
        if not isclose(sum(question.max_marks for question in self.questions), self.total_marks, abs_tol=1e-9):
            raise ValueError("Canonical question maxima must sum to the rubric total.")
        return self

    @property
    def marking_point_count(self) -> int:
        return sum(len(question.marking_points) for question in self.questions)


class CanonicalMarkingPointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    criterion: str = Field(min_length=1)
    max_marks: float = Field(gt=0)
    criterion_type: Literal["semantic", "exact"]
    guidance: str | None


class CanonicalQuestionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    question_text: str = Field(min_length=1)
    max_marks: float = Field(gt=0)
    marking_points: list[CanonicalMarkingPointDraft] = Field(min_length=1)


class CanonicalRubricDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    title: str | None
    total_marks: float = Field(gt=0)
    questions: list[CanonicalQuestionDraft] = Field(min_length=1)

    def to_canonical(self, expected_total_marks: float | None = None) -> CanonicalRubric:
        rubric = CanonicalRubric(
            title=self.title,
            total_marks=self.total_marks,
            questions=[{
                "question_id": f"q{question_index}",
                "question_text": question.question_text,
                "max_marks": question.max_marks,
                "marking_points": [{
                    "id": f"q{question_index}_p{point_index}",
                    **point.model_dump(),
                } for point_index, point in enumerate(question.marking_points, start=1)],
            } for question_index, question in enumerate(self.questions, start=1)],
        )
        if expected_total_marks is not None and not isclose(
                rubric.total_marks, expected_total_marks, abs_tol=1e-9):
            raise ValueError("Canonical rubric total does not match the assignment's declared total marks.")
        return rubric


def demo_rubric(total_marks: float | None = None) -> CanonicalRubric:
    maximum = total_marks or 20
    return CanonicalRubric(
        title="Demo rubric",
        total_marks=maximum,
        questions=[{
            "question_id": "q1",
            "question_text": "Demo assessment criterion",
            "max_marks": maximum,
            "marking_points": [{
                "id": "q1_p1",
                "criterion": "Demonstrate the requested knowledge or method",
                "max_marks": maximum,
                "criterion_type": "semantic",
                "guidance": "Fixed development rubric; uploaded criteria are not analyzed in mock mode.",
            }],
        }],
    )
