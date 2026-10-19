import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import Principal
from bukmatika.persistence.secret_models import EncryptedSecret
from bukmatika.secrets import (
    DatabaseSecretStore,
    InstallationSecretKey,
    SecretIntegrityError,
    SecretOwner,
    SecretReference,
    SecretUnavailable,
)


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(
        kind="local",
        external_subject=f"secret-{suffix}-{uuid4()}",
    )
    session.add(principal)
    await session.flush()
    return principal


def _store(session: AsyncSession, key_path: Path) -> DatabaseSecretStore:
    return DatabaseSecretStore(
        session,
        installation_key=InstallationSecretKey(key_path),
    )


async def test_generic_secret_is_bound_to_principal_owner_and_slot(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "owner")
    owner_id = uuid4()
    owner = SecretOwner(
        principal_id=principal.id,
        owner_kind="plugin_connection",
        owner_id=owner_id,
        slot="database_password",
    )
    store = _store(session, tmp_path / "credential.key")
    secret = "database-secret"

    reference = await store.replace(owner=owner, secret=secret)
    row = await session.scalar(
        select(EncryptedSecret).where(
            EncryptedSecret.principal_id == principal.id,
            EncryptedSecret.owner_kind == "plugin_connection",
            EncryptedSecret.owner_id == owner_id,
            EncryptedSecret.slot == "database_password",
        )
    )

    assert row is not None
    assert row.aad_version == 2
    assert secret.encode() not in row.ciphertext
    assert await store.reveal(owner=owner, reference=reference) == secret


async def test_cross_principal_owner_and_slot_reveal_fail_closed(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "primary")
    other = await _principal(session, "other")
    owner = SecretOwner(
        principal_id=principal.id,
        owner_kind="plugin_connection",
        owner_id=uuid4(),
        slot="api_key",
    )
    store = _store(session, tmp_path / "credential.key")
    reference = await store.replace(owner=owner, secret="owner-only")

    wrong_owners = (
        SecretOwner(
            principal_id=other.id,
            owner_kind=owner.owner_kind,
            owner_id=owner.owner_id,
            slot=owner.slot,
        ),
        SecretOwner(
            principal_id=principal.id,
            owner_kind="ai_provider_connection",
            owner_id=owner.owner_id,
            slot=owner.slot,
        ),
        SecretOwner(
            principal_id=principal.id,
            owner_kind=owner.owner_kind,
            owner_id=owner.owner_id,
            slot="database_password",
        ),
    )
    for wrong_owner in wrong_owners:
        with pytest.raises(SecretUnavailable):
            await store.reveal(owner=wrong_owner, reference=reference)


async def test_rotation_invalidates_stale_generic_reference(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "rotation")
    owner = SecretOwner(
        principal_id=principal.id,
        owner_kind="plugin_connection",
        owner_id=uuid4(),
        slot="api_key",
    )
    store = _store(session, tmp_path / "credential.key")

    first = await store.replace(owner=owner, secret="first")
    second = await store.replace(owner=owner, secret="second")

    assert first.value != second.value
    with pytest.raises(SecretUnavailable):
        await store.reveal(owner=owner, reference=first)
    assert await store.reveal(owner=owner, reference=second) == "second"


async def test_ciphertext_swap_between_slots_fails_authentication(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "swap")
    owner_id = uuid4()
    first_owner = SecretOwner(
        principal_id=principal.id,
        owner_kind="plugin_connection",
        owner_id=owner_id,
        slot="api_key",
    )
    second_owner = SecretOwner(
        principal_id=principal.id,
        owner_kind="plugin_connection",
        owner_id=owner_id,
        slot="database_password",
    )
    store = _store(session, tmp_path / "credential.key")
    first = await store.replace(owner=first_owner, secret="first-secret")
    second = await store.replace(owner=second_owner, secret="second-secret")

    first_row = await session.scalar(
        select(EncryptedSecret).where(
            EncryptedSecret.id == UUID(first.value.removeprefix("secret:"))
        )
    )
    second_row = await session.scalar(
        select(EncryptedSecret).where(
            EncryptedSecret.id == UUID(second.value.removeprefix("secret:"))
        )
    )
    assert first_row is not None
    assert second_row is not None
    first_row.ciphertext = second_row.ciphertext
    first_row.nonce = second_row.nonce
    await session.flush()

    with pytest.raises(SecretIntegrityError):
        await store.reveal(owner=first_owner, reference=first)


async def test_delete_owner_destroys_all_owned_secret_slots(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "delete-owner")
    owner_id = uuid4()
    store = _store(session, tmp_path / "credential.key")
    for slot in ("api_key", "database_password"):
        await store.replace(
            owner=SecretOwner(
                principal_id=principal.id,
                owner_kind="plugin_connection",
                owner_id=owner_id,
                slot=slot,
            ),
            secret=f"secret-{slot}",
        )

    await store.delete_owner(
        principal_id=principal.id,
        owner_kind="plugin_connection",
        owner_id=owner_id,
    )

    rows = list(
        await session.scalars(
            select(EncryptedSecret).where(
                EncryptedSecret.principal_id == principal.id,
                EncryptedSecret.owner_kind == "plugin_connection",
                EncryptedSecret.owner_id == owner_id,
            )
        )
    )
    assert rows == []


def test_legacy_provider_reference_format_is_accepted() -> None:
    reference = SecretReference(f"credential:{uuid4()}")
    assert reference.value.startswith("credential:")


async def test_migrated_provider_ciphertext_uses_legacy_aad(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "legacy")
    owner_id = uuid4()
    secret_id = uuid4()
    nonce = os.urandom(12)
    secret = "legacy-provider-secret"
    key = InstallationSecretKey(tmp_path / "credential.key")
    ciphertext = AESGCM(key.load_or_create()).encrypt(
        nonce,
        secret.encode(),
        (
            f"bukmatika-provider-credential:v1:"
            f"{principal.id}:{owner_id}:{secret_id}"
        ).encode("ascii"),
    )
    session.add(
        EncryptedSecret(
            id=secret_id,
            principal_id=principal.id,
            owner_kind="ai_provider_connection",
            owner_id=owner_id,
            slot="credential",
            ciphertext=ciphertext,
            nonce=nonce,
            aad_version=1,
            key_version=1,
        )
    )
    await session.flush()

    store = DatabaseSecretStore(session, installation_key=key)
    revealed = await store.reveal(
        owner=SecretOwner(
            principal_id=principal.id,
            owner_kind="ai_provider_connection",
            owner_id=owner_id,
            slot="credential",
        ),
        reference=SecretReference(f"credential:{secret_id}"),
    )

    assert revealed == secret
