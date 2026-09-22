from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import LibraryEntry, Principal, Work
from bukmatika.persistence.personalization import ContextGoalDenied, ContextSelectionDenied
from bukmatika.persistence.personalization_models import Goal, PreferenceClaim
from bukmatika.personalization.context import ContextAssembler
from bukmatika.personalization.domain import (
    ContextRequest,
    ContextScope,
    ContextTask,
    ExplicitPreferenceRequest,
    PersonalizationSettingsUpdate,
    PreferenceKey,
    PreferenceScopeType,
)
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"context-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


async def test_ai_disabled_short_circuits_private_context_material(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "disabled")
    service = PersonalizationService(session_scope_factory=_scope(session))
    await service.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.SUBJECT_INTERESTS,
            value={"subjects": ["Atlantic history"]},
        ),
    )
    await service.update_settings(
        principal_id=principal.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=False,
            learning_enabled=True,
            autonomy_level=0,
        ),
    )

    manifest = await ContextAssembler(session_scope_factory=_scope(session)).assemble(
        principal_id=principal.id,
        request=ContextRequest(
            task=ContextTask.RESEARCH,
            goal_id=uuid4(),
            library_entry_ids=[uuid4()],
        ),
    )

    assert manifest.ai_enabled is False
    assert manifest.model_context_ready is False
    assert manifest.preferences == []
    assert manifest.goal is None
    assert manifest.library_entries == []
    assert manifest.available_capabilities == []
    assert manifest.exclusion_reasons == ["ai_disabled"]


async def test_context_prefers_exact_scope_then_domain_then_global_without_mutation(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "precedence")
    service = PersonalizationService(session_scope_factory=_scope(session))
    global_claim = await service.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.FORMAT_PREFERRED,
            value={"format": "PDF"},
        ),
    )
    domain_claim = await service.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.FORMAT_PREFERRED,
            value={"format": "EPUB"},
            scope_type=PreferenceScopeType.RESEARCH,
        ),
    )
    exact_claim = await service.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.FORMAT_PREFERRED,
            value={"format": "TXT"},
            scope_type=PreferenceScopeType.RESEARCH,
            scope_value="atlantic-contact",
        ),
    )
    citation_claim = await service.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "Chicago"},
        ),
    )

    assembler = ContextAssembler(session_scope_factory=_scope(session))
    exact = await assembler.assemble(
        principal_id=principal.id,
        request=ContextRequest(
            task=ContextTask.RESEARCH,
            scopes=[
                ContextScope(
                    scope_type=PreferenceScopeType.RESEARCH,
                    scope_value="atlantic-contact",
                )
            ],
        ),
    )
    broad = await assembler.assemble(
        principal_id=principal.id,
        request=ContextRequest(task=ContextTask.RESEARCH),
    )
    discovery = await assembler.assemble(
        principal_id=principal.id,
        request=ContextRequest(task=ContextTask.DISCOVERY),
    )

    exact_by_key = {item.key: item for item in exact.preferences}
    broad_by_key = {item.key: item for item in broad.preferences}
    discovery_by_key = {item.key: item for item in discovery.preferences}

    assert exact_by_key[PreferenceKey.FORMAT_PREFERRED].claim_id == exact_claim.claim_id
    assert exact_by_key[PreferenceKey.FORMAT_PREFERRED].value == {"format": "TXT"}
    assert "matched" in exact_by_key[PreferenceKey.FORMAT_PREFERRED].inclusion_reason
    assert exact_by_key[PreferenceKey.CITATION_STYLE].claim_id == citation_claim.claim_id

    assert broad_by_key[PreferenceKey.FORMAT_PREFERRED].claim_id == domain_claim.claim_id
    assert broad_by_key[PreferenceKey.FORMAT_PREFERRED].value == {"format": "EPUB"}
    assert discovery_by_key[PreferenceKey.FORMAT_PREFERRED].claim_id == global_claim.claim_id
    assert discovery_by_key[PreferenceKey.FORMAT_PREFERRED].value == {"format": "PDF"}

    claims = list(
        (
            await session.scalars(
                select(PreferenceClaim).where(PreferenceClaim.principal_id == principal.id)
            )
        ).all()
    )
    assert len(claims) == 4
    assert {claim.status for claim in claims} == {"active"}


async def test_context_goal_must_be_active_and_owned(session: AsyncSession) -> None:
    owner = await _principal(session, "goal-owner")
    other = await _principal(session, "goal-other")
    owned_goal = Goal(
        principal_id=owner.id,
        title="Compare early Atlantic contact accounts",
        kind="research",
        status="active",
        scope={"period": "pre-1492"},
        constraints={"sources": "primary"},
    )
    foreign_goal = Goal(
        principal_id=other.id,
        title="Private other-user goal",
        kind="research",
        status="active",
        scope={},
        constraints={},
    )
    session.add_all([owned_goal, foreign_goal])
    await session.flush()
    assembler = ContextAssembler(session_scope_factory=_scope(session))

    manifest = await assembler.assemble(
        principal_id=owner.id,
        request=ContextRequest(task=ContextTask.RESEARCH, goal_id=owned_goal.id),
    )
    assert manifest.goal is not None
    assert manifest.goal.goal_id == owned_goal.id
    assert manifest.goal.title == owned_goal.title

    with pytest.raises(ContextGoalDenied):
        await assembler.assemble(
            principal_id=owner.id,
            request=ContextRequest(task=ContextTask.RESEARCH, goal_id=foreign_goal.id),
        )


async def test_context_library_selections_are_principal_owned(session: AsyncSession) -> None:
    owner = await _principal(session, "library-owner")
    other = await _principal(session, "library-other")
    owner_work = Work(canonical_title="Owned Work", normalized_title="owned work")
    foreign_work = Work(canonical_title="Foreign Work", normalized_title="foreign work")
    session.add_all([owner_work, foreign_work])
    await session.flush()
    owner_entry = LibraryEntry(
        principal_id=owner.id,
        work_id=owner_work.id,
        status="saved",
    )
    foreign_entry = LibraryEntry(
        principal_id=other.id,
        work_id=foreign_work.id,
        status="saved",
    )
    session.add_all([owner_entry, foreign_entry])
    await session.flush()
    assembler = ContextAssembler(session_scope_factory=_scope(session))

    manifest = await assembler.assemble(
        principal_id=owner.id,
        request=ContextRequest(
            task=ContextTask.RESEARCH,
            library_entry_ids=[owner_entry.id],
        ),
    )
    assert len(manifest.library_entries) == 1
    assert manifest.library_entries[0].library_entry_id == owner_entry.id
    assert manifest.library_entries[0].title == "Owned Work"
    assert manifest.library_entries[0].document_ids == []

    with pytest.raises(ContextSelectionDenied):
        await assembler.assemble(
            principal_id=owner.id,
            request=ContextRequest(
                task=ContextTask.RESEARCH,
                library_entry_ids=[foreign_entry.id],
            ),
        )


async def test_context_capabilities_are_task_bounded(session: AsyncSession) -> None:
    principal = await _principal(session, "capabilities")
    assembler = ContextAssembler(session_scope_factory=_scope(session))

    research = await assembler.assemble(
        principal_id=principal.id,
        request=ContextRequest(task=ContextTask.RESEARCH),
    )
    discovery = await assembler.assemble(
        principal_id=principal.id,
        request=ContextRequest(task=ContextTask.DISCOVERY),
    )

    assert research.available_capabilities == ["research.search", "reader.open"]
    assert discovery.available_capabilities == ["discovery.search", "catalog.search"]
    assert research.model_context_ready is True
    assert discovery.model_context_ready is True
