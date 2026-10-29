from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from backend.api.schemas import Contract, Role, SuccessResponse
from backend.auth.security import current_identity, require_roles, settings_for_request
from backend.core.config import Settings
from backend.graph.client import GraphClient
from backend.graph.contracts import GraphQueryInput, GraphQueryRecord, GraphQueryOutput
from backend.services import graph_service as service

router = APIRouter(prefix="/api", tags=["graph context"])


def graph_client(request: Request) -> GraphClient:
    return request.app.state.graph_client


class GraphHealth(Contract):
    status: Literal["ready"]


@router.get("/graph/health", response_model=SuccessResponse[GraphHealth])
async def health(request: Request, user: dict = Depends(require_roles(Role.ADMIN, Role.AUDITOR)),
                 settings: Settings = Depends(settings_for_request), client: GraphClient = Depends(graph_client)):
    return SuccessResponse(data=GraphHealth(**await client.health(settings)), request_id=request.state.request_id)


@router.post("/workflows/{workflow_id}/graph-queries", response_model=SuccessResponse[GraphQueryOutput])
async def query(workflow_id: UUID, body: GraphQueryInput, request: Request,
                 user: dict = Depends(require_roles(Role.DOCTOR, Role.ADMIN)), settings: Settings = Depends(settings_for_request),
                 client: GraphClient = Depends(graph_client)):
    return SuccessResponse(data=await service.execute(settings, client, str(workflow_id), user, body), request_id=request.state.request_id)


@router.get("/workflows/{workflow_id}/graph-queries", response_model=SuccessResponse[list[GraphQueryRecord]])
def queries(workflow_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
            user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.list_queries(settings, str(workflow_id), user, limit, offset), request_id=request.state.request_id)
