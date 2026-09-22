from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.configuration import (
    LocalModelConfigurationUpdate,
    ModelConfigurationMode,
    ModelConfigurationSource,
    PrincipalModelRuntimeResolver,
)
from bukmatika.ai.gateway import ModelReadinessState
from bukmatika.ai.routes import local_model_inventory
from bukmatika.config import Settings
from bukmatika.main import app
from bukmatika.persistence.models import InteractionEvent, Principal
from bukmatika.persistence.personalization_models import UserModel
from bukmatika.personalization.portability import PersonalizationPortabilityService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"ai-config-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


class _DisabledProfileService:
    async def profile(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return SimpleNamespace(ai_enabled=False)


class _ProbeForbiddenResolver:
    async def installed_models(self):  # type: ignore[no-untyped-def]
        raise AssertionError("AI-off model inventory must not probe the local runtime")


async def test_profile_model_selection_isolated_and_overrides_installation_default(
    session: AsyncSession,
) -> None:
    first = await _principal(session, "first")
    second = await _principal(session, "second")
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = PrincipalModelRuntimeResolver(
            settings=Settings(model_provider="ollama", ollama_model="llama3.2:latest"),
            client=client,
            session_scope_factory=_scope(session),
        )

        second_before = await resolver.configuration(principal_id=second.id)
        assert second_before.mode is ModelConfigurationMode.INSTALLATION_DEFAULT
        assert second_before.source is ModelConfigurationSource.INSTALLATION
        assert second_before.effective_model == "llama3.2:latest"

        first_config = await resolver.update_configuration(
            principal_id=first.id,
            update=LocalModelConfigurationUpdate(
                mode=ModelConfigurationMode.OLLAMA,
                model="qwen3:8b",
            ),
        )
        assert first_config.mode is ModelConfigurationMode.OLLAMA
        assert first_config.source is ModelConfigurationSource.PROFILE
        assert first_config.effective_model == "qwen3:8b"

        first_runtime = await resolver.resolve(principal_id=first.id)
        second_runtime = await resolver.resolve(principal_id=second.id)
        assert first_runtime.gateway.identity is not None
        assert first_runtime.gateway.identity.model == "qwen3:8b"
        assert second_runtime.gateway.identity is not None
        assert second_runtime.gateway.identity.model == "llama3.2:latest"

        disabled = await resolver.update_configuration(
            principal_id=first.id,
            update=LocalModelConfigurationUpdate(mode=ModelConfigurationMode.DISABLED),
        )
        assert disabled.effective_provider is None
        assert (await resolver.resolve(principal_id=first.id)).gateway.identity is None
        assert (await resolver.resolve(principal_id=second.id)).gateway.identity is not None

    stored_first = await session.scalar(
        select(UserModel).where(UserModel.principal_id == first.id)
    )
    stored_second = await session.scalar(
        select(UserModel).where(UserModel.principal_id == second.id)
    )
    assert stored_first is not None
    assert stored_first.model_provider_override == "none"
    assert stored_first.model_name_override is None
    assert stored_second is not None
    assert stored_second.model_provider_override is None
    assert stored_second.model_name_override is None


async def test_reset_returns_profile_to_installation_default_and_export_includes_override(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "portability")
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = PrincipalModelRuntimeResolver(
            settings=Settings(model_provider="none"),
            client=client,
            session_scope_factory=_scope(session),
        )
        await resolver.update_configuration(
            principal_id=principal.id,
            update=LocalModelConfigurationUpdate(
                mode=ModelConfigurationMode.OLLAMA,
                model="gemma3:4b",
            ),
        )

    portability = PersonalizationPortabilityService(session_scope_factory=_scope(session))
    exported = await portability.export(principal_id=principal.id)
    assert exported.schema_version == 1
    assert exported.user_model.model_provider_override == "ollama"
    assert exported.user_model.model_name_override == "gemma3:4b"

    reset = await portability.reset(principal_id=principal.id)
    assert reset.model_provider_override is None
    assert reset.model_name_override is None


async def test_model_configuration_records_sanitized_semantic_event(session: AsyncSession) -> None:
    principal = await _principal(session, "event")
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = PrincipalModelRuntimeResolver(
            settings=Settings(model_provider="none"),
            client=client,
            session_scope_factory=_scope(session),
        )
        await resolver.update_configuration(
            principal_id=principal.id,
            update=LocalModelConfigurationUpdate(
                mode=ModelConfigurationMode.OLLAMA,
                model="qwen3:8b",
            ),
        )

    event = await session.scalar(
        select(InteractionEvent).where(
            InteractionEvent.principal_id == principal.id,
            InteractionEvent.event_type == "personalization.model_configuration_updated",
        )
    )
    assert event is not None
    assert event.context == {
        "mode": "ollama",
        "provider": "ollama",
        "model": "qwen3:8b",
    }


async def test_installed_model_inventory_is_loopback_metadata_only() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "qwen3:8b", "model": "qwen3:8b"},
                    {"name": "llama3.2:latest", "model": "llama3.2:latest"},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = PrincipalModelRuntimeResolver(
            settings=Settings(
                model_provider="none",
                ollama_base_url="http://127.0.0.1:11434",
                model_readiness_timeout_seconds=1,
            ),
            client=client,
        )
        inventory = await resolver.installed_models()

    assert inventory.state is ModelReadinessState.READY
    assert inventory.models == ["llama3.2:latest", "qwen3:8b"]
    assert captured == {
        "method": "GET",
        "url": "http://127.0.0.1:11434/api/tags",
        "body": b"",
    }


async def test_ai_off_inventory_route_does_not_probe_runtime() -> None:
    with pytest.raises(HTTPException) as captured:
        await local_model_inventory(
            identity=SimpleNamespace(principal_id=uuid4()),  # type: ignore[arg-type]
            resolver=_ProbeForbiddenResolver(),  # type: ignore[arg-type]
            profile_service=_DisabledProfileService(),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == {"code": "AI_DISABLED"}


@pytest.mark.parametrize(
    "model",
    ["", "   ", "bad model", "../../escape", "model?query", "model#fragment"],
)
def test_model_configuration_rejects_invalid_model_names(model: str) -> None:
    with pytest.raises(ValidationError):
        LocalModelConfigurationUpdate(
            mode=ModelConfigurationMode.OLLAMA,
            model=model,
        )


def test_local_ai_configuration_routes_are_mounted() -> None:
    paths = set(app.openapi()["paths"])
    assert "/v1/ai/configuration" in paths
    assert "/v1/ai/local/models" in paths
