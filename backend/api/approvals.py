"""Review metadata, independent decisions, and canonical action previews."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, JsonValue

from backend.api.schemas import Contract, Role, SuccessResponse
from backend.auth.security import current_identity, require_roles, settings_for_request
from backend.core.config import Settings
from backend.risk.policy import POLICY_VERSION, ENABLED_WRITES, classify
from backend.services import approval_service as service

router = APIRouter(prefix="/api", tags=["risk and approvals"])


class ApprovalRecord(Contract):
    id: UUID
    workflow_id: UUID
    workflow_step_id: UUID
    operation: str
    risk: Literal["medium", "high"]
    policy_version: str
    requested_by: str
    requested_at: datetime
    request_reason: str
    expires_at: datetime
    status: Literal["pending", "approved", "rejected", "consumed", "invalidated"]
    decision_mode: Literal["human", "automatic"] | None
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    consumed_by: str | None
    consumed_at: datetime | None
    consumed_call_id: UUID | None


class RequestApproval(Contract):
    expected_version: int = Field(ge=0, strict=True)
    reason: str = Field(default="Request independent action review", min_length=1, max_length=1000)


class Decision(RequestApproval):
    approval_id: UUID
    reason: str = Field(min_length=1, max_length=1000)


def record(row):
    return ApprovalRecord(**{key: row[key] for key in ApprovalRecord.model_fields})


@router.get("/risk-policy", response_model=SuccessResponse[dict[str, JsonValue]])
def policy(request: Request, user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data={"version": POLICY_VERSION, "medium_risk_review": settings.medium_risk_review,
        "high_risk_review": "required", "enabled_writes": {op: classify(op) for op in ENABLED_WRITES}},
        request_id=request.state.request_id)


@router.get("/approvals", response_model=SuccessResponse[list[ApprovalRecord]])
def queue(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
          user: dict = Depends(require_roles(Role.DOCTOR, Role.ADMIN)), settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=[record(row) for row in service.queue(settings, user, limit, offset)], request_id=request.state.request_id)


@router.get("/workflows/{workflow_id}/approvals", response_model=SuccessResponse[list[ApprovalRecord]])
def history(workflow_id: UUID, request: Request, user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=[record(row) for row in service.list_workflow(settings, str(workflow_id), user)], request_id=request.state.request_id)


@router.post("/workflows/{workflow_id}/approvals", response_model=SuccessResponse[ApprovalRecord])
def submit(workflow_id: UUID, body: RequestApproval, request: Request, user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=record(service.request_approval(settings, str(workflow_id), user, body)), request_id=request.state.request_id)


@router.get("/workflows/{workflow_id}/approvals/{approval_id}/review", response_model=SuccessResponse[dict[str, JsonValue]])
def review(workflow_id: UUID, approval_id: UUID, request: Request,
           user: dict = Depends(require_roles(Role.DOCTOR, Role.ADMIN)), settings: Settings = Depends(settings_for_request)):
    from fastapi.encoders import jsonable_encoder
    return SuccessResponse(data=jsonable_encoder(service.review(settings, str(workflow_id), approval_id, user)), request_id=request.state.request_id)


@router.post("/workflows/{workflow_id}/approve", response_model=SuccessResponse[ApprovalRecord])
def approve(workflow_id: UUID, body: Decision, request: Request,
            user: dict = Depends(require_roles(Role.DOCTOR, Role.ADMIN)), settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=record(service.decide(settings, str(workflow_id), user, body, True)), request_id=request.state.request_id)


@router.post("/workflows/{workflow_id}/reject", response_model=SuccessResponse[ApprovalRecord])
def reject(workflow_id: UUID, body: Decision, request: Request,
           user: dict = Depends(require_roles(Role.DOCTOR, Role.ADMIN)), settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=record(service.decide(settings, str(workflow_id), user, body, False)), request_id=request.state.request_id)
