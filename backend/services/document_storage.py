"""Private local document storage and bounded structural validation. No OCR."""

import hashlib
import os
import re
import tempfile
import warnings
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

FORMATS = {
    ".pdf": ("application/pdf", ".pdf"), ".txt": ("text/plain", ".txt"),
    ".png": ("image/png", ".png"), ".jpg": ("image/jpeg", ".jpg"),
    ".jpeg": ("image/jpeg", ".jpg"),
}


def upload_metadata(file: UploadFile):
    name = (file.filename or "").strip()
    if (not name or len(name.encode("utf-8")) > 255 or name.startswith(".")
            or any(c in name for c in '/\\:') or any(not c.isprintable() for c in name)):
        raise HTTPException(400, "Invalid document filename")
    suffix = Path(name).suffix.lower()
    if suffix not in FORMATS:
        raise HTTPException(415, "Supported files are PDF, UTF-8 TXT, PNG and JPEG")
    media_type, stored_suffix = FORMATS[suffix]
    declared = (file.content_type or "").split(";", 1)[0].lower().strip()
    if declared not in {"", "application/octet-stream", media_type}:
        raise HTTPException(415, "File type does not match its declared media type")
    return name, media_type, stored_suffix


def validate_content(path: Path, media_type: str, settings):
    try:
        if media_type == "text/plain":
            text = path.read_text(encoding="utf-8-sig")
            if not text.strip() or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                raise ValueError("Invalid plain text")
        elif media_type == "application/pdf":
            with path.open("rb") as source:
                if source.read(5) != b"%PDF-":
                    raise ValueError("Invalid PDF header")
                source.seek(0)
                reader = PdfReader(source, strict=True)
                if reader.is_encrypted or not 1 <= len(reader.pages) <= settings.document_max_pdf_pages:
                    raise ValueError("Unsupported PDF")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    expected = "PNG" if media_type == "image/png" else "JPEG"
                    if image.format != expected or image.width * image.height > settings.document_max_image_pixels:
                        raise ValueError("Unsupported image")
                    image.verify()
                with Image.open(path) as image:
                    image.load()
    except (ValueError, OSError, SyntaxError, KeyError, TypeError, RecursionError, PyPdfError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, "Document content is invalid, encrypted, or exceeds format limits") from exc


def storage_root(settings) -> Path:
    root = Path(settings.document_storage_dir).expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def stored_path(settings, key: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}\.(pdf|txt|png|jpg)", key):
        raise ValueError("Invalid server storage key")
    root = Path(settings.document_storage_dir).expanduser().resolve()
    path = root / key
    # Reject symlink escapes, even if metadata was changed outside the API.
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("Invalid server storage path")
    return path


@contextmanager
def staged_upload(settings, file: UploadFile):
    name, media_type, suffix = upload_metadata(file)
    root = storage_root(settings)
    fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=root)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as destination:
            while chunk := file.file.read(64 * 1024):
                size += len(chunk)
                if size > settings.document_max_bytes:
                    raise HTTPException(413, "Document exceeds the configured upload limit")
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if size == 0:
            raise HTTPException(422, "Document is empty")
        validate_content(temporary, media_type, settings)
        yield temporary, name, media_type, suffix, size, digest.hexdigest()
    finally:
        temporary.unlink(missing_ok=True)
