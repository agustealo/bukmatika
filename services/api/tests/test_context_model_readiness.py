from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import Principal
from bukmatika.personalization.context import ContextAssembler
from bukmatika.personalization.domain import (
    ContextRequest,
    ContextTask,
    PersonalizationSettingsUpdate,
)
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"readiness-context-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


async def test_reader_context_advertises_research_answer_only_when_runtime_ready(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "ready")
    calls = 0

    async def ready() -> bool:
        nonlocal calls
        calls += 1
        return True

    manifest = await ContextAssembler(
        session_scope_factory=_scope(session),
        research_answer_availability=ready,
    ).assemble(
        principal_id=principal.id,
        request=ContextRequest(task=ContextTask.READER),
    )

    assert calls == 1
    assert "research.answer" in manifest.available_capabilities


async def test_reader_context_hides_research_answer_when_runtime_not_ready(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "not-ready")
    calls = 0

    async def not_ready() -> bool:
        nonlocal calls
        calls += 1
        return False

    manifest = await ContextAssembler(
        session_scope_factory=_scope(session),
        research_answer_availability=not_ready,
    ).assemble(
        principal_id=principal.id,
        request=ContextRequest(task=ContextTask.RESEARCH),
    )

    assert calls == 1
    assert "research.answer" not in manifest.available_capabilities
    assert manifest.available_capabilities == ["research.search", "reader.open"]


async def test_ai_disabled_context_never_probes_runtime_readiness(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "disabled")
    await PersonalizationService(session_scope_factory=_scope(session)).update_settings(
        principal_id=principal.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=False,
            learning_enabled=True,
            autonomy_level=0,
        ),
    )
    calls = 0

    async def must_not_run() -> bool:
        nonlocal calls
        calls += 1
        raise AssertionError("AI-off context must not probe the model runtime")

    manifest = await ContextAssembler(
        session_scope_factory=_scope(session),
        research_answer_availability=must_not_run,
    ).assemble(
        principal_id=principal.id,
        request=ContextRequest(task=ContextTask.READER),
    )

    assert calls == 0
    assert manifest.ai_enabled is False
    assert manifest.available_capabilities == []
    assert manifest.exclusion_reasons == ["ai_disabled"]
