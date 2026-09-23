from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library import (
    CollectionCreate,
    LibraryOrganizationNotFound,
    LibraryReadingStatus,
    LibraryService,
    TagAssignRequest,
)
from bukmatika.main import app
from bukmatika.persistence.document_models import Document
from bukmatika.persistence.library import LibraryRepository
from bukmatika.persistence.library_organization_models import (
    LibraryCollectionEntry,
    LibraryEntryTag,
)
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, Principal, StoredObject, Work
from bukmatika.persistence.reader_models import ReadingState


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_entry(
    session: AsyncSession,
    *,
    suffix: str,
    principal: Principal | None = None,
) -> tuple[Principal, Work, LibraryEntry]:
    owner = principal or Principal(kind="local", external_subject=f"org-{suffix}")
    work = Work(canonical_title=f"Organized Work {suffix}", normalized_title=f"organized work {suffix}")
    if principal is None:
        session.add(owner)
    session.add(work)
    await session.flush()
    entry = LibraryEntry(
        principal_id=owner.id,
        work_id=work.id,
        edition_id=None,
        status="saved",
    )
    session.add(entry)
    await session.flush()
    return owner, work, entry


async def _seed_document(
    session: AsyncSession,
    *,
    work: Work,
    suffix: str,
    updated_at: datetime,
) -> tuple[Edition, Document]:
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/org/{suffix}",
        byte_size=64,
        media_type="text/plain",
    )
    edition = Edition(work_id=work.id, title=f"Edition {suffix}", language="en")
    session.add_all([stored, edition])
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=f"https://example.org/{suffix}.txt",
        stored_object_id=stored.id,
        byte_size=64,
    )
    session.add(asset)
    await session.flush()
    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="TXT",
        parser_name="text",
        parser_version="1",
        section_count=1,
        chunk_count=1,
        updated_at=updated_at,
    )
    session.add(document)
    await session.flush()
    return edition, document


async def test_collection_and_tag_names_are_normalized_and_membership_is_idempotent(
    session: AsyncSession,
) -> None:
    principal, _, first = await _seed_entry(session, suffix="first")
    _, _, second = await _seed_entry(session, suffix="second", principal=principal)
    service = LibraryService(session_scope_factory=_scope(session))

    first_collection = await service.create_collection(
        principal_id=principal.id,
        create=CollectionCreate(name="  Atlantic   History  "),
    )
    second_collection = await service.create_collection(
        principal_id=principal.id,
        create=CollectionCreate(name="ATLANTIC HISTORY"),
    )
    assert first_collection.collection_id == second_collection.collection_id
    assert first_collection.name == "Atlantic History"

    await service.add_collection_entry(
        principal_id=principal.id,
        collection_id=first_collection.collection_id,
        library_entry_id=first.id,
    )
    await service.add_collection_entry(
        principal_id=principal.id,
        collection_id=first_collection.collection_id,
        library_entry_id=first.id,
    )
    assert len((await session.scalars(LibraryCollectionEntry.__table__.select())).all()) == 1

    first_tag = await service.assign_tag(
        principal_id=principal.id,
        library_entry_id=first.id,
        request=TagAssignRequest(name="  Pre Columbus "),
    )
    second_tag = await service.assign_tag(
        principal_id=principal.id,
        library_entry_id=second.id,
        request=TagAssignRequest(name="PRE COLUMBUS"),
    )
    assert first_tag.tag_id == second_tag.tag_id
    assert len((await session.scalars(LibraryEntryTag.__table__.select())).all()) == 2

    organization = await service.organization(principal_id=principal.id)
    assert organization.collections[0].item_count == 1
    assert organization.tags[0].item_count == 2


async def test_collection_and_tag_filters_intersect_without_copying_library_entries(
    session: AsyncSession,
) -> None:
    principal, _, first = await _seed_entry(session, suffix="filter-first")
    _, _, second = await _seed_entry(session, suffix="filter-second", principal=principal)
    _, _, third = await _seed_entry(session, suffix="filter-third", principal=principal)
    service = LibraryService(session_scope_factory=_scope(session))
    collection = await service.create_collection(
        principal_id=principal.id,
        create=CollectionCreate(name="Research queue"),
    )
    for entry in (first, second):
        await service.add_collection_entry(
            principal_id=principal.id,
            collection_id=collection.collection_id,
            library_entry_id=entry.id,
        )
    tag = await service.assign_tag(
        principal_id=principal.id,
        library_entry_id=second.id,
        request=TagAssignRequest(name="Priority"),
    )
    await service.assign_tag(
        principal_id=principal.id,
        library_entry_id=third.id,
        request=TagAssignRequest(name="priority"),
    )

    filtered = await service.list_library(
        principal_id=principal.id,
        collection_id=collection.collection_id,
        tag_id=tag.tag_id,
        reading_status=LibraryReadingStatus.UNREAD,
    )
    assert [item.library_entry_id for item in filtered.items] == [second.id]
    assert [item.name for item in filtered.items[0].collections] == ["Research queue"]
    assert [item.name for item in filtered.items[0].tags] == ["Priority"]

    entries = await LibraryRepository(session).library_entries(principal.id)
    assert {entry.id for entry in entries} == {first.id, second.id, third.id}


async def test_cross_principal_organization_mutation_is_denied(session: AsyncSession) -> None:
    owner, _, owner_entry = await _seed_entry(session, suffix="owner")
    intruder, _, intruder_entry = await _seed_entry(session, suffix="intruder")
    service = LibraryService(session_scope_factory=_scope(session))
    collection = await service.create_collection(
        principal_id=owner.id,
        create=CollectionCreate(name="Owner shelf"),
    )

    with pytest.raises(LibraryOrganizationNotFound):
        await service.add_collection_entry(
            principal_id=owner.id,
            collection_id=collection.collection_id,
            library_entry_id=intruder_entry.id,
        )
    with pytest.raises(LibraryOrganizationNotFound):
        await service.assign_tag(
            principal_id=intruder.id,
            library_entry_id=owner_entry.id,
            request=TagAssignRequest(name="stolen"),
        )

    assert (await service.list_library(principal_id=owner.id)).items[0].collections == []


async def test_work_level_library_resume_uses_actual_latest_read_state(
    session: AsyncSession,
) -> None:
    principal, work, entry = await _seed_entry(session, suffix="resume")
    now = datetime.now(UTC)
    _, first_document = await _seed_document(
        session,
        work=work,
        suffix="resume-first",
        updated_at=now - timedelta(days=1),
    )
    _, newest_document = await _seed_document(
        session,
        work=work,
        suffix="resume-newest",
        updated_at=now,
    )
    state = ReadingState(
        library_entry_id=entry.id,
        document_id=first_document.id,
        status="reading",
        progress_fraction=0.55,
        section_ordinal=0,
        char_offset=4,
        locator={"page": 1},
        last_read_at=now,
    )
    session.add(state)
    await session.flush()

    fallback = await LibraryRepository(session).readable_document_for_entry(entry)
    assert fallback is not None
    assert fallback[0].id == newest_document.id

    response = await LibraryService(session_scope_factory=_scope(session)).list_library(
        principal_id=principal.id,
        reading_status=LibraryReadingStatus.READING,
    )
    assert len(response.items) == 1
    item = response.items[0]
    assert item.readable_document_id == first_document.id
    assert item.progress_fraction == 0.55
    assert item.reading_status == "reading"


async def test_organization_routes_are_mounted_in_openapi_contract() -> None:
    paths = app.openapi()["paths"]
    assert "/v1/library/organization" in paths
    assert "/v1/library/collections" in paths
    assert "/v1/library/entries/{library_entry_id}/tags" in paths
    assert "post" in paths["/v1/library/collections"]
