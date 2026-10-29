"""Backend application factory; no AI/MCP/biometric imports or startup I/O."""

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.api.errors import install_error_handlers
from backend.api.health import router as health_router
from backend.api.auth import router as auth_router
from backend.api.workflows import router as workflow_router
from backend.api.documents import router as document_router
from backend.api.upload_limits import UploadLimitMiddleware
from backend.api.integrations import router as integration_router
from backend.api.business import router as business_router
from backend.integrations.client import HospitalClient
from backend.auth.router import router as staff_auth_router
from backend.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    app = FastAPI(title="MediLink Backend", version="1.0")
    app.state.settings = config
    app.state.hospital_client = HospitalClient()
    app.add_middleware(UploadLimitMiddleware, max_file_bytes=config.document_max_bytes)
    app.add_middleware(
        CORSMiddleware, allow_origins=list(config.cors_origins),
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        # Generate our own ID rather than trusting arbitrary client log content.
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if (request.url.path.startswith("/api/documents") or request.url.path.endswith("/tool-calls")
                or request.url.path.startswith(("/api/integrations", "/api/onboarding", "/api/claims", "/api/patients", "/api/appointments"))
                or request.url.path.endswith(("/case", "/advance", "/evidence"))):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(workflow_router)
    app.include_router(staff_auth_router)
    app.include_router(document_router)
    app.include_router(integration_router)
    app.include_router(business_router)
    return app
