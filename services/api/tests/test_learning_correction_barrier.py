from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.models import (
    Asset,
    Edition,
    InteractionEvent,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.persistence.personalization_models import PreferenceClaim
from bukmatika.personalization.learning import LearningService, OutcomeCreate, OutcomeValue


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession) -> Principal:
    principal = Principal(kind="local", external_subject="learning-correction-barrier")
    session.add(principal)
    await session.flush()
    return principal


async def _record_open(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    occurred_at: datetime,
) -> InteractionEvent:
    work = Work(
        canonical_title=f"Correction Work {suffix}",
        normalized_title=f"correction work {suffix}",
    )
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/correction/{suffix}",
        byte_size=128,
        media_type="application/pdf",
    )
    session.add_all([work, stored])
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title=f"Correction Edition {suffix}",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="PDF",
        media_type="application/pdf",
        remote_url=f"https://example.org/{suffix}.pdf",
        stored_object_id=stored.id,
        byte_size=128,
    )
    session.add(asset)
    await session.flush()
    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="PDF",
        parser_name="test",
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    session.add(document)
    await session.flush()
    event = await InteractionEventRepository(session).record(
        SemanticEventType.READER_OPENED,
        principal_id=principal.id,
        entity_type="document",
        entity_id=document.id,
    )
    event.occurred_at = occurred_at
    await session.flush()
    return event


async def test_correction_blocks_old_evidence_until_new_threshold_is_rebuilt(
    session: AsyncSession,
) -> None:
    principal = await _principal(session)
    service = LearningService(session_scope_factory=_scope(session))
    baseline = datetime.now(UTC)

    for index in range(4):
        await _record_open(
            session,
            principal=principal,
            suffix=f"before-{index}",
            occurred_at=baseline - timedelta(days=10 - index),
        )

    original_id = await service.refresh_format_preference(
        principal_id=principal.id,
        now=baseline,
    )
    assert original_id is not None

    correction_at = baseline + timedelta(days=1)
    await service.record_outcome(
        principal_id=principal.id,
        request=OutcomeCreate(
            outcome=OutcomeValue.CORRECTED,
            entity_type="preference_claim",
            entity_id=original_id,
        ),
        now=correction_at,
    )
    original = await session.get(PreferenceClaim, original_id)
    assert original is not None
    assert original.status == "contradicted"
    assert original.updated_at == correction_at

    recreated_from_old = await service.refresh_format_preference(
        principal_id=principal.id,
        now=correction_at + timedelta(days=1),
    )
    assert recreated_from_old is None

    for index in range(3):
        await _record_open(
            session,
            principal=principal,
            suffix=f"after-{index}",
            occurred_at=correction_at + timedelta(days=2 + index),
        )
    still_below_threshold = await service.refresh_format_preference(
        principal_id=principal.id,
        now=correction_at + timedelta(days=6),
    )
    assert still_below_threshold is None

    await _record_open(
        session,
        principal=principal,
        suffix="after-3",
        occurred_at=correction_at + timedelta(days=7),
    )
    replacement_id = await service.refresh_format_preference(
        principal_id=principal.id,
        now=correction_at + timedelta(days=8),
    )
    assert replacement_id is not None
    assert replacement_id != original_id
    replacement = await session.get(PreferenceClaim, replacement_id)
    assert replacement is not None
    assert replacement.source == "inferred"
    assert replacement.status == "active"
    assert replacement.value == {"format": "PDF"}
    assert replacement.evidence_count == 4
    assert replacement.first_observed_at > correction_at
