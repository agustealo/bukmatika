import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import (
    CredentialIntegrityError,
    CredentialReference,
    CredentialUnavailable,
    DatabaseSecretStore,
    InstallationCredentialKey,
    SecretOwner,
)
from bukmatika.persistence.models import Principal
from bukmatika.persistence.secret_models import EncryptedSecret


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(
        kind="local",
        external_subject=f"general-secret-{suffix}-{uuid4()}",
    )
    session.add(principal)
    await session.flush()
    return principal


def _store(session: AsyncSession, key_path: Path) -> DatabaseSecretStore:
    return DatabaseSecretStore(
        session,
        installation_key=InstallationCredentialKey(key_path),
    )


async def test_secret_reference_is_scoped_to_owner_and_slot(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "scope")
    owner_id = uuid4()
    owner = SecretOwner(
        principal_id=principal.id,
        owner_kind="wordpress_connection",
        owner_id=owner_id,
        slot="application_password",
    )
    store = _store(session, tmp_path / "credential.key")
    reference = await store.replace(owner=owner, secret="wp-secret")

    with pytest.raises(CredentialUnavailable):
        await store.reveal(
            owner=SecretOwner(
                principal_id=principal.id,
                owner_kind="wordpress_connection",
                owner_id=uuid4(),
                slot="application_password",
            ),
            reference=reference,
        )

    with pytest.raises(CredentialUnavailable):
        await store.reveal(
            owner=SecretOwner(
                principal_id=principal.id,
                owner_kind="wordpress_connection",
                owner_id=owner_id,
                slot="database_password",
            ),
            reference=reference,
        )


async def test_secret_reference_is_principal_scoped(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner_principal = await _principal(session, "owner")
    other_principal = await _principal(session, "other")
    owner_id = uuid4()
    store = _store(session, tmp_path / "credential.key")
    reference = await store.replace(
        owner=SecretOwner(
            principal_id=owner_principal.id,
            owner_kind="wordpress_connection",
            owner_id=owner_id,
            slot="application_password",
        ),
        secret="principal-secret",
    )

    with pytest.raises(CredentialUnavailable):
        await store.reveal(
            owner=SecretOwner(
                principal_id=other_principal.id,
                owner_kind="wordpress_connection",
                owner_id=owner_id,
                slot="application_password",
            ),
            reference=reference,
        )


async def test_ciphertext_swap_between_owner_slots_fails_authentication(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "swap")
    owner_id = uuid4()
    first_owner = SecretOwner(
        principal_id=principal.id,
        owner_kind="wordpress_connection",
        owner_id=owner_id,
        slot="application_password",
    )
    second_owner = SecretOwner(
        principal_id=principal.id,
        owner_kind="wordpress_connection",
        owner_id=owner_id,
        slot="database_password",
    )
    store = _store(session, tmp_path / "credential.key")
    first_reference = await store.replace(owner=first_owner, secret="first-secret")
    second_reference = await store.replace(owner=second_owner, secret="second-secret")

    first_id = UUID(first_reference.value.removeprefix("credential:"))
    second_id = UUID(second_reference.value.removeprefix("credential:"))
    first = await session.get(EncryptedSecret, first_id)
    second = await session.get(EncryptedSecret, second_id)
    assert first is not None
    assert second is not None

    first.ciphertext, second.ciphertext = second.ciphertext, first.ciphertext
    first.nonce, second.nonce = second.nonce, first.nonce
    await session.flush()

    with pytest.raises(CredentialIntegrityError):
        await store.reveal(owner=first_owner, reference=first_reference)
    with pytest.raises(CredentialIntegrityError):
        await store.reveal(owner=second_owner, reference=second_reference)


async def test_delete_owner_removes_all_slots_without_touching_other_owner(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "delete-owner")
    owner_id = uuid4()
    other_owner_id = uuid4()
    store = _store(session, tmp_path / "credential.key")

    for slot in ("application_password", "database_password"):
        await store.replace(
            owner=SecretOwner(
                principal_id=principal.id,
                owner_kind="wordpress_connection",
                owner_id=owner_id,
                slot=slot,
            ),
            secret=f"{slot}-secret",
        )
    other_reference = await store.replace(
        owner=SecretOwner(
            principal_id=principal.id,
            owner_kind="wordpress_connection",
            owner_id=other_owner_id,
            slot="application_password",
        ),
        secret="keep-me",
    )

    await store.delete_owner(
        principal_id=principal.id,
        owner_kind="wordpress_connection",
        owner_id=owner_id,
    )

    removed = list(
        await session.scalars(
            select(EncryptedSecret).where(
                EncryptedSecret.principal_id == principal.id,
                EncryptedSecret.owner_kind == "wordpress_connection",
                EncryptedSecret.owner_id == owner_id,
            )
        )
    )
    assert removed == []
    assert await store.reveal(
        owner=SecretOwner(
            principal_id=principal.id,
            owner_kind="wordpress_connection",
            owner_id=other_owner_id,
            slot="application_password",
        ),
        reference=other_reference,
    ) == "keep-me"


async def test_legacy_ai_ciphertext_remains_decryptable_after_schema_migration(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "legacy")
    connection_id = uuid4()
    credential_id = uuid4()
    nonce = os.urandom(12)
    key_path = tmp_path / "credential.key"
    key = InstallationCredentialKey(key_path).load_or_create()
    plaintext = b"legacy-provider-secret"
    aad = (
        f"bukmatika-provider-credential:v1:"
        f"{principal.id}:{connection_id}:{credential_id}"
    ).encode("ascii")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    session.add(
        EncryptedSecret(
            id=credential_id,
            principal_id=principal.id,
            owner_kind="ai_provider_connection",
            owner_id=connection_id,
            slot="api_key",
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=1,
            aad_scheme="ai_provider_v1",
        )
    )
    await session.flush()

    revealed = await _store(session, key_path).reveal(
        owner=SecretOwner(
            principal_id=principal.id,
            owner_kind="ai_provider_connection",
            owner_id=connection_id,
            slot="api_key",
        ),
        reference=CredentialReference(f"credential:{credential_id}"),
    )
    assert revealed == plaintext.decode()
