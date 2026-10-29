import hashlib
import importlib
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import ModuleType

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfWriter

from backend.api.schemas import ExtractionResult
from backend.application import create_app
from backend.db.connection import connection
from backend.services import document_service as service


def image_bytes(format="PNG"):
    result = io.BytesIO()
    Image.new("RGB", (4, 4), color="white").save(result, format=format)
    return result.getvalue()


def pdf_bytes(*, encrypted=False, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt("synthetic-password")
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def upload(api_env, *, role="patient", filename="report.txt", content=b"Synthetic report", media="text/plain", client=None):
    return (client or api_env.client).post("/api/documents", headers=api_env.headers(role),
                                         files={"file": (filename, content, media)})


def test_upload_metadata_raw_storage_and_restart_persistence(api_env):
    response = upload(api_env)
    assert response.status_code == 201
    document = response.json()["data"]
    assert document["owner_user_id"] == api_env.users["patient"]["user_id"]
    assert document["extraction_status"] == "pending"
    assert document["size_bytes"] == len(b"Synthetic report")
    assert not {"storage_key", "sha256", "text", "extraction_token"} & document.keys()
    with connection(api_env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT storage_key, sha256 FROM documents WHERE id = %s", (document["id"],))
            key, digest = cursor.fetchone()
    stored = api_env.settings.document_storage_dir / key
    assert stored.read_bytes() == b"Synthetic report"
    assert digest == hashlib.sha256(stored.read_bytes()).hexdigest()
    # A completely new app instance finds both metadata and original content.
    with TestClient(create_app(api_env.settings)) as restarted:
        assert restarted.get(f"/api/documents/{document['id']}", headers=api_env.headers()).json()["data"] == document
        downloaded = restarted.get(f"/api/documents/{document['id']}/content", headers=api_env.headers())
        assert downloaded.content == b"Synthetic report"
        assert downloaded.headers["Cache-Control"] == "private, no-store"
        assert downloaded.headers["X-Content-Type-Options"] == "nosniff"
        assert "attachment" in downloaded.headers["Content-Disposition"]


@pytest.mark.parametrize("filename,content,media", [
    ("report.pdf", pdf_bytes(), "application/pdf"),
    ("scan.png", image_bytes(), "image/png"),
    ("scan.jpeg", image_bytes("JPEG"), "image/jpeg"),
    ("report.TXT", b"Synthetic text", "application/octet-stream"),
])
def test_supported_formats(api_env, filename, content, media):
    response = upload(api_env, filename=filename, content=content, media=media)
    assert response.status_code == 201
    assert response.json()["data"]["extraction_status"] == "pending"


@pytest.mark.parametrize("filename,content,media,expected", [
    ("report.exe", b"executable", "application/octet-stream", 415),
    ("report.pdf", b"not a PDF", "application/pdf", 422),
    ("report.pdf", pdf_bytes(encrypted=True), "application/pdf", 422),
    ("report.txt", b"Synthetic", "text/html", 415),
    ("report.txt", b"\xff\xfe\x00", "text/plain", 422),
    ("report.txt", b"a\x00b", "text/plain", 422),
    ("report.txt", b"  \n", "text/plain", 422),
    ("report.txt", b"", "text/plain", 422),
    ("scan.png", image_bytes("JPEG"), "image/png", 422),
    ("scan.jpg", image_bytes("JPEG")[:-32], "image/jpeg", 422),
    ("../report.txt", b"Synthetic", "text/plain", 400),
    ("..\\report.txt", b"Synthetic", "text/plain", 400),
    (".hidden.txt", b"Synthetic", "text/plain", 400),
])
def test_invalid_uploads_leave_no_document_or_staging_file(api_env, filename, content, media, expected):
    response = upload(api_env, filename=filename, content=content, media=media)
    assert response.status_code == expected
    assert api_env.client.get("/api/documents", headers=api_env.headers()).json()["data"] == []
    assert not list(api_env.settings.document_storage_dir.glob("*"))


def test_file_limit_and_total_request_limit(api_env):
    small = replace(api_env.settings, document_max_bytes=8)
    with TestClient(create_app(small)) as client:
        assert upload(api_env, content=b"123456789", client=client).status_code == 413
        response = client.post("/api/documents", headers={**api_env.headers(), "Content-Length": "1000000"}, content=b"x")
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert not list(api_env.settings.document_storage_dir.glob("*"))


def test_streamed_request_without_content_length_is_bounded(api_env):
    small = replace(api_env.settings, document_max_bytes=8)
    with TestClient(create_app(small)) as client:
        def chunks():
            yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="large.txt"\r\nContent-Type: text/plain\r\n\r\n'
            yield b"x" * 70000
            yield b"\r\n--boundary--\r\n"
        response = client.post("/api/documents", headers={**api_env.headers(), "Content-Type": "multipart/form-data; boundary=boundary"}, content=chunks())
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_pdf_page_and_image_pixel_limits(api_env):
    limited = replace(api_env.settings, document_max_pdf_pages=1, document_max_image_pixels=4)
    with TestClient(create_app(limited)) as client:
        assert upload(api_env, filename="report.pdf", content=pdf_bytes(pages=2), media="application/pdf", client=client).status_code == 422
        assert upload(api_env, filename="scan.png", content=image_bytes(), media="image/png", client=client).status_code == 422


@pytest.mark.parametrize("role,metadata_status,content_status", [
    ("patient", 200, 200), ("patient2", 404, 404), ("receptionist", 404, 404),
    ("doctor", 200, 200), ("admin", 200, 200), ("auditor", 200, 403),
])
def test_document_access_policy(api_env, role, metadata_status, content_status):
    document = upload(api_env).json()["data"]
    url = f"/api/documents/{document['id']}"
    assert api_env.client.get(url, headers=api_env.headers(role)).status_code == metadata_status
    assert api_env.client.get(url + "/content", headers=api_env.headers(role)).status_code == content_status
    assert api_env.client.get(url + "/extraction", headers=api_env.headers(role)).status_code == content_status
    assert upload(api_env, role="auditor").status_code == 403


def test_receptionist_can_only_read_own_documents_and_listing_is_scoped(api_env):
    upload(api_env)
    own = upload(api_env, role="receptionist").json()["data"]
    listed = api_env.client.get("/api/documents", headers=api_env.headers("receptionist")).json()["data"]
    assert [d["id"] for d in listed] == [own["id"]]
    assert api_env.client.get(f"/api/documents/{own['id']}/content", headers=api_env.headers("receptionist")).status_code == 200
    assert len(api_env.client.get("/api/documents", headers=api_env.headers("auditor")).json()["data"]) == 2


def test_storage_failure_rolls_back_metadata(api_env, monkeypatch):
    monkeypatch.setattr(service.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("private path")))
    response = upload(api_env)
    assert response.status_code == 503
    assert "private path" not in response.text
    assert api_env.client.get("/api/documents", headers=api_env.headers()).json()["data"] == []
    assert not list(api_env.settings.document_storage_dir.glob("*"))


def test_missing_source_returns_controlled_error(api_env):
    document = upload(api_env).json()["data"]
    for path in api_env.settings.document_storage_dir.iterdir():
        path.unlink()
    response = api_env.client.get(f"/api/documents/{document['id']}/content", headers=api_env.headers())
    assert response.status_code == 503
    assert str(api_env.settings.document_storage_dir) not in response.text


def test_storage_path_cannot_follow_symlink_outside_private_root(api_env, tmp_path):
    document = upload(api_env).json()["data"]
    stored = next(api_env.settings.document_storage_dir.iterdir())
    outside = tmp_path / "unrelated-private.txt"
    outside.write_text("Do not return this content")
    stored.unlink()
    stored.symlink_to(outside)
    response = api_env.client.get(f"/api/documents/{document['id']}/content", headers=api_env.headers())
    assert response.status_code == 503
    assert "Do not return" not in response.text


@pytest.mark.parametrize("filename,content,media,expected,status", [
    ("notes.txt", b"Synthetic report", "text/plain", 200, "completed"),
    ("scan.pdf", pdf_bytes(), "application/pdf", 400, "failed"),
])
def test_legacy_upload_uses_persistent_storage(api_env, monkeypatch, filename, content, media, expected, status):
    graph = ModuleType("backend.agents.graph")
    graph.run_conversation = lambda *args, **kwargs: pytest.fail("Document upload must not invoke the agent")
    monkeypatch.setitem(sys.modules, "backend.agents.graph", graph)
    sys.modules.pop("backend.main", None)
    main = importlib.import_module("backend.main")
    try:
        with TestClient(main.app) as client:
            response = client.post("/chat/upload", headers=api_env.headers("doctor"), files={"file": (filename, content, media)})
            assert response.status_code == expected
            if expected == 200:
                payload = response.json()
                assert payload["filename"] == filename
                assert payload["file_type"] == "TXT"
                assert payload["characters_extracted"] == len(content)
                assert payload["preview"] == content.decode()
        with TestClient(create_app(api_env.settings)) as restarted:
            docs = restarted.get("/api/documents", headers=api_env.headers("doctor")).json()["data"]
            assert len(docs) == 1 and docs[0]["extraction_status"] == status
            extracted = restarted.get(f"/api/documents/{docs[0]['id']}/extraction", headers=api_env.headers("doctor")).json()["data"]
            if status == "completed":
                assert extracted["result"]["text"] == content.decode()
            else:
                assert extracted["error_code"] == "NO_READABLE_TEXT"
    finally:
        sys.modules.pop("backend.main", None)


def test_extraction_lifecycle_is_persistent_and_validated(api_env):
    document = upload(api_env).json()["data"]
    job = service.claim_extraction(api_env.settings, document["id"])
    with pytest.raises(HTTPException):
        service.claim_extraction(api_env.settings, document["id"])
    result = ExtractionResult(text="Synthetic report", fields=[{
        "name": "patient.name", "value": "Synthetic Patient", "confidence": 0.9, "validation_status": "needs_review",
    }])
    completed = service.finish_extraction(api_env.settings, job, result=result)
    assert completed.extraction_status == "completed" and completed.extraction_version == 2
    with TestClient(create_app(api_env.settings)) as restarted:
        extracted = restarted.get(f"/api/documents/{document['id']}/extraction", headers=api_env.headers()).json()["data"]
    assert extracted["result"]["fields"][0]["validation_status"] == "needs_review"
    assert extracted["result"]["text"] == "Synthetic report"
    assert "token" not in extracted and "source_path" not in extracted
    with pytest.raises(HTTPException):
        service.finish_extraction(api_env.settings, job, result=result)


def test_failed_extraction_retry_rejects_stale_worker(api_env):
    document = upload(api_env).json()["data"]
    old = service.claim_extraction(api_env.settings, document["id"])
    service.finish_extraction(api_env.settings, old, error_code="EXTRACTION_FAILED")
    with pytest.raises(HTTPException):
        service.claim_extraction(api_env.settings, document["id"])
    current = service.claim_extraction(api_env.settings, document["id"], retry_failed=True)
    with pytest.raises(HTTPException):
        service.finish_extraction(api_env.settings, old, result=ExtractionResult(text="stale"))
    service.finish_extraction(api_env.settings, current, result=ExtractionResult(text="confirmed"))


def test_only_one_concurrent_worker_claim_succeeds(api_env):
    document = upload(api_env).json()["data"]
    def claim(_):
        try:
            return service.claim_extraction(api_env.settings, document["id"]).token
        except HTTPException as exc:
            assert exc.status_code == 409
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert len([r for r in results if r]) == 1


def test_extraction_worker_does_not_accept_empty_or_invalid_results(api_env):
    document = upload(api_env).json()["data"]
    job = service.claim_extraction(api_env.settings, document["id"])
    with pytest.raises(ValueError):
        service.finish_extraction(api_env.settings, job, result=ExtractionResult())
    with pytest.raises(ValidationError):
        ExtractionResult(fields=[{"name": "field", "value": "x", "confidence": 2}])
    with pytest.raises(ValueError):
        service.finish_extraction(api_env.settings, job, error_code="private exception text")
    assert api_env.client.post(f"/api/documents/{document['id']}/extraction", headers=api_env.headers(), json={"status": "completed"}).status_code == 405


def test_workflow_document_ownership_links_and_execution_gate(api_env):
    document = upload(api_env).json()["data"]
    body = {"workflow_type": "onboarding", "request": "Synthetic onboarding", "document_ids": [document["id"]]}
    assert api_env.client.post("/api/workflows", headers=api_env.headers("patient2"), json=body).status_code == 404
    assert api_env.client.post("/api/workflows", headers=api_env.headers(), json={**body, "document_ids": [document["id"], document["id"]]}).status_code == 422
    response = api_env.client.post("/api/workflows", headers={**api_env.headers(), "Idempotency-Key": "with-document"}, json=body)
    assert response.status_code == 201
    run = response.json()["data"]
    assert run["document_ids"] == [document["id"]]
    again = api_env.client.post("/api/workflows", headers={**api_env.headers(), "Idempotency-Key": "with-document"}, json=body)
    assert again.status_code == 200 and again.json()["data"]["id"] == run["id"]
    url = f"/api/workflows/{run['id']}"
    assert api_env.client.post(url + "/transitions", headers=api_env.headers("doctor"), json={"expected_version": 0, "status": "PLANNING"}).status_code == 200
    assert api_env.client.put(url + "/plan", headers=api_env.headers("doctor"), json={"expected_version": 1, "steps": [{"action": "validate_patient"}]}).status_code == 200
    assert api_env.client.post(url + "/transitions", headers=api_env.headers("doctor"), json={"expected_version": 2, "status": "EXECUTING"}).status_code == 409
    job = service.claim_extraction(api_env.settings, document["id"])
    service.finish_extraction(api_env.settings, job, result=ExtractionResult(text="Synthetic extracted text"))
    assert api_env.client.post(url + "/transitions", headers=api_env.headers("doctor"), json={"expected_version": 2, "status": "EXECUTING"}).status_code == 200
