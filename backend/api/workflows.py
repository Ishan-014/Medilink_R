from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import Field

from backend.api.schemas import Contract, SuccessResponse, WorkflowCreate, WorkflowRun, WorkflowStatus, WorkflowType
from backend.auth.security import current_identity, settings_for_request
from backend.core.config import Settings
from backend.services import workflow_service as service

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class VersionInput(Contract):
    expected_version: int = Field(ge=0, strict=True)


class TransitionInput(VersionInput):
    status: WorkflowStatus


class PlanStepInput(Contract):
    action: str = Field(min_length=1, max_length=200)


class PlanInput(VersionInput):
    steps: list[PlanStepInput] = Field(min_length=1, max_length=50)


@router.post("", status_code=201, response_model=SuccessResponse[WorkflowRun])
def create(body: WorkflowCreate, request: Request, response: Response,
           idempotency_key: Annotated[str | None, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")] = None,
           user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    data, replayed = service.create_workflow(settings, body, user, idempotency_key)
    if replayed:
        response.status_code = 200
    return SuccessResponse(data=data, request_id=request.state.request_id)


@router.get("", response_model=SuccessResponse[list[WorkflowRun]])
def list_runs(request: Request, status: WorkflowStatus | None = None,
              workflow_type: WorkflowType | None = None,
              limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
              user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.list_workflows(settings, user, status=status, workflow_type=workflow_type,
                                                     limit=limit, offset=offset), request_id=request.state.request_id)


@router.get("/{workflow_id}", response_model=SuccessResponse[WorkflowRun])
def detail(workflow_id: UUID, request: Request, user: dict = Depends(current_identity),
           settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.get_workflow(settings, str(workflow_id), user), request_id=request.state.request_id)


@router.put("/{workflow_id}/plan", response_model=SuccessResponse[WorkflowRun])
def plan(workflow_id: UUID, body: PlanInput, request: Request, user: dict = Depends(current_identity),
         settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.change_workflow(settings, str(workflow_id), user, body, plan=True),
                           request_id=request.state.request_id)


@router.post("/{workflow_id}/transitions", response_model=SuccessResponse[WorkflowRun])
def transition(workflow_id: UUID, body: TransitionInput, request: Request, user: dict = Depends(current_identity),
               settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.change_workflow(settings, str(workflow_id), user, body),
                           request_id=request.state.request_id)


@router.post("/{workflow_id}/cancel", response_model=SuccessResponse[WorkflowRun])
def cancel(workflow_id: UUID, body: VersionInput, request: Request, user: dict = Depends(current_identity),
           settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.cancel_workflow(settings, str(workflow_id), user, body.expected_version),
                           request_id=request.state.request_id)
