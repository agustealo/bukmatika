from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.configuration import PrincipalModelRuntimeResolver
from bukmatika.ai.gateway import ModelDataClassification, ModelRequest, ModelTask
from bukmatika.ai.provider_connection_domain import (
    ModelAssignmentUpdate,
    ModelRole,
    ProviderConnectionCreate,
    ProviderCredentialUpdate,
)
from bukmatika.ai.provider_connections import ProviderConnectionService
from bukmatika.ai.routing_policy import ModelProviderPolicyDenied
from bukmatika.config import Settings
from bukmatika.persistence.models import Principal
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.routing_policy import AIRoutingPolicyRepository


class _Answer(BaseModel):
    value: str


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"openai-runtime-{suffix}-{uuid4()}")
    session.add(principal)
    await session.flush()
    return principal


def _session_scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


def _request(classification: ModelDataClassification) -> ModelRequest:
    return ModelRequest(
        task=ModelTask.RUNTIME_SMOKE,
        payload={"probe": "provider-neutral"},
        data_classification=classification,
        max_output_tokens=64,
        timeout_seconds=3,
    )


async def _configured_openai_profile(
    session: AsyncSession,
    tmp_path: Path,
    *,
    cloud_policy: str,
) -> tuple[Principal, Settings]:
    principal = await _principal(session, cloud_policy)
    settings = Settings(credential_key_path=tmp_path / f"{cloud_policy}.key")
    personalization = PersonalizationRepository(session)
    user_model = await personalization.get_or_create_user_model(principal.id)
    user_model.ai_enabled = True

    service = ProviderConnectionService(
        settings=settings,
        session_scope_factory=_session_scope(session),
    )
    connection = await service.create_connection(
        principal_id=principal.id,
        request=ProviderConnectionCreate(provider_id="openai", display_name="OpenAI test"),
    )
    await service.replace_credential(
        principal_id=principal.id,
        connection_id=connection.connection_id,
        request=ProviderCredentialUpdate(secret="sk-runtime-secret"),
    )
    await service.assign_model(
        principal_id=principal.id,
        role=ModelRole.PRIMARY,
        request=ModelAssignmentUpdate(
            connection_id=connection.connection_id,
            model_id="gpt-6-luna",
        ),
    )
    policy = await AIRoutingPolicyRepository(session).get_or_create(principal_id=principal.id)
    policy.model_selection_mode = "profile"
    policy.cloud_egress_policy = cloud_policy
    await session.flush()
    return principal, settings


async def test_public_only_profile_blocks_private_openai_egress_then_allows_public(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal, settings = await _configured_openai_profile(
        session,
        tmp_path,
        cloud_policy="public_only",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": '{"value":"ok"}'}],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        runtime = await PrincipalModelRuntimeResolver(
            settings=settings,
            client=client,
            session_scope_factory=_session_scope(session),
        ).resolve(principal_id=principal.id)

        assert runtime.gateway.identity is not None
        assert runtime.gateway.identity.provider == "openai"
        assert runtime.gateway.identity.model == "gpt-6-luna"
        assert runtime.gateway.identity.routing == "cloud"

        with pytest.raises(ModelProviderPolicyDenied):
            await runtime.gateway.generate_structured(
                _request(ModelDataClassification.PRIVATE_USER_CONTEXT),
                _Answer,
            )
        assert requests == []

        answer = await runtime.gateway.generate_structured(
            _request(ModelDataClassification.PUBLIC),
            _Answer,
        )

    assert answer.value == "ok"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.openai.com/v1/responses"
    assert requests[0].headers["authorization"] == "Bearer sk-runtime-secret"
    assert "sk-runtime-secret" not in requests[0].content.decode("utf-8")


async def test_local_only_profile_denies_openai_before_provider_io(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal, settings = await _configured_openai_profile(
        session,
        tmp_path,
        cloud_policy="local_only",
    )
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        del request
        requests += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        runtime = await PrincipalModelRuntimeResolver(
            settings=settings,
            client=client,
            session_scope_factory=_session_scope(session),
        ).resolve(principal_id=principal.id)
        readiness = await runtime.gateway.readiness()
        assert readiness.ready is False
        with pytest.raises(ModelProviderPolicyDenied):
            await runtime.gateway.generate_structured(
                _request(ModelDataClassification.PUBLIC),
                _Answer,
            )

    assert requests == 0
