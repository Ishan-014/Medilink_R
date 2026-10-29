"""Document persistence, access checks and trusted extraction-worker handoff."""

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from psycopg2.extras import Json, RealDictCursor

from backend.api.schemas import DocumentMetadata, DocumentExtraction, ExtractionResult, Role
from backend.db.connection import connection
from backend.services.document_storage import staged_upload, stored_path

GLOBAL_READERS = {Role.DOCTOR, Role.ADMIN}
METADATA_READERS = GLOBAL_READERS | {Role.AUDITOR}
ERROR_CODES = {"EXTRACTION_FAILED", "SOURCE_UNAVAILABLE", "NO_READABLE_TEXT"}


def authorize(row, user, *, content=False):
    if user["role"] == Role.AUDITOR and content:
        raise HTTPException(403, "Auditors may inspect metadata only")
    allowed_roles = GLOBAL_READERS if content else METADATA_READERS
    if user["role"] not in allowed_roles and row["owner_user_id"] != user["user_id"]:
        raise HTTPException(404, "Document not found")


def load(cursor, document_id, user=None, *, content=False, lock=False):
    cursor.execute("SELECT * FROM documents WHERE id = %s" + (" FOR UPDATE" if lock else ""), (str(document_id),))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    if user is not None:
        authorize(row, user, content=content)
    return dict(row)


def metadata(row) -> DocumentMetadata:
    return DocumentMetadata(**{key: row[key] for key in DocumentMetadata.model_fields})


def upload_document(settings, file, user) -> DocumentMetadata:
    if user["role"] == Role.AUDITOR:
        raise HTTPException(403, "Auditors have read-only access")
    try:
        with staged_upload(settings, file) as (temporary, name, media, suffix, size, digest):
            document_id = str(uuid4())
            key = document_id + suffix
            with connection(settings) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        INSERT INTO documents (id, owner_user_id, filename, media_type, size_bytes, sha256, storage_key)
                        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
                    """, (document_id, user["user_id"], name, media, size, digest, key))
                    row = cursor.fetchone()
                    os.replace(temporary, stored_path(settings, key))
                # Connection context commits only after the file is published.
            return metadata(row)
    except OSError as exc:
        raise HTTPException(503, "Document storage is unavailable") from exc
    # On an uncertain DB commit, keep the published file. Never remove a file
    # that might be referenced by a successfully committed metadata record.


def get_document(settings, document_id, user):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            return metadata(load(cursor, document_id, user))


def list_documents(settings, user, *, limit=50, offset=0):
    where, args = ("", []) if user["role"] in METADATA_READERS else (" WHERE owner_user_id = %s", [user["user_id"]])
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM documents" + where + " ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
                           (*args, limit, offset))
            return [metadata(row) for row in cursor.fetchall()]


def get_content(settings, document_id, user):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            row = load(cursor, document_id, user, content=True)
    try:
        path = stored_path(settings, row["storage_key"])
        if not path.is_file():
            raise OSError("Missing source")
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Document source is unavailable") from exc
    return path, row["filename"], row["media_type"]


def get_extraction(settings, document_id, user):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            row = load(cursor, document_id, user, content=True, lock=True)
            cursor.execute("SELECT result FROM document_extraction_results WHERE document_id = %s", (str(document_id),))
            result = cursor.fetchone()
            return DocumentExtraction(
                document_id=document_id, status=row["extraction_status"], version=row["extraction_version"],
                result=ExtractionResult(**result["result"]) if result else None, error_code=row["extraction_error_code"],
            )


def link_documents(cursor, workflow_id, document_ids, user):
    if len(set(document_ids)) != len(document_ids):
        raise HTTPException(422, "Duplicate document references are not allowed")
    for document_id in document_ids:
        load(cursor, document_id, user, content=True)
        cursor.execute("INSERT INTO workflow_documents (workflow_id, document_id) VALUES (%s, %s)",
                       (str(workflow_id), str(document_id)))


@dataclass(frozen=True)
class ExtractionJob:
    document_id: str
    token: str
    source_path: Path
    media_type: str


def claim_extraction(settings, document_id, *, retry_failed=False) -> ExtractionJob:
    """Trusted server-worker API, never exposed as a patient/staff HTTP mutation."""
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            row = load(cursor, document_id, lock=True)
            allowed = {"pending", "failed"} if retry_failed else {"pending"}
            if row["extraction_status"] not in allowed:
                raise HTTPException(409, "Extraction is already claimed or finished")
            token = str(uuid4())
            cursor.execute("""
                UPDATE documents SET extraction_status = 'processing', extraction_token = %s,
                    extraction_version = extraction_version + 1, extraction_error_code = NULL,
                    updated_at = clock_timestamp() WHERE id = %s
            """, (token, str(document_id)))
            return ExtractionJob(str(document_id), token, stored_path(settings, row["storage_key"]), row["media_type"])


def finish_extraction(settings, job: ExtractionJob, *, result: ExtractionResult | None = None, error_code: str | None = None):
    if (result is None) == (error_code is None) or (error_code is not None and error_code not in ERROR_CODES):
        raise ValueError("Supply validated extraction results OR a supported error code")
    if result is not None:
        # Enforce the schema even when called by a non-HTTP worker.
        result = ExtractionResult.model_validate(result)
        if not result.text.strip() and not result.fields:
            raise ValueError("Completed extraction requires text or structured fields")
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            row = load(cursor, job.document_id, lock=True)
            if row["extraction_status"] != "processing" or str(row["extraction_token"]) != job.token:
                raise HTTPException(409, "Extraction attempt is stale or already finished")
            if result is not None:
                cursor.execute("""
                    INSERT INTO document_extraction_results (document_id, result) VALUES (%s, %s)
                    ON CONFLICT (document_id) DO UPDATE SET result = EXCLUDED.result, created_at = clock_timestamp()
                """, (job.document_id, Json(result.model_dump(mode="json"))))
            cursor.execute("""
                UPDATE documents SET extraction_status = %s, extraction_token = NULL,
                    extraction_error_code = %s, extraction_version = extraction_version + 1,
                    updated_at = clock_timestamp() WHERE id = %s RETURNING *
            """, ("completed" if result is not None else "failed", error_code, job.document_id))
            return metadata(cursor.fetchone())
