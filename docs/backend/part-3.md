# Backend Part 3: documents and extraction persistence

Based on `backend-part-2`. This implements Person 1's document storage, metadata,
validation and status-persistence responsibilities. MCP, agent and frontend files
are unchanged. OCR/vision, speech-to-text and automatic extraction workers remain
the AI owner's work; this backend provides their persistence interface.

## Setup and upgrade

Activate the project environment and run from the repository root:

```bash
python -m pip install -r backend/requirements-foundation.txt
python -m backend.db.migrate --status
python -m backend.db.migrate
python -m uvicorn backend.application:create_app --factory --reload
```

The full existing application remains `backend.main:app`. Both entry points expose
the new document APIs; the full app also retains `/chat/upload`.

Configure the following in `.env` if changing the defaults:

```env
MEDILINK_DOCUMENT_STORAGE_DIR=./var/documents
MEDILINK_DOCUMENT_MAX_BYTES=5242880
MEDILINK_DOCUMENT_MAX_IMAGE_PIXELS=20000000
MEDILINK_DOCUMENT_MAX_PDF_PAGES=100
```

Relative environment storage paths resolve from the repository root. Use a private
persistent directory, never a frontend/public directory. The default directory is
Git-ignored. New storage directories use mode 0700 and files use mode 0600 where
the OS supports POSIX permissions. Existing directory permissions remain under
operator control. Deployments must retain both PostgreSQL and the document volume
across restarts; database backups alone do not contain the source files.

Migration `0003` creates `documents`, `document_extraction_results` and
`workflow_documents`. Earlier migrations remain unchanged. Existing users,
workflows and clinical tables are retained. Because Part 2 disallowed document
references, unmanaged nonempty legacy reference arrays cause a migration failure
rather than being silently adopted. Such data needs an explicit reconciliation.

## HTTP APIs

All routes require an active authenticated identity and use Part 2's auth-stage
and role checks. JSON responses use the standard `{data, request_id}` envelope.

| Method/path | Result |
| --- | --- |
| POST `/api/documents` | Multipart `file`; 201 with persistent metadata |
| GET `/api/documents` | Scoped metadata list; `limit` 1–100, `offset` ≥ 0 |
| GET `/api/documents/{id}` | Metadata, extraction status/version and safe error code |
| GET `/api/documents/{id}/content` | Authenticated attachment download of the source file |
| GET `/api/documents/{id}/extraction` | Extraction status and any stored text/structured fields |

Document responses carry `Cache-Control: private, no-store` and
`X-Content-Type-Options: nosniff`. Content downloads are attachments, not public
links. No storage key, filesystem path, extraction token or raw file bytes are
included in metadata. The backend derives owner IDs from authentication.

Metadata contains UUID `id`, `owner_user_id`, display `filename`, verified
`media_type`, `size_bytes`, `created_at`, `updated_at`, `extraction_status`,
`extraction_version` and optional `extraction_error_code`. New uploads start
`pending`, version 0. Upload success means the source was stored, not that OCR or
extraction has occurred.

### Document permissions

| Role | Metadata | Source and extraction results | Upload |
| --- | --- | --- | --- |
| Patient | Own documents | Own documents | Yes, as self |
| Receptionist | Own documents | Own documents | Yes, as self |
| Doctor | All documents | All documents | Yes, as self |
| Admin | All documents | All documents | Yes, as self |
| Auditor | All metadata | Denied | Denied |

Another patient's/receptionist's document returns 404 to unauthorized callers.
This follows the prototype's single-facility role model; assignment-based consent
or explicit document sharing is not introduced here.

## Upload validation and storage

Supported source formats:

- PDF: valid `%PDF-` header, strict parser validation, 1–100 pages by default;
  encrypted documents are rejected. Scanned PDFs may be stored without OCR.
- TXT: nonempty readable UTF-8 text (optional BOM), without binary control bytes.
- PNG/JPEG: matching image format, verified/decoded structure, bounded pixel count.

Filename extension, declared MIME type and content must agree. An absent MIME type
or `application/octet-stream` is accepted when structural validation succeeds.
Names must be printable, bounded to 255 UTF-8 bytes, and contain no path separators,
drive delimiters or leading dot. Server-generated UUID filenames are used on disk.

Files stream in bounded chunks with an exact configurable byte cap (5 MiB default,
25 MiB maximum configuration). Whole multipart request bodies are bounded to the
file cap plus 64 KiB overhead, including streamed requests without Content-Length.
PDF page/image pixel caps are independently configurable. Oversize requests return
413; unsupported/mismatched types return 415; malformed/empty content returns 422;
invalid filenames return 400. Errors do not echo file contents or private paths.

The backend stages a validated file privately and computes its SHA-256 digest.
Metadata insertion and file publication happen before the database transaction
commits. Validation/definite storage failures remove staging files and roll back
metadata. Files never use the submitted filename as their filesystem path.

PostgreSQL and a filesystem cannot share one atomic transaction. A process crash
or an uncertain DB commit after publication can leave an unreferenced private
file. The backend keeps that file rather than deleting a source that might have
successfully committed metadata. Automated orphan reconciliation is deferred to
the reliability work in Part 8. Missing sources return a controlled 503; metadata
does not falsely claim that the download succeeded.

## Extraction worker contract

Trusted backend workers import from `backend.services.document_service`:

```python
from backend.api.schemas import ExtractionResult
from backend.services.document_service import claim_extraction, finish_extraction

job = claim_extraction(settings, document_id)
# Worker reads job.source_path and uses its configured extraction implementation.
result = ExtractionResult(
    text="Synthetic extracted text",
    fields=[{
        "name": "patient.name", "value": "Synthetic Patient",
        "confidence": 0.95, "validation_status": "needs_review",
    }],
)
finish_extraction(settings, job, result=result)
# On a definite failure instead:
# finish_extraction(settings, job, error_code="EXTRACTION_FAILED")
```

Claiming is transactional and moves `pending → processing`, returning a private
source path, media type and one-use attempt token. Only one concurrent worker may
claim the document. Completion atomically stores validated results and moves to
`completed`; failure moves to `failed` with a closed error code. Every status
change increments the extraction version. Reusing a stale/completed token returns
a conflict and cannot replace newer results.

Supported safe error codes: `EXTRACTION_FAILED`, `SOURCE_UNAVAILABLE`, and
`NO_READABLE_TEXT`. Workers must not persist raw exception strings as status errors.
An explicit `claim_extraction(..., retry_failed=True)` starts a fresh attempt after
failure; it does not reclaim a running/completed attempt. Worker crash recovery
and scheduling are not implemented in this part.

Results contain up to 100,000 text characters and up to 100 uniquely named fields.
Each field contains `name`, `value` (up to 5,000 characters), confidence in [0, 1],
and `validation_status`: `unreviewed`, `valid`, `needs_review`, or `invalid`.
Completed results require nonempty text or fields. These are extraction validation
states, not clinical approval decisions. There is no public status/result-write
endpoint, so patients cannot self-mark extraction or approval as successful.

## Workflow references

`POST /api/workflows` now accepts existing document UUIDs in `document_ids`.
The backend validates content access to every reference, rejects duplicates and
persists a foreign-key-backed association in the same workflow transaction.
Unknown/unauthorized references return 404 without creating a partial workflow.
Idempotent workflow creation continues to work with document references.

Workflow responses expose the reference IDs; they do not copy source files or
extracted text into workflow records. Transitioning to `EXECUTING` or `COMPLETED`
is blocked while any referenced document is pending, processing or failed.
Extraction completion alone does not establish clinical validity or grant a
high-risk approval; those authoritative risk/approval gates belong to Part 7.

## Existing chat upload compatibility

`POST /chat/upload` remains doctor/receptionist-only and accepts PDF/TXT. It now
uses the same persistent source/metadata/result storage and removes the old
in-memory document dictionary. Its existing `message`, `filename`, `file_type`,
`characters_extracted` and `preview` response fields are retained; `document_id`
and `extraction_status` are added.

The existing deterministic PDF text/TXT decoding path is retained here; no AI/OCR
is added. The stored extracted text remains capped at 50,000 characters. A scanned
PDF without text is preserved with a failed `NO_READABLE_TEXT` status and returns
a controlled 400. A later OCR worker may explicitly retry it. The unchanged chat
agent is not connected to the new stored results by this backend-only change.

## Validation

```bash
python -m pip install -r backend/requirements-test.txt
MEDILINK_TEST_DB_DSN='dbname=medilink_part3_test host=localhost user=postgres' \
  python -m pytest tests/backend -q
```

Use an empty disposable PostgreSQL database. Fixtures refuse existing public
tables and keep synthetic source files in temporary directories. Coverage includes
restart persistence, formats/content limits, metadata validation, ownership,
download guards, storage failures, stale/concurrent worker claims, workflow links,
legacy upload behavior and upgrading from Part 2. Real OCR/vision/speech and the
unchanged AI/MCP pipeline are outside this backend suite.

Validation result: **105 backend tests passed** against an isolated PostgreSQL 18
database. Static checks passed for all changed Python files.
