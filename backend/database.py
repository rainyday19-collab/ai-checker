from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from grading import AIUsage, AssessmentResponse, AssessmentResult, SavedAssessment

DB_PATH = Path(__file__).parent / "data" / "ai_checker.db"


@contextmanager
def connection():
    with closing(sqlite3.connect(DB_PATH)) as database:
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys = ON")
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
        database.execute("""
            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                subject TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL
            )
        """)
        database.execute("""
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT,
                total_marks REAL CHECK (total_marks > 0),
                mark_scheme_text TEXT,
                created_at TEXT NOT NULL
            )
        """)
        database.execute("CREATE INDEX IF NOT EXISTS assignments_class_id ON assignments(class_id)")
        columns = {row["name"] for row in database.execute("PRAGMA table_info(assignments)")}
        for column in ("mark_scheme_key", "mark_scheme_filename", "mark_scheme_type"):
            if column not in columns:
                database.execute(f"ALTER TABLE assignments ADD COLUMN {column} TEXT")
        database.execute("CREATE UNIQUE INDEX IF NOT EXISTS assignments_scheme_key ON assignments(mark_scheme_key)")
        database.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
                student_name TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'graded', 'failed')),
                assessment_id INTEGER REFERENCES assessments(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                graded_at TEXT,
                usage_json TEXT
            )
        """)
        database.execute("CREATE INDEX IF NOT EXISTS submissions_assignment_id ON submissions(assignment_id)")


def save_assessment(assessment: AssessmentResponse, student_filename: str, mark_scheme_filename: str | None, used_manual_criteria: bool) -> int:
    with connection() as database:
        return insert_assessment(database, assessment, student_filename, mark_scheme_filename, used_manual_criteria)


def insert_assessment(database, assessment: AssessmentResponse, student_filename: str, mark_scheme_filename: str | None, used_manual_criteria: bool) -> int:
    """Shared insert; the caller owns the transaction."""
    result = assessment.result
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


CLASS_QUERY = """
    SELECT classes.*, COUNT(assignments.id) AS assignment_count
    FROM classes LEFT JOIN assignments ON assignments.class_id = classes.id
"""


def list_classes() -> list[dict]:
    with connection() as database:
        rows = database.execute(CLASS_QUERY + " GROUP BY classes.id ORDER BY classes.created_at DESC, classes.id DESC").fetchall()
        return [dict(row) for row in rows]


def get_class(class_id: int) -> dict | None:
    with connection() as database:
        row = database.execute(CLASS_QUERY + " WHERE classes.id = ? GROUP BY classes.id", (class_id,)).fetchone()
        return dict(row) if row else None


def create_class(values: dict) -> dict:
    with connection() as database:
        cursor = database.execute(
            "INSERT INTO classes (name, subject, description, created_at) VALUES (?, ?, ?, ?)",
            (values["name"], values["subject"], values["description"], datetime.now(timezone.utc).isoformat()),
        )
        row = database.execute("SELECT *, 0 AS assignment_count FROM classes WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def delete_class(class_id: int) -> bool:
    from mark_scheme_storage import cleanup_unreferenced, managed_path
    with connection() as database:
        keys = [row[0] for row in database.execute("SELECT mark_scheme_key FROM assignments WHERE class_id = ?", (class_id,)) if row[0]]
        for key in keys:
            managed_path(key)
        deleted = database.execute("DELETE FROM classes WHERE id = ?", (class_id,)).rowcount > 0
    cleanup_unreferenced(keys)
    return deleted


def list_assignments(class_id: int) -> list[dict]:
    with connection() as database:
        rows = database.execute(
            "SELECT * FROM assignments WHERE class_id = ? ORDER BY created_at DESC, id DESC", (class_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_assignment(assignment_id: int) -> dict | None:
    with connection() as database:
        row = database.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        return dict(row) if row else None


def create_assignment(class_id: int, values: dict) -> dict | None:
    with connection() as database:
        # INSERT ... SELECT keeps the parent check and insert in the same statement.
        cursor = database.execute("""
            INSERT INTO assignments (class_id, title, description, total_marks, mark_scheme_text, created_at,
                                     mark_scheme_key, mark_scheme_filename, mark_scheme_type)
            SELECT id, ?, ?, ?, ?, ?, ?, ?, ? FROM classes WHERE id = ?
        """, (values["title"], values["description"], values["total_marks"], values["mark_scheme_text"],
              datetime.now(timezone.utc).isoformat(), values.get("mark_scheme_key"),
              values.get("mark_scheme_filename"), values.get("mark_scheme_type"), class_id))
        if not cursor.rowcount:
            return None
        row = database.execute("SELECT * FROM assignments WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def delete_assignment(assignment_id: int) -> bool:
    from mark_scheme_storage import cleanup_unreferenced, managed_path
    with connection() as database:
        row = database.execute("SELECT mark_scheme_key FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        key = row[0] if row else None
        if key:
            managed_path(key)
        deleted = database.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,)).rowcount > 0
    cleanup_unreferenced([key])
    return deleted


SUBMISSION_QUERY = """
    SELECT submissions.id, assignment_id, student_name, original_filename, status,
           assessment_id, submissions.created_at, graded_at,
           assessments.total_score, assessments.max_score, assessments.percentage
    FROM submissions LEFT JOIN assessments ON assessments.id = submissions.assessment_id
"""


def save_submission(assignment_id: int, student_name: str, filename: str, assessment: AssessmentResponse) -> int | None:
    """Success-only persistence. Both inserts roll back if either fails."""
    with connection() as database:
        if database.execute("SELECT id FROM assignments WHERE id = ?", (assignment_id,)).fetchone() is None:
            return None
        assessment_id = insert_assessment(database, assessment, filename, None, True)
        now = datetime.now(timezone.utc).isoformat()
        cursor = database.execute("""
            INSERT INTO submissions (assignment_id, student_name, original_filename, status,
                                     assessment_id, created_at, graded_at, usage_json)
            VALUES (?, ?, ?, 'graded', ?, ?, ?, ?)
        """, (assignment_id, student_name, filename, assessment_id, now, now,
              assessment.usage.model_dump_json() if assessment.usage else None))
        return cursor.lastrowid


def list_submissions(assignment_id: int) -> list[dict]:
    with connection() as database:
        rows = database.execute(SUBMISSION_QUERY +
            " WHERE assignment_id = ? ORDER BY submissions.created_at DESC, submissions.id DESC", (assignment_id,)).fetchall()
        return [dict(row) for row in rows]


def get_submission(submission_id: int) -> dict | None:
    with connection() as database:
        row = database.execute(SUBMISSION_QUERY + " WHERE submissions.id = ?", (submission_id,)).fetchone()
        if row is None:
            return None
        usage = database.execute("SELECT usage_json FROM submissions WHERE id = ?", (submission_id,)).fetchone()[0]
    values = dict(row)
    assessment = get_assessment(values["assessment_id"]) if values["assessment_id"] else None
    if assessment and usage:
        assessment = assessment.model_copy(update={"usage": AIUsage.model_validate_json(usage)})
    return values | {"assessment": assessment}


def delete_submission(submission_id: int) -> bool:
    # Assessments are independent history records and are deliberately preserved.
    with connection() as database:
        return database.execute("DELETE FROM submissions WHERE id = ?", (submission_id,)).rowcount > 0
