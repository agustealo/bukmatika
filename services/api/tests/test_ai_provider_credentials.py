import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.credentials import (
    CredentialIntegrityError,
    CredentialKeyUnavailable,
    CredentialReference,
    CredentialUnavailable,
    DatabaseCredentialStore,
    InstallationCredentialKey,
)
from bukmatika.persistence.models import Principal
from bukmatika.persistence.provider_models import AIProviderCredential
from bukmatika.persistence.providers import (
    ProviderConnectionNotFound,
    ProviderConnectionRepository,
)


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(
        kind="local",
        external_subject=f"credential-{suffix}-{uuid4()}",
    )
    session.add(principal)
    await session.flush()
    return principal


async def _cloud_connection(session: AsyncSession, principal: Principal):  # type: ignore[no-untyped-def]
    return await ProviderConnectionRepository(session).create_connection(
        principal_id=principal.id,
        provider_id="synthetic-cloud",
        routing_type="cloud",
        display_name="Synthetic Cloud",
    )


def _store(session: AsyncSession, key_path: Path) -> DatabaseCredentialStore:
    return DatabaseCredentialStore(
        session,
        installation_key=InstallationCredentialKey(key_path),
    )


async def test_credential_is_encrypted_and_normal_connection_row_only_has_reference(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "encrypted")
    connection = await _cloud_connection(session, principal)
    secret = "sk-test-secret-that-must-never-be-stored-verbatim"
    store = _store(session, tmp_path / "credential.key")

    reference = await store.replace(
        principal_id=principal.id,
        connection_id=connection.id,
        secret=secret,
    )

    credential = await session.scalar(
        select(AIProviderCredential).where(AIProviderCredential.connection_id == connection.id)
    )
    assert credential is not None
    assert secret.encode() not in credential.ciphertext
    assert credential.nonce != b""
    assert connection.credential_reference == reference.value
    assert secret not in reference.value
    assert not hasattr(connection, "api_key")
    assert await store.reveal(
        principal_id=principal.id,
        connection_id=connection.id,
        reference=reference,
    ) == secret


def test_installation_key_is_created_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "provider-credentials.key"
    key = InstallationCredentialKey(path).load_or_create()

    assert len(key) == 32
    assert path.read_bytes() == key
    assert path.stat().st_mode & 0o077 == 0
    assert path.parent.stat().st_mode & 0o077 == 0


async def test_rotation_invalidates_old_reference_and_removes_old_ciphertext(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "rotation")
    connection = await _cloud_connection(session, principal)
    store = _store(session, tmp_path / "credential.key")

    first = await store.replace(
        principal_id=principal.id,
        connection_id=connection.id,
        secret="first-secret",
    )
    first_id = first.value
    second = await store.replace(
        principal_id=principal.id,
        connection_id=connection.id,
        secret="second-secret",
    )

    assert second.value != first_id
    with pytest.raises(CredentialUnavailable):
        await store.reveal(
            principal_id=principal.id,
            connection_id=connection.id,
            reference=CredentialReference(first_id),
        )
    assert await store.reveal(
        principal_id=principal.id,
        connection_id=connection.id,
        reference=second,
    ) == "second-secret"

    rows = list(
        await session.scalars(
            select(AIProviderCredential).where(
                AIProviderCredential.connection_id == connection.id
            )
        )
    )
    assert len(rows) == 1


async def test_cross_principal_reveal_is_rejected(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner = await _principal(session, "owner")
    other = await _principal(session, "other")
    connection = await _cloud_connection(session, owner)
    store = _store(session, tmp_path / "credential.key")
    reference = await store.replace(
        principal_id=owner.id,
        connection_id=connection.id,
        secret="owner-secret",
    )

    with pytest.raises(ProviderConnectionNotFound) as captured:
        await store.reveal(
            principal_id=other.id,
            connection_id=connection.id,
            reference=reference,
        )
    assert "owner-secret" not in str(captured.value)


async def test_delete_removes_secret_and_reference(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "delete")
    connection = await _cloud_connection(session, principal)
    store = _store(session, tmp_path / "credential.key")
    reference = await store.replace(
        principal_id=principal.id,
        connection_id=connection.id,
        secret="delete-me",
    )

    await store.delete(principal_id=principal.id, connection_id=connection.id)

    assert connection.credential_reference is None
    assert await session.scalar(
        select(AIProviderCredential).where(AIProviderCredential.connection_id == connection.id)
    ) is None
    with pytest.raises(CredentialUnavailable):
        await store.reveal(
            principal_id=principal.id,
            connection_id=connection.id,
            reference=reference,
        )


async def test_ciphertext_tampering_fails_authentication_without_secret_leakage(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "tamper")
    connection = await _cloud_connection(session, principal)
    secret = "tamper-proof-secret"
    store = _store(session, tmp_path / "credential.key")
    reference = await store.replace(
        principal_id=principal.id,
        connection_id=connection.id,
        secret=secret,
    )
    credential = await session.scalar(
        select(AIProviderCredential).where(AIProviderCredential.connection_id == connection.id)
    )
    assert credential is not None
    credential.ciphertext = bytes([credential.ciphertext[0] ^ 1]) + credential.ciphertext[1:]
    await session.flush()

    with pytest.raises(CredentialIntegrityError) as captured:
        await store.reveal(
            principal_id=principal.id,
            connection_id=connection.id,
            reference=reference,
        )
    assert secret not in str(captured.value)


def test_key_loader_rejects_group_or_world_readable_key(tmp_path: Path) -> None:
    path = tmp_path / "provider-credentials.key"
    path.write_bytes(os.urandom(32))
    path.chmod(0o644)

    with pytest.raises(CredentialKeyUnavailable, match="permissions"):
        InstallationCredentialKey(path).load_or_create()
