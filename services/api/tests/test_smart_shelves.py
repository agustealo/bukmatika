from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library import (
    CollectionCreate,
    LibraryOrganizationNotFound,
    LibraryReadingStatus,
    LibraryService,
    SmartShelfCreate,
    SmartShelfRule,
    TagAssignRequest,
)
from bukmatika.main import app
from bukmatika.persistence.document_models import Document
from bukmatika.persistence.library_organization_models import LibrarySmartShelf
from bukmatika.persistence.models import (
    Asset,
    Edition,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.persistence.reader_models import ReadingState


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _entry(
    session: AsyncSession,
    *,
    suffix: str,
    principal: Principal | None = None,
) -> tuple[Principal, Work, LibraryEntry]:
    owner = principal or Principal(kind="local", external_subject=f"smart-shelf-{suffix}")
    work = Work(
        canonical_title=f"Smart Shelf Work {suffix}",
        normalized_title=f"smart shelf work {suffix}",
    )
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


async def _document(
    session: AsyncSession,
    *,
    work: Work,
    suffix: str,
) -> Document:
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/smart-shelf/{suffix}",
        byte_size=32,
        media_type="text/plain",
    )
    edition = Edition(
        work_id=work.id,
        title=f"Smart Shelf Edition {suffix}",
        language="en",
    )
    session.add_all([stored, edition])
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=f"https://example.org/{suffix}.txt",
        stored_object_id=stored.id,
        byte_size=stored.byte_size,
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
    )
    session.add(document)
    await session.flush()
    return document


def test_smart_shelf_rule_requires_at_least_one_predicate() -> None:
    with pytest.raises(ValidationError):
        SmartShelfRule()


async def test_smart_shelf_recomputes_live_collection_and_tag_intersection(
    session: AsyncSession,
) -> None:
    principal, _, first = await _entry(session, suffix="live-first")
    _, _, second = await _entry(session, suffix="live-second", principal=principal)
    _, _, third = await _entry(session, suffix="live-third", principal=principal)
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
    shelf = await service.create_smart_shelf(
        principal_id=principal.id,
        create=SmartShelfCreate(
            name="Priority research",
            rule=SmartShelfRule(
                collection_id=collection.collection_id,
                tag_id=tag.tag_id,
            ),
        ),
    )

    initial = await service.smart_shelf_contents(
        principal_id=principal.id,
        smart_shelf_id=shelf.smart_shelf_id,
    )
    assert [item.library_entry_id for item in initial.items] == [second.id]
    assert initial.shelf.item_count == 1

    await service.assign_tag(
        principal_id=principal.id,
        library_entry_id=first.id,
        request=TagAssignRequest(name="PRIORITY"),
    )
    expanded = await service.smart_shelf_contents(
        principal_id=principal.id,
        smart_shelf_id=shelf.smart_shelf_id,
    )
    assert {item.library_entry_id for item in expanded.items} == {first.id, second.id}

    await service.remove_collection_entry(
        principal_id=principal.id,
        collection_id=collection.collection_id,
        library_entry_id=second.id,
    )
    narrowed = await service.smart_shelf_contents(
        principal_id=principal.id,
        smart_shelf_id=shelf.smart_shelf_id,
    )
    assert [item.library_entry_id for item in narrowed.items] == [first.id]


async def test_smart_shelf_recomputes_after_reading_state_changes(
    session: AsyncSession,
) -> None:
    principal, work, entry = await _entry(session, suffix="reading-live")
    document = await _document(session, work=work, suffix="reading-live")
    service = LibraryService(session_scope_factory=_scope(session))
    shelf = await service.create_smart_shelf(
        principal_id=principal.id,
        create=SmartShelfCreate(
            name="Currently reading",
            rule=SmartShelfRule(reading_status=LibraryReadingStatus.READING),
        ),
    )

    empty = await service.smart_shelf_contents(
        principal_id=principal.id,
        smart_shelf_id=shelf.smart_shelf_id,
    )
    assert empty.items == []

    state = ReadingState(
        library_entry_id=entry.id,
        document_id=document.id,
        status="reading",
        progress_fraction=0.25,
        locator={},
        last_read_at=datetime.now(UTC),
    )
    session.add(state)
    await session.flush()

    reading = await service.smart_shelf_contents(
        principal_id=principal.id,
        smart_shelf_id=shelf.smart_shelf_id,
    )
    assert [item.library_entry_id for item in reading.items] == [entry.id]

    state.status = "finished"
    state.progress_fraction = 1.0
    await session.flush()
    finished = await service.smart_shelf_contents(
        principal_id=principal.id,
        smart_shelf_id=shelf.smart_shelf_id,
    )
    assert finished.items == []


async def test_smart_shelf_rejects_cross_principal_references_and_access(
    session: AsyncSession,
) -> None:
    owner, _, owner_entry = await _entry(session, suffix="owner")
    intruder, _, _ = await _entry(session, suffix="intruder")
    service = LibraryService(session_scope_factory=_scope(session))
    collection = await service.create_collection(
        principal_id=owner.id,
        create=CollectionCreate(name="Private shelf source"),
    )
    await service.add_collection_entry(
        principal_id=owner.id,
        collection_id=collection.collection_id,
        library_entry_id=owner_entry.id,
    )

    with pytest.raises(LibraryOrganizationNotFound):
        await service.create_smart_shelf(
            principal_id=intruder.id,
            create=SmartShelfCreate(
                name="Cross principal",
                rule=SmartShelfRule(collection_id=collection.collection_id),
            ),
        )

    shelf = await service.create_smart_shelf(
        principal_id=owner.id,
        create=SmartShelfCreate(
            name="Owner smart shelf",
            rule=SmartShelfRule(collection_id=collection.collection_id),
        ),
    )
    with pytest.raises(LibraryOrganizationNotFound):
        await service.smart_shelf_contents(
            principal_id=intruder.id,
            smart_shelf_id=shelf.smart_shelf_id,
        )


async def test_deleting_rule_dependency_cascades_shelf_instead_of_broadening(
    session: AsyncSession,
) -> None:
    principal, _, entry = await _entry(session, suffix="cascade")
    service = LibraryService(session_scope_factory=_scope(session))
    collection = await service.create_collection(
        principal_id=principal.id,
        create=CollectionCreate(name="Temporary source"),
    )
    await service.add_collection_entry(
        principal_id=principal.id,
        collection_id=collection.collection_id,
        library_entry_id=entry.id,
    )
    shelf = await service.create_smart_shelf(
        principal_id=principal.id,
        create=SmartShelfCreate(
            name="Dependent shelf",
            rule=SmartShelfRule(collection_id=collection.collection_id),
        ),
    )

    await service.delete_collection(
        principal_id=principal.id,
        collection_id=collection.collection_id,
    )
    assert await session.scalar(
        select(LibrarySmartShelf).where(LibrarySmartShelf.id == shelf.smart_shelf_id)
    ) is None
    with pytest.raises(LibraryOrganizationNotFound):
        await service.smart_shelf_contents(
            principal_id=principal.id,
            smart_shelf_id=shelf.smart_shelf_id,
        )


async def test_smart_shelf_names_are_idempotent_only_for_identical_rules(
    session: AsyncSession,
) -> None:
    principal, _, entry = await _entry(session, suffix="idempotent")
    service = LibraryService(session_scope_factory=_scope(session))
    tag = await service.assign_tag(
        principal_id=principal.id,
        library_entry_id=entry.id,
        request=TagAssignRequest(name="Reference"),
    )
    request = SmartShelfCreate(
        name="  Reference books ",
        rule=SmartShelfRule(tag_id=tag.tag_id),
    )
    first = await service.create_smart_shelf(principal_id=principal.id, create=request)
    second = await service.create_smart_shelf(
        principal_id=principal.id,
        create=SmartShelfCreate(
            name="REFERENCE BOOKS",
            rule=SmartShelfRule(tag_id=tag.tag_id),
        ),
    )
    assert first.smart_shelf_id == second.smart_shelf_id
    assert second.name == "Reference books"



def test_smart_shelf_routes_are_mounted_in_openapi_contract() -> None:
    paths = app.openapi()["paths"]
    assert "/v1/library/smart-shelves" in paths
    assert "/v1/library/smart-shelves/{smart_shelf_id}" in paths
    assert "/v1/library/smart-shelves/{smart_shelf_id}/remove" in paths
    assert "post" in paths["/v1/library/smart-shelves"]
    assert "get" in paths["/v1/library/smart-shelves/{smart_shelf_id}"]
