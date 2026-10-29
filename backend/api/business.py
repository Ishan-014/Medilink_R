from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import Field, JsonValue

from backend.api.business_schemas import (OnboardingStart, OnboardingUpdate, ClaimStart, ClaimUpdate,
                                         CaseStatus, PatientProfile, ClaimRecord, AppointmentCreate, AppointmentRecord)
from backend.api.integrations import hospital_client
from backend.api.schemas import Contract, Role, SuccessResponse
from backend.auth.security import current_identity, require_roles, settings_for_request
from backend.core.config import Settings
from backend.integrations.client import HospitalClient
from backend.services import business_service as service

router = APIRouter(prefix="/api", tags=["onboarding and claims"])
Key = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]


class AdvanceInput(Contract):
    expected_version: int = Field(ge=0, strict=True)


class EvidenceRecord(Contract):
    operation: str
    tool_call_id: UUID
    facts: dict[str, JsonValue]


@router.post("/onboarding", status_code=201, response_model=SuccessResponse[CaseStatus])
def onboarding(body: OnboardingStart, request: Request, response: Response, idempotency_key: Key = None,
               user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    case, replayed = service.start(settings, user, body, idempotency_key, "onboarding")
    if replayed:
        response.status_code = 200
    return SuccessResponse(data=case, request_id=request.state.request_id)


@router.post("/claims", status_code=201, response_model=SuccessResponse[CaseStatus])
def claim(body: ClaimStart, request: Request, response: Response, idempotency_key: Key = None,
          user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    case, replayed = service.start(settings, user, body, idempotency_key, "insurance_claim")
    if replayed:
        response.status_code = 200
    return SuccessResponse(data=case, request_id=request.state.request_id)


@router.get("/workflows/{workflow_id}/case", response_model=SuccessResponse[CaseStatus])
def status(workflow_id: UUID, request: Request, user: dict = Depends(current_identity),
           settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.get_status(settings, str(workflow_id), user), request_id=request.state.request_id)


@router.patch("/onboarding/{workflow_id}", response_model=SuccessResponse[CaseStatus])
def update_onboarding(workflow_id: UUID, body: OnboardingUpdate, request: Request, user: dict = Depends(current_identity),
                      settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.update_intake(settings, str(workflow_id), user, body, "onboarding"), request_id=request.state.request_id)


@router.patch("/claims/{workflow_id}/intake", response_model=SuccessResponse[CaseStatus])
def update_claim(workflow_id: UUID, body: ClaimUpdate, request: Request, user: dict = Depends(current_identity),
                 settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.update_intake(settings, str(workflow_id), user, body, "insurance_claim"), request_id=request.state.request_id)


@router.post("/workflows/{workflow_id}/advance", response_model=SuccessResponse[CaseStatus])
async def advance(workflow_id: UUID, body: AdvanceInput, request: Request,
                  user: dict = Depends(require_roles(Role.DOCTOR, Role.ADMIN)), settings: Settings = Depends(settings_for_request),
                  client: HospitalClient = Depends(hospital_client)):
    data = await service.advance(settings, client, str(workflow_id), user, body.expected_version)
    return SuccessResponse(data=data, request_id=request.state.request_id)


@router.get("/patients/{patient_id}", response_model=SuccessResponse[PatientProfile])
def patient(patient_id: UUID, request: Request,
            user: dict = Depends(require_roles(Role.PATIENT, Role.DOCTOR, Role.ADMIN, Role.RECEPTIONIST)),
            settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.read_entity(settings, "patient_profiles", patient_id, user), request_id=request.state.request_id)


@router.get("/workflows/{workflow_id}/evidence", response_model=SuccessResponse[list[EvidenceRecord]])
def evidence(workflow_id: UUID, request: Request,
             user: dict = Depends(require_roles(Role.PATIENT, Role.DOCTOR, Role.ADMIN, Role.RECEPTIONIST)),
             settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.evidence(settings, str(workflow_id), user), request_id=request.state.request_id)


@router.get("/claims/{claim_id}", response_model=SuccessResponse[ClaimRecord])
def claim_record(claim_id: UUID, request: Request, user: dict = Depends(require_roles(Role.PATIENT, Role.DOCTOR, Role.ADMIN)),
                 settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.read_entity(settings, "claims", claim_id, user), request_id=request.state.request_id)


@router.post("/appointments", status_code=201, response_model=SuccessResponse[AppointmentRecord])
def appointment(body: AppointmentCreate, request: Request,
                user: dict = Depends(require_roles(Role.PATIENT, Role.DOCTOR, Role.ADMIN, Role.RECEPTIONIST)),
                settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.create_appointment(settings, body, user), request_id=request.state.request_id)


@router.get("/appointments/{appointment_id}", response_model=SuccessResponse[AppointmentRecord])
def appointment_record(appointment_id: UUID, request: Request,
                       user: dict = Depends(require_roles(Role.PATIENT, Role.DOCTOR, Role.ADMIN, Role.RECEPTIONIST)),
                       settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.read_entity(settings, "appointments", appointment_id, user), request_id=request.state.request_id)
