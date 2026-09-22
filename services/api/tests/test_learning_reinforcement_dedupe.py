from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.models import Asset, Edition, Principal, StoredObject, Work
from bukmatika.persistence.personalization_models import (
    PreferenceClaim,
    PreferenceClaimEvidence,
)
from bukmatika.personalization.learning import LearningService, OutcomeCreate, OutcomeValue


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession) -> Principal:
    principal = Principal(kind="local", external_subject="learning-reopen-dedupe")
    session.add(principal)
    await session.flush()
    return principal


async def _open_document(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
) -> Document:
    work = Work(
        canonical_title=f"Reopen Work {suffix}",
        normalized_title=f"reopen work {suffix}",
    )
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/reopen/{suffix}",
        byte_size=128,
        media_type="application/pdf",
    )
    session.add_all([work, stored])
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title=f"Reopen Edition {suffix}",
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
    await InteractionEventRepository(session).record(
        SemanticEventType.READER_OPENED,
        principal_id=principal.id,
        entity_type="document",
        entity_id=document.id,
    )
    return document


async def test_reopening_existing_document_does_not_restore_or_inflate_claim(
    session: AsyncSession,
) -> None:
    principal = await _principal(session)
    service = LearningService(session_scope_factory=_scope(session))
    documents = [
        await _open_document(session, principal=principal, suffix=f"pdf-{index}")
        for index in range(4)
    ]

    claim_id = await service.refresh_format_preference(principal_id=principal.id)
    assert claim_id is not None
    claim = await session.get(PreferenceClaim, claim_id)
    assert claim is not None
    assert claim.evidence_count == 4
    initial_reinforced_at = claim.last_reinforced_at

    await service.record_outcome(
        principal_id=principal.id,
        request=OutcomeCreate(
            outcome=OutcomeValue.REJECTED,
            entity_type="preference_claim",
            entity_id=claim_id,
        ),
    )
    reduced_confidence = claim.confidence

    await InteractionEventRepository(session).record(
        SemanticEventType.READER_OPENED,
        principal_id=principal.id,
        entity_type="document",
        entity_id=documents[0].id,
    )
    refreshed_id = await service.refresh_format_preference(principal_id=principal.id)

    assert refreshed_id == claim_id
    assert claim.evidence_count == 4
    assert claim.confidence == reduced_confidence
    assert claim.last_reinforced_at == initial_reinforced_at
    provenance_count = await session.scalar(
        select(func.count())
        .select_from(PreferenceClaimEvidence)
        .where(PreferenceClaimEvidence.preference_claim_id == claim_id)
    )
    assert provenance_count == 4
