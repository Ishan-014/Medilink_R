from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, HTTPException
from fastapi.encoders import jsonable_encoder

from backend.api.schemas import Role, SuccessResponse
from backend.api.audit_schemas import AuditRecord, SystemEvent, Page, WorkflowTrace
from backend.auth.security import current_identity, require_roles, settings_for_request
from backend.core.config import Settings
from backend.recovery.contracts import ReconcileInput, ReconciliationRecord
from backend.services import audit_service, reconciliation_service

router = APIRouter(prefix="/api", tags=["audit and recovery"])


def dates(after, before):
    if any(value is not None and value.tzinfo is None for value in (after, before)):
        raise HTTPException(422, "Date filters must include a timezone")
    if after and before and after >= before:
        raise HTTPException(422, "after must precede before")


@router.get("/audit", response_model=SuccessResponse[Page[AuditRecord]])
def audit(
    request: Request,
    workflow_id: UUID | None = None,
    actor_user_id: str | None = Query(None, max_length=100),
    outcome: Literal["success", "denied", "failure"] | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    user: dict = Depends(require_roles(Role.ADMIN, Role.AUDITOR)),
    settings: Settings = Depends(settings_for_request),
):
    dates(after, before)
    data = audit_service.list_records(
        settings,
        "audit_logs",
        limit=limit,
        offset=offset,
        workflow_id=workflow_id,
        actor_user_id=actor_user_id,
        outcome=outcome,
        after=after,
        before=before,
    )
    return SuccessResponse(
        data=jsonable_encoder(data), request_id=request.state.request_id
    )


@router.get("/system-events", response_model=SuccessResponse[Page[SystemEvent]])
def events(
    request: Request,
    workflow_id: UUID | None = None,
    code: str | None = Query(None, pattern=r"^[A-Z][A-Z0-9_]{0,79}$"),
    after: datetime | None = None,
    before: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    user: dict = Depends(require_roles(Role.ADMIN, Role.AUDITOR)),
    settings: Settings = Depends(settings_for_request),
):
    dates(after, before)
    data = audit_service.list_records(
        settings,
        "system_events",
        limit=limit,
        offset=offset,
        workflow_id=workflow_id,
        code=code,
        after=after,
        before=before,
    )
    return SuccessResponse(
        data=jsonable_encoder(data), request_id=request.state.request_id
    )


@router.get(
    "/workflows/{workflow_id}/trace", response_model=SuccessResponse[WorkflowTrace]
)
def trace(
    workflow_id: UUID,
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    user: dict = Depends(current_identity),
    settings: Settings = Depends(settings_for_request),
):
    return SuccessResponse(
        data=jsonable_encoder(
            audit_service.trace(settings, str(workflow_id), user, limit, offset)
        ),
        request_id=request.state.request_id,
    )


@router.post(
    "/workflows/{workflow_id}/tool-calls/{call_id}/reconcile",
    response_model=SuccessResponse[ReconciliationRecord],
)
async def reconcile(
    workflow_id: UUID,
    call_id: UUID,
    body: ReconcileInput,
    request: Request,
    user: dict = Depends(require_roles(Role.ADMIN)),
    settings: Settings = Depends(settings_for_request),
):
    data = await reconciliation_service.reconcile(
        settings,
        request.app.state.reconciliation_client,
        str(workflow_id),
        str(call_id),
        user,
        body,
    )
    return SuccessResponse(data=data, request_id=request.state.request_id)


@router.get(
    "/workflows/{workflow_id}/reconciliations",
    response_model=SuccessResponse[list[ReconciliationRecord]],
)
def attempts(
    workflow_id: UUID,
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    user: dict = Depends(current_identity),
    settings: Settings = Depends(settings_for_request),
):
    return SuccessResponse(
        data=reconciliation_service.list_attempts(
            settings, str(workflow_id), user, limit, offset
        ),
        request_id=request.state.request_id,
    )
