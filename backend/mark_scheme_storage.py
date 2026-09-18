"""Local, private storage for assignment mark schemes; never use client paths."""
from pathlib import Path
import re
from uuid import uuid4

from fastapi import HTTPException

import database
from document_processing import MAX_FILE_BYTES, SUPPORTED_TYPES, extract_content


def storage_directory() -> Path:
    directory = database.DB_PATH.parent / "mark_schemes"
    if directory.is_symlink() or directory.resolve() != database.DB_PATH.parent.resolve() / "mark_schemes":
        raise HTTPException(503, "Mark scheme storage is unavailable.")
    return directory


def managed_path(key: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.(pdf|png|jpg|jpeg)", key):
        raise HTTPException(503, "The saved mark scheme is unavailable. Create a new assignment with a valid scheme.")
    directory = storage_directory().resolve()
    path = directory / key
    if path.is_symlink() or not path.resolve().is_relative_to(directory):
        raise HTTPException(503, "Mark scheme storage is unavailable.")
    return path


def save_document(document) -> dict:
    filename = document.filename.replace("\\", "/").rsplit("/", 1)[-1]
    key = uuid4().hex + Path(filename).suffix.lower()
    path = managed_path(key)
    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file:
            created = True
            file.write(document.original_bytes)
    except OSError as error:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise HTTPException(503, "Could not save the mark scheme. No assignment was created.") from error
    return {"mark_scheme_key": key, "mark_scheme_filename": filename, "mark_scheme_type": document.type}


def load_document(assignment):
    key = assignment.get("mark_scheme_key")
    if not key:
        return None
    path = managed_path(key)
    try:
        with path.open("rb") as file:
            data = file.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise HTTPException(503, "The saved mark scheme file is missing or unreadable. Create a new assignment with a valid scheme.") from error
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "The saved mark scheme must be 10 MB or smaller.")
    return extract_content(data, assignment["mark_scheme_filename"], SUPPORTED_TYPES[path.suffix], "Mark scheme")


def delete_file(key: str):
    path = managed_path(key)
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        raise HTTPException(503, "Could not clean up the saved mark scheme file.") from error


def cleanup_unreferenced(keys):
    for key in keys:
        if not key:
            continue
        with database.connection() as connection:
            shared = connection.execute("SELECT 1 FROM assignments WHERE mark_scheme_key = ?", (key,)).fetchone()
        if shared is None:
            delete_file(key)
