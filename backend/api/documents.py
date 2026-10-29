from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse

from backend.api.schemas import DocumentMetadata, DocumentExtraction, SuccessResponse
from backend.auth.security import current_identity, settings_for_request
from backend.core.config import Settings
from backend.services import document_service as service

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("", status_code=201, response_model=SuccessResponse[DocumentMetadata])
def upload(request: Request, file: UploadFile = File(...), user: dict = Depends(current_identity),
           settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.upload_document(settings, file, user), request_id=request.state.request_id)


@router.get("", response_model=SuccessResponse[list[DocumentMetadata]])
def list_files(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
               user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.list_documents(settings, user, limit=limit, offset=offset), request_id=request.state.request_id)


@router.get("/{document_id}", response_model=SuccessResponse[DocumentMetadata])
def detail(document_id: UUID, request: Request, user: dict = Depends(current_identity),
           settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.get_document(settings, document_id, user), request_id=request.state.request_id)


@router.get("/{document_id}/content", response_class=FileResponse)
def content(document_id: UUID, user: dict = Depends(current_identity), settings: Settings = Depends(settings_for_request)):
    path, filename, media = service.get_content(settings, document_id, user)
    return FileResponse(path, media_type=media, filename=filename,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/{document_id}/extraction", response_model=SuccessResponse[DocumentExtraction])
def extraction(document_id: UUID, request: Request, user: dict = Depends(current_identity),
               settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=service.get_extraction(settings, document_id, user), request_id=request.state.request_id)
