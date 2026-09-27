from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.persistence.identity import AuthenticatedPrincipal
from bukmatika.persistence.models import Principal
from bukmatika.persistence.privacy_models import PrivacyErasureObject
from bukmatika.privacy.routes import export_account_data
from bukmatika.privacy.service import AccountPrivacyService


def _scope(
    session: AsyncSession,
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


@pytest.mark.asyncio
async def test_account_export_does_not_advance_pending_storage_erasure(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = Principal(kind="local", external_subject=f"local:{uuid4()}")
    session.add(principal)
    await session.flush()

    sha256 = "a" * 64
    storage_key = f"objects/{sha256[:2]}/{sha256[2:4]}/{sha256}"
    stored_path = tmp_path / storage_key
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(b"queued private bytes")
    queued = PrivacyErasureObject(
        sha256=sha256,
        storage_key=storage_key,
        status="pending",
    )
    session.add(queued)
    await session.flush()

    service = AccountPrivacyService(
        storage=LocalObjectStore(tmp_path),
        session_scope_factory=_scope(session),
    )
    identity = AuthenticatedPrincipal(
        principal_id=principal.id,
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    exported = await export_account_data(identity=identity, service=service)

    await session.refresh(queued)
    assert exported.principal.principal_id == principal.id
    assert queued.status == "pending"
    assert queued.attempt_count == 0
    assert queued.last_attempt_at is None
    assert queued.deleted_at is None
    assert stored_path.read_bytes() == b"queued private bytes"
