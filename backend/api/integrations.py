from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from backend.api.schemas import Contract, Role, SuccessResponse
from backend.auth.security import current_identity, require_roles, settings_for_request
from backend.core.config import Settings
from backend.integrations.client import HospitalClient
from backend.integrations.config import SERVER_NAMES, ServerName, load_config, configured_server
from backend.integrations.policy import READ_TOOLS
from backend.services.tool_call_service import ToolCallInput, ToolCallOutput, ToolCallRecord, execute, list_calls

router = APIRouter(prefix="/api", tags=["hospital integrations"])


def hospital_client(request: Request) -> HospitalClient:
    return request.app.state.hospital_client


class IntegrationInfo(Contract):
    name: ServerName
    configured: bool
    transport: Literal["stdio", "streamable_http"] | None


class IntegrationHealth(Contract):
    name: ServerName
    status: Literal["healthy"] = "healthy"
    available_read_tools: list[str]


@router.get("/integrations", response_model=SuccessResponse[list[IntegrationInfo]])
def integrations(request: Request, user: dict = Depends(require_roles(Role.ADMIN, Role.AUDITOR)),
                 settings: Settings = Depends(settings_for_request)):
    servers = load_config(settings).servers
    return SuccessResponse(data=[IntegrationInfo(name=name, configured=name in servers,
                                                transport=servers[name].transport if name in servers else None)
                                 for name in SERVER_NAMES], request_id=request.state.request_id)


@router.get("/integrations/{name}/health", response_model=SuccessResponse[IntegrationHealth])
async def health(name: ServerName, request: Request, user: dict = Depends(require_roles(Role.ADMIN, Role.AUDITOR)),
                 settings: Settings = Depends(settings_for_request), client: HospitalClient = Depends(hospital_client)):
    server = configured_server(settings, name)
    tools = await client.health(server)
    return SuccessResponse(data=IntegrationHealth(name=name, available_read_tools=sorted(set(tools) & READ_TOOLS[name])),
                           request_id=request.state.request_id)


@router.post("/workflows/{workflow_id}/tool-calls", response_model=SuccessResponse[ToolCallOutput])
async def call_tool(workflow_id: UUID, body: ToolCallInput, request: Request,
                    user: dict = Depends(require_roles(Role.DOCTOR, Role.ADMIN)),
                    settings: Settings = Depends(settings_for_request), client: HospitalClient = Depends(hospital_client)):
    data = await execute(settings, client, str(workflow_id), user, body)
    return SuccessResponse(data=data, request_id=request.state.request_id)


@router.get("/workflows/{workflow_id}/tool-calls", response_model=SuccessResponse[list[ToolCallRecord]])
def calls(workflow_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
          user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=list_calls(settings, str(workflow_id), user, limit, offset), request_id=request.state.request_id)
