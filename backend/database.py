from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from grading import AssessmentResponse, AssessmentResult, SavedAssessment

DB_PATH = Path(__file__).parent / "data" / "ai_checker.db"


@contextmanager
def connection():
    with closing(sqlite3.connect(DB_PATH)) as database:
        database.row_factory = sqlite3.Row
        with database:
            yield database


def initialize_database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connection() as database:
        database.execute("""
            CREATE TABLE IF NOT EXISTS assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                student_filename TEXT NOT NULL,
                mark_scheme_filename TEXT,
                used_manual_criteria INTEGER NOT NULL,
                grading_mode TEXT NOT NULL,
                total_score REAL NOT NULL,
                max_score REAL NOT NULL,
                percentage REAL NOT NULL,
                summary TEXT NOT NULL,
                result_json TEXT NOT NULL
            )
        """)


def save_assessment(assessment: AssessmentResponse, student_filename: str, mark_scheme_filename: str | None, used_manual_criteria: bool) -> int:
    result = assessment.result
    with connection() as database:
        cursor = database.execute("""
            INSERT INTO assessments (
                created_at, student_filename, mark_scheme_filename, used_manual_criteria,
                grading_mode, total_score, max_score, percentage, summary, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(), student_filename, mark_scheme_filename,
            int(used_manual_criteria), assessment.grading_mode, result.total_score,
            result.max_score, result.percentage, result.summary, result.model_dump_json(),
        ))
        return cursor.lastrowid


def list_assessments() -> list[dict]:
    with connection() as database:
        rows = database.execute("""
            SELECT id, created_at, student_filename, total_score, max_score, percentage
            FROM assessments ORDER BY created_at DESC, id DESC
        """).fetchall()
        return [dict(row) for row in rows]


def get_assessment(assessment_id: int) -> SavedAssessment | None:
    with connection() as database:
        row = database.execute("SELECT * FROM assessments WHERE id = ?", (assessment_id,)).fetchone()
    if row is None:
        return None
    values = dict(row)
    result_data = json.loads(values.pop("result_json"))
    # Stored JSON is complete, but percentage is recalculated during validation.
    result_data.pop("percentage", None)
    result = AssessmentResult.model_validate(result_data)
    return SavedAssessment(
        id=values["id"], created_at=values["created_at"],
        student_filename=values["student_filename"], mark_scheme_filename=values["mark_scheme_filename"],
        used_manual_criteria=bool(values["used_manual_criteria"]), grading_mode=values["grading_mode"], result=result,
    )


def delete_assessment(assessment_id: int) -> bool:
    with connection() as database:
        cursor = database.execute("DELETE FROM assessments WHERE id = ?", (assessment_id,))
        return cursor.rowcount > 0


def get_statistics() -> dict:
    with connection() as database:
        totals = database.execute("""
            SELECT COUNT(*) AS total_assessments,
                   ROUND(AVG(percentage), 2) AS average_percentage,
                   ROUND(AVG(total_score), 2) AS average_score,
                   ROUND(AVG(max_score), 2) AS average_max_score,
                   MAX(percentage) AS highest_percentage
            FROM assessments
        """).fetchone()
        recent = database.execute("""
            SELECT id, created_at, student_filename, total_score, max_score, percentage
            FROM assessments ORDER BY created_at DESC, id DESC LIMIT 5
        """).fetchall()
    return dict(totals) | {"recent_assessments": [dict(row) for row in recent]}
