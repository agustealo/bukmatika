import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_reader import _seed_reader_document

from bukmatika.persistence.models import Principal, StoredObject, Work
from bukmatika.persistence.readers import ReaderRepository


async def test_older_reader_transaction_cannot_overwrite_newer_progress() -> None:
    database_url = os.getenv("BUKMATIKA_DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL integration URL not configured")

    suffix = f"write-order-{uuid4().hex}"
    engine = create_async_engine(database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with sessions() as setup:
            entry, document, sections = await _seed_reader_document(setup, suffix=suffix)
            principal_id = entry.principal_id
            entry_id = entry.id
            document_id = document.id
            first_section_id = sections[0].id
            later_section_id = sections[1].id
            await setup.commit()

        async with sessions() as older, sessions() as newer:
            older_repository = ReaderRepository(older)
            older_access = await older_repository.require_access(
                principal_id,
                entry_id,
                document_id,
            )
            older_started_at = await older.scalar(select(func.transaction_timestamp()))
            assert older_started_at is not None

            await asyncio.sleep(0.01)

            newer_repository = ReaderRepository(newer)
            newer_access = await newer_repository.require_access(
                principal_id,
                entry_id,
                document_id,
            )
            newer_started_at = await newer.scalar(select(func.transaction_timestamp()))
            assert newer_started_at is not None
            assert newer_started_at > older_started_at

            newer_state = await newer_repository.save_progress(
                access=newer_access,
                section_id=later_section_id,
                char_offset=9,
            )
            await newer.commit()
            assert newer_state.section_id == later_section_id
            assert newer_state.char_offset == 9

            stale_result = await older_repository.save_progress(
                access=older_access,
                section_id=first_section_id,
                char_offset=3,
            )
            await older.commit()

            assert stale_result.section_id == later_section_id
            assert stale_result.char_offset == 9
            assert stale_result.position_write_started_at == newer_started_at

        async with sessions() as verification:
            persisted = await ReaderRepository(verification).state_for(entry_id, document_id)
            assert persisted is not None
            assert persisted.section_id == later_section_id
            assert persisted.char_offset == 9
            assert persisted.position_write_started_at == newer_started_at
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                delete(Principal).where(Principal.external_subject == f"reader-{suffix}")
            )
            await cleanup.execute(
                delete(Work).where(Work.normalized_title == f"reader work {suffix}")
            )
            await cleanup.execute(
                delete(StoredObject).where(StoredObject.storage_key == f"objects/aa/bb/{suffix}")
            )
            await cleanup.commit()
        await engine.dispose()
