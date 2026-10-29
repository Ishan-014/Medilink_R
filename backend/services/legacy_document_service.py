"""Keep the existing PDF/TXT chat upload response with persistent storage."""

from fastapi import HTTPException
from pypdf import PdfReader

from backend.api.schemas import ExtractionResult
from backend.services.document_service import upload_document, claim_extraction, finish_extraction


def upload_chat_document(settings, file, user):
    if not (file.filename or "").lower().endswith((".pdf", ".txt")):
        raise HTTPException(400, "Only PDF and TXT files are currently supported")
    document = upload_document(settings, file, user)
    job = claim_extraction(settings, document.id)
    try:
        if job.media_type == "text/plain":
            text = job.source_path.read_text(encoding="utf-8-sig")
        else:
            with job.source_path.open("rb") as source:
                reader = PdfReader(source, strict=True)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
        text = text.strip()
    except Exception as exc:
        finish_extraction(settings, job, error_code="EXTRACTION_FAILED")
        raise HTTPException(400, "Unable to extract document text") from exc
    if not text:
        finish_extraction(settings, job, error_code="NO_READABLE_TEXT")
        raise HTTPException(400, "No readable text was found; OCR is required for scanned documents")
    finish_extraction(settings, job, result=ExtractionResult(text=text[:50000]))
    return {
        "message": "Document uploaded and processed successfully.",
        "filename": document.filename, "file_type": "PDF" if job.media_type == "application/pdf" else "TXT",
        "characters_extracted": len(text), "preview": text[:500],
        "document_id": str(document.id), "extraction_status": "completed",
    }
