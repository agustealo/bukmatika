from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library import (
    CollectionCreate,
    LibraryPortabilityService,
    LibraryService,
    SmartShelfCreate,
    SmartShelfRule,
    TagAssignRequest,
)
from bukmatika.main import app
from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.models import (
    Asset,
    Contributor,
    Edition,
    Identifier,
    LibraryEntry,
    Principal,
    RightsDecision,
    RightsDecisionEvidence,
    RightsEvidenceRecord,
    SourceRecord,
    SourceRecordLink,
    StoredObject,
    Subject,
    Work,
    WorkContributor,
    WorkSubject,
)
from bukmatika.persistence.reader_models import Bookmark, Highlight, ReadingState


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_portable_library(
    session: AsyncSession,
    *,
    suffix: str,
    public_domain: bool,
) -> tuple[Principal, LibraryEntry, StoredObject]:
    principal = Principal(kind="local", external_subject=f"library-export-{suffix}")
    work = Work(
        canonical_title=f"Portable Work {suffix}",
        normalized_title=f"portable work {suffix}",
    )
    session.add_all([principal, work])
    await session.flush()

    author = Contributor(
        display_name=f"Author {suffix}",
        normalized_name=f"author {suffix}",
    )
    subject = Subject(
        display_name=f"Subject {suffix}",
        normalized_name=f"subject {suffix}",
    )
    session.add_all([author, subject])
    await session.flush()
    session.add_all(
        [
            WorkContributor(work_id=work.id, contributor_id=author.id, role="author"),
            WorkSubject(work_id=work.id, subject_id=subject.id),
        ]
    )

    edition = Edition(
        work_id=work.id,
        title=f"Portable Edition {suffix}",
        language="en",
        publication_year=1899,
        publisher="Open Press",
        edition_statement="First portable edition",
    )
    session.add(edition)
    await session.flush()
    session.add_all(
        [
            Identifier(
                entity_type="work",
                entity_id=work.id,
                scheme="lccn",
                value=f"work-{suffix}",
                normalized_value=f"work-{suffix}",
            ),
            Identifier(
                entity_type="edition",
                entity_id=edition.id,
                scheme="isbn",
                value=f"978-{suffix}",
                normalized_value=f"978-{suffix}",
            ),
        ]
    )
    source = SourceRecord(
        provider="fixture-provider",
        provider_record_id=f"record-{suffix}",
        canonical_url=f"https://catalog.example/{suffix}",
    )
    session.add(source)
    await session.flush()
    session.add_all(
        [
            SourceRecordLink(
                source_record_id=source.id,
                entity_type="work",
                entity_id=work.id,
                relationship="describes",
            ),
            SourceRecordLink(
                source_record_id=source.id,
                entity_type="edition",
                entity_id=edition.id,
                relationship="describes",
            ),
        ]
    )

    token = uuid4().hex
    stored = StoredObject(
        sha256=token + token,
        storage_key=f"private/storage/{suffix}/book.epub",
        byte_size=512,
        media_type="application/epub+zip",
    )
    session.add(stored)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="EPUB",
        media_type="application/epub+zip",
        remote_url=f"https://signed.example/{suffix}?secret=must-not-export",
        stored_object_id=stored.id,
        byte_size=512,
    )
    session.add(asset)
    await session.flush()
    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="EPUB",
        parser_name="epub",
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    session.add(document)
    await session.flush()
    section = DocumentSection(
        document_id=document.id,
        ordinal=0,
        heading="Chapter one",
        locator={"spine_index": 0, "href": "chapter-1.xhtml"},
        text="Portable source text that is intentionally not part of the manifest.",
    )
    session.add(section)
    await session.flush()

    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    session.add(entry)
    await session.flush()
    state = ReadingState(
        library_entry_id=entry.id,
        document_id=document.id,
        status="reading",
        progress_fraction=0.42,
        section_id=section.id,
        section_ordinal=0,
        char_offset=9,
        locator=section.locator,
    )
    session.add(state)
    await session.flush()
    session.add_all(
        [
            Bookmark(
                reading_state_id=state.id,
                section_id=section.id,
                char_offset=9,
                locator=section.locator,
                label=f"Bookmark {suffix}",
            ),
            Highlight(
                reading_state_id=state.id,
                section_id=section.id,
                char_start=0,
                char_end=8,
                locator=section.locator,
                note=f"Highlight note {suffix}",
            ),
        ]
    )

    if public_domain:
        evidence = RightsEvidenceRecord(
            evidence_sha256=("a" * 63) + suffix[-1],
            state="public_domain",
            source="fixture-provider",
            basis="Explicit public-domain statement",
            evidence_url=f"https://catalog.example/{suffix}/rights",
            confidence=1.0,
        )
        session.add(evidence)
        await session.flush()
        decision = RightsDecision(
            subject_type="asset",
            subject_id=asset.id,
            rights_state="public_domain",
            jurisdiction="US",
            policy_version="rights-us-v1",
            permissions={
                "discover": True,
                "display_metadata": True,
                "download": True,
                "retain": True,
                "process": True,
                "ocr": True,
                "export": True,
                "share": True,
            },
            reason="Fixture public-domain decision",
        )
        session.add(decision)
        await session.flush()
        session.add(
            RightsDecisionEvidence(
                rights_decision_id=decision.id,
                rights_evidence_id=evidence.id,
            )
        )
    await session.flush()
    return principal, entry, stored


async def test_library_export_is_principal_scoped_portable_and_byte_free(
    session: AsyncSession,
) -> None:
    owner, entry, stored = await _seed_portable_library(
        session,
        suffix="owner1",
        public_domain=True,
    )
    other, _, other_stored = await _seed_portable_library(
        session,
        suffix="other2",
        public_domain=True,
    )
    service = LibraryService(session_scope_factory=_scope(session))
    collection = await service.create_collection(
        principal_id=owner.id,
        create=CollectionCreate(name="Primary Sources", description="Owned research set"),
    )
    await service.add_collection_entry(
        principal_id=owner.id,
        collection_id=collection.collection_id,
        library_entry_id=entry.id,
    )
    tag = await service.assign_tag(
        principal_id=owner.id,
        library_entry_id=entry.id,
        request=TagAssignRequest(name="Atlantic"),
    )
    shelf = await service.create_smart_shelf(
        principal_id=owner.id,
        create=SmartShelfCreate(
            name="Reading Atlantic",
            description="Active reading queue",
            rule=SmartShelfRule(
                reading_status="reading",
                collection_id=collection.collection_id,
                tag_id=tag.tag_id,
            ),
        ),
    )

    exported = await LibraryPortabilityService(
        session_scope_factory=_scope(session)
    ).export(principal_id=owner.id)

    assert exported.schema_version == 1
    assert exported.export_kind == "bukmatika-library-manifest"
    assert exported.content_mode == "metadata-and-state-only"
    assert len(exported.entries) == 1
    exported_entry = exported.entries[0]
    assert exported_entry.source_library_entry_id == entry.id
    assert exported_entry.work.authors == ["Author owner1"]
    assert exported_entry.work.identifiers[0].scheme == "lccn"
    assert exported_entry.work.sources[0].provider_record_id == "record-owner1"
    assert exported_entry.edition is not None
    assert exported_entry.edition.identifiers[0].scheme == "isbn"
    assert exported_entry.collection_ids == [collection.collection_id]
    assert exported_entry.tag_ids == [tag.tag_id]
    assert exported.collections[0].source_collection_id == collection.collection_id
    assert exported.tags[0].source_tag_id == tag.tag_id
    assert exported.smart_shelves[0].source_smart_shelf_id == shelf.smart_shelf_id

    asset = exported_entry.assets[0]
    assert asset.content_sha256 == stored.sha256
    assert asset.document is not None
    assert asset.document.source_sha256 == stored.sha256
    assert asset.rights is not None
    assert asset.rights.rights_state == "public_domain"
    assert asset.byte_policy.policy_export_allowed is True
    assert asset.byte_policy.policy_share_allowed is True
    assert asset.byte_policy.bytes_included is False

    reading = exported_entry.reading_states[0]
    assert reading.progress_fraction == 0.42
    assert reading.position is not None
    assert reading.position.section_ordinal == 0
    assert reading.bookmarks[0].label == "Bookmark owner1"
    assert reading.highlights[0].note == "Highlight note owner1"

    payload = exported.model_dump_json()
    assert stored.storage_key not in payload
    assert other_stored.storage_key not in payload
    assert "must-not-export" not in payload
    assert "Portable source text that is intentionally not part of the manifest." not in payload
    assert "other2" not in payload
    assert str(other.id) not in payload


async def test_library_export_fails_closed_when_no_rights_decision_exists(
    session: AsyncSession,
) -> None:
    principal, _, _ = await _seed_portable_library(
        session,
        suffix="unknown3",
        public_domain=False,
    )
    exported = await LibraryPortabilityService(
        session_scope_factory=_scope(session)
    ).export(principal_id=principal.id)

    asset = exported.entries[0].assets[0]
    assert asset.rights is None
    assert asset.byte_policy.bytes_included is False
    assert asset.byte_policy.policy_export_allowed is False
    assert asset.byte_policy.policy_share_allowed is False


async def test_library_export_ordering_is_deterministic(session: AsyncSession) -> None:
    principal, _, _ = await _seed_portable_library(
        session,
        suffix="zulu4",
        public_domain=False,
    )
    second_owner, second_entry, _ = await _seed_portable_library(
        session,
        suffix="alpha5",
        public_domain=False,
    )
    second_entry.principal_id = principal.id
    await session.flush()
    await session.delete(second_owner)
    await session.flush()

    service = LibraryPortabilityService(session_scope_factory=_scope(session))
    first = await service.export(principal_id=principal.id)
    second = await service.export(principal_id=principal.id)
    first_payload = first.model_dump(exclude={"exported_at"})
    second_payload = second.model_dump(exclude={"exported_at"})

    assert [entry.work.canonical_title for entry in first.entries] == [
        "Portable Work alpha5",
        "Portable Work zulu4",
    ]
    assert first_payload == second_payload


def test_library_export_route_is_mounted() -> None:
    assert "/v1/library/export" in set(app.openapi()["paths"])
