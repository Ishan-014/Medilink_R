import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.schemas import HealthData, SuccessResponse, ErrorResponse
from backend.core.config import Settings
from backend.db.connection import connection

router = APIRouter(prefix="/api/health", tags=["health"])


def app_settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("", response_model=SuccessResponse[HealthData])
def health(request: Request):
    """Liveness: does not require PostgreSQL, biometrics, MCP, or an LLM."""
    return SuccessResponse(
        data=HealthData(status="ok"), request_id=request.state.request_id,
    )


@router.get("/ready", response_model=SuccessResponse[HealthData],
            responses={503: {"model": ErrorResponse}})
def readiness(request: Request, settings: Settings = Depends(app_settings)):
    """Database connectivity check. Does not apply migrations or alter data."""
    try:
        with connection(settings) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return SuccessResponse(
        data=HealthData(status="ready"), request_id=request.state.request_id,
    )
