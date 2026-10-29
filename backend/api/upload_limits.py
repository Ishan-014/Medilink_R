"""Bound multipart requests before their bodies can be fully spooled to disk."""

from uuid import uuid4

from fastapi import HTTPException
from starlette.responses import JSONResponse


class UploadLimitMiddleware:
    def __init__(self, app, max_file_bytes: int):
        self.app = app
        # Leave room for the multipart filename/boundary and transport metadata.
        self.max_body_bytes = max_file_bytes + 64 * 1024

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope["path"].rstrip("/") not in {"/api/documents", "/chat/upload"}):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) < 0:
                    raise ValueError
                too_large = int(declared) > self.max_body_bytes
            except ValueError:
                return await self.reject(scope, receive, send, 400, "Invalid Content-Length")
            if too_large:
                return await self.reject(scope, receive, send, 413, "Upload request exceeds the configured limit")
        count = 0

        async def bounded_receive():
            nonlocal count
            message = await receive()
            if message["type"] == "http.request":
                count += len(message.get("body", b""))
                if count > self.max_body_bytes:
                    raise HTTPException(413, "Upload request exceeds the configured limit")
            return message

        await self.app(scope, bounded_receive, send)

    async def reject(self, scope, receive, send, status, message):
        request_id = scope.setdefault("state", {}).setdefault("request_id", uuid4().hex)
        body = {"detail": message}
        if scope["path"].startswith("/api/"):
            body = {"error": {"code": "FILE_TOO_LARGE" if status == 413 else "INVALID_REQUEST",
                              "message": message, "details": []}, "request_id": request_id}
        response = JSONResponse(body, status_code=status, headers={"X-Request-ID": request_id})
        await response(scope, receive, send)
