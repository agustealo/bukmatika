from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelGateway,
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelReadinessState,
    ModelRequest,
    StructuredResponseT,
)
from bukmatika.ai.provider_registry import RoutingType
from bukmatika.persistence import session_scope
from bukmatika.persistence.personalization import (
    PersonalizationRepository,
    lock_personalization_state,
)
from bukmatika.persistence.routing_policy import AIRoutingPolicyRepository


class ModelSelectionMode(StrEnum):
    INSTALLATION_DEFAULT = "installation_default"
    PROFILE = "profile"


class CloudEgressPolicy(StrEnum):
    LOCAL_ONLY = "local_only"
    PUBLIC_ONLY = "public_only"
    PRIVATE_CONTEXT = "private_context"


class AIRoutingPolicyResponse(BaseModel):
    ai_enabled: bool
    model_selection_mode: ModelSelectionMode
    cloud_egress_policy: CloudEgressPolicy


class AIRoutingPolicyUpdate(BaseModel):
    ai_enabled: bool
    model_selection_mode: ModelSelectionMode
    cloud_egress_policy: CloudEgressPolicy


class ModelProviderPolicyDenied(RuntimeError):
    code = "MODEL_PROVIDER_POLICY_DENIED"


class PolicyEnforcedModelGateway:
    """Fail-closed wrapper that authorizes external egress before provider I/O."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        routing_type: RoutingType,
        cloud_egress_policy: CloudEgressPolicy,
    ) -> None:
        self._gateway = gateway
        self._routing_type = routing_type
        self._cloud_egress_policy = cloud_egress_policy

    @property
    def identity(self) -> ModelProviderIdentity | None:
        return self._gateway.identity

    async def readiness(self) -> ModelProviderReadiness:
        if (
            self._routing_type is RoutingType.CLOUD
            and self._cloud_egress_policy is CloudEgressPolicy.LOCAL_ONLY
        ):
            return ModelProviderReadiness(
                state=ModelReadinessState.UNCONFIGURED,
                configured=True,
                ready=False,
                identity=self.identity,
            )
        return await self._gateway.readiness()

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        self._authorize(request.data_classification)
        return await self._gateway.generate_structured(request, response_type)

    def _authorize(self, classification: ModelDataClassification) -> None:
        if self._routing_type is RoutingType.LOCAL:
            return
        if self._cloud_egress_policy is CloudEgressPolicy.LOCAL_ONLY:
            raise ModelProviderPolicyDenied("Cloud model egress is disabled by routing policy")
        if (
            self._cloud_egress_policy is CloudEgressPolicy.PUBLIC_ONLY
            and classification is not ModelDataClassification.PUBLIC
        ):
            raise ModelProviderPolicyDenied(
                "Private user context is not authorized for cloud model egress"
            )


class AIRoutingPolicyService:
    async def get(self, *, principal_id: UUID) -> AIRoutingPolicyResponse:
        async with session_scope() as database_session:
            user_model = await PersonalizationRepository(database_session).get_or_create_user_model(
                principal_id
            )
            policy = await AIRoutingPolicyRepository(database_session).get_or_create(
                principal_id=principal_id
            )
            return AIRoutingPolicyResponse(
                ai_enabled=user_model.ai_enabled,
                model_selection_mode=ModelSelectionMode(policy.model_selection_mode),
                cloud_egress_policy=CloudEgressPolicy(policy.cloud_egress_policy),
            )

    async def update(
        self,
        *,
        principal_id: UUID,
        request: AIRoutingPolicyUpdate,
    ) -> AIRoutingPolicyResponse:
        async with session_scope() as database_session:
            await lock_personalization_state(database_session, principal_id)
            personalization = PersonalizationRepository(database_session)
            user_model = await personalization.get_or_create_user_model(principal_id)
            user_model.ai_enabled = request.ai_enabled
            policy_repository = AIRoutingPolicyRepository(database_session)
            policy = await policy_repository.get_or_create(principal_id=principal_id)
            policy.model_selection_mode = request.model_selection_mode.value
            policy.cloud_egress_policy = request.cloud_egress_policy.value
            await database_session.flush()
            return AIRoutingPolicyResponse(
                ai_enabled=user_model.ai_enabled,
                model_selection_mode=ModelSelectionMode(policy.model_selection_mode),
                cloud_egress_policy=CloudEgressPolicy(policy.cloud_egress_policy),
            )


__all__ = [
    "AIRoutingPolicyResponse",
    "AIRoutingPolicyService",
    "AIRoutingPolicyUpdate",
    "CloudEgressPolicy",
    "ModelProviderPolicyDenied",
    "ModelSelectionMode",
    "PolicyEnforcedModelGateway",
]
