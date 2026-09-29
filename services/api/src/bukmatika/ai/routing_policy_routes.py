from typing import Annotated

from fastapi import APIRouter, Depends

from bukmatika.ai.routing_policy import (
    AIRoutingPolicyResponse,
    AIRoutingPolicyService,
    AIRoutingPolicyUpdate,
)
from bukmatika.identity import AuthenticatedPrincipal, require_principal

router = APIRouter(tags=["ai-routing-policy"])
_service = AIRoutingPolicyService()


def routing_policy_service() -> AIRoutingPolicyService:
    return _service


@router.get("/routing-policy", response_model=AIRoutingPolicyResponse)
async def get_routing_policy(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[AIRoutingPolicyService, Depends(routing_policy_service)],
) -> AIRoutingPolicyResponse:
    return await service.get(principal_id=identity.principal_id)


@router.put("/routing-policy", response_model=AIRoutingPolicyResponse)
async def update_routing_policy(
    request: AIRoutingPolicyUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[AIRoutingPolicyService, Depends(routing_policy_service)],
) -> AIRoutingPolicyResponse:
    return await service.update(
        principal_id=identity.principal_id,
        request=request,
    )
