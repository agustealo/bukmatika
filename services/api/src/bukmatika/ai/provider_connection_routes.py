from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from bukmatika.ai.provider_connection_domain import (
    ModelAssignmentResponse,
    ModelAssignmentUpdate,
    ModelRole,
    ProviderCatalogResponse,
    ProviderConnectionCreate,
    ProviderConnectionListResponse,
    ProviderConnectionResponse,
    ProviderConnectionUpdate,
    ProviderCredentialUpdate,
)
from bukmatika.ai.provider_connections import (
    ProviderCapabilityUnavailable,
    ProviderConnectionNotFound,
    ProviderConnectionService,
    ProviderCredentialNotAllowed,
    ProviderNotRegistered,
)
from bukmatika.identity import AuthenticatedPrincipal, require_principal

router = APIRouter(tags=["ai-provider-connections"])
_service = ProviderConnectionService()


def provider_connection_service() -> ProviderConnectionService:
    return _service


@router.get("/providers", response_model=ProviderCatalogResponse)
async def provider_catalog(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ProviderConnectionService, Depends(provider_connection_service)],
) -> ProviderCatalogResponse:
    del identity
    return service.catalog()


@router.get("/provider-connections", response_model=ProviderConnectionListResponse)
async def list_provider_connections(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ProviderConnectionService, Depends(provider_connection_service)],
) -> ProviderConnectionListResponse:
    return await service.list_connections(principal_id=identity.principal_id)


@router.post(
    "/provider-connections",
    response_model=ProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider_connection(
    request: ProviderConnectionCreate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ProviderConnectionService, Depends(provider_connection_service)],
) -> ProviderConnectionResponse:
    try:
        return await service.create_connection(
            principal_id=identity.principal_id,
            request=request,
        )
    except ProviderNotRegistered as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "AI_PROVIDER_NOT_REGISTERED"},
        ) from exc


@router.patch(
    "/provider-connections/{connection_id}",
    response_model=ProviderConnectionResponse,
)
async def update_provider_connection(
    connection_id: UUID,
    request: ProviderConnectionUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ProviderConnectionService, Depends(provider_connection_service)],
) -> ProviderConnectionResponse:
    try:
        return await service.update_connection(
            principal_id=identity.principal_id,
            connection_id=connection_id,
            request=request,
        )
    except ProviderConnectionNotFound as exc:
        raise _not_found() from exc


@router.delete(
    "/provider-connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def disconnect_provider_connection(
    connection_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ProviderConnectionService, Depends(provider_connection_service)],
) -> Response:
    try:
        await service.disconnect(
            principal_id=identity.principal_id,
            connection_id=connection_id,
        )
    except ProviderConnectionNotFound as exc:
        raise _not_found() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/provider-connections/{connection_id}/credential",
    response_model=ProviderConnectionResponse,
)
async def replace_provider_credential(
    connection_id: UUID,
    request: ProviderCredentialUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ProviderConnectionService, Depends(provider_connection_service)],
) -> ProviderConnectionResponse:
    try:
        return await service.replace_credential(
            principal_id=identity.principal_id,
            connection_id=connection_id,
            request=request,
        )
    except ProviderConnectionNotFound as exc:
        raise _not_found() from exc
    except ProviderCredentialNotAllowed as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "AI_PROVIDER_CREDENTIAL_NOT_ALLOWED"},
        ) from exc


@router.delete(
    "/provider-connections/{connection_id}/credential",
    response_model=ProviderConnectionResponse,
)
async def delete_provider_credential(
    connection_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ProviderConnectionService, Depends(provider_connection_service)],
) -> ProviderConnectionResponse:
    try:
        return await service.delete_credential(
            principal_id=identity.principal_id,
            connection_id=connection_id,
        )
    except ProviderConnectionNotFound as exc:
        raise _not_found() from exc


@router.put(
    "/model-assignments/{role}",
    response_model=ModelAssignmentResponse,
)
async def assign_model_role(
    role: ModelRole,
    request: ModelAssignmentUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ProviderConnectionService, Depends(provider_connection_service)],
) -> ModelAssignmentResponse:
    try:
        return await service.assign_model(
            principal_id=identity.principal_id,
            role=role,
            request=request,
        )
    except ProviderConnectionNotFound as exc:
        raise _not_found() from exc
    except ProviderNotRegistered as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "AI_PROVIDER_NOT_REGISTERED"},
        ) from exc
    except ProviderCapabilityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "AI_MODEL_CAPABILITY_UNAVAILABLE"},
        ) from exc


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "AI_PROVIDER_CONNECTION_NOT_FOUND"},
    )
