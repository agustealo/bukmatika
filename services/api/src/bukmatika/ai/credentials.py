import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.provider_models import AIProviderCredential
from bukmatika.persistence.providers import ProviderConnectionRepository

_CREDENTIAL_REFERENCE_PREFIX = "credential:"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_MAX_SECRET_BYTES = 16_384
_KEY_VERSION = 1


class CredentialUnavailable(LookupError):
    pass


class CredentialIntegrityError(RuntimeError):
    pass


class CredentialKeyUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CredentialReference:
    value: str

    def __post_init__(self) -> None:
        _parse_reference(self.value)


class CredentialStore(Protocol):
    async def replace(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        secret: str,
    ) -> CredentialReference: ...

    async def reveal(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        reference: CredentialReference,
    ) -> str: ...

    async def delete(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
    ) -> None: ...


class InstallationCredentialKey:
    """Installation-owned AES-256 key kept outside PostgreSQL and portability bundles."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load_or_create(self) -> bytes:
        self._ensure_parent()
        try:
            return self._read_existing()
        except FileNotFoundError:
            pass

        key = os.urandom(_KEY_BYTES)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except FileExistsError:
            return self._read_existing()
        except OSError as exc:
            raise CredentialKeyUnavailable("Credential key could not be created") from exc

        try:
            written = os.write(descriptor, key)
            if written != len(key):
                raise CredentialKeyUnavailable("Credential key write was incomplete")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return key

    def _ensure_parent(self) -> None:
        try:
            self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._path.parent, 0o700)
        except OSError as exc:
            raise CredentialKeyUnavailable("Credential key directory is unavailable") from exc

    def _read_existing(self) -> bytes:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise CredentialKeyUnavailable("Credential key could not be opened safely") from exc

        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise CredentialKeyUnavailable("Credential key must be a regular file")
            if metadata.st_mode & 0o077:
                raise CredentialKeyUnavailable("Credential key permissions are too broad")
            key = os.read(descriptor, _KEY_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(key) != _KEY_BYTES:
            raise CredentialKeyUnavailable("Credential key has an invalid length")
        return key


class DatabaseCredentialStore:
    """Canonical encrypted provider-secret owner.

    PostgreSQL stores authenticated ciphertext and principal/connection ownership. The installation
    key remains outside the database. Raw secrets are returned only from the internal `reveal` method.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        installation_key: InstallationCredentialKey,
    ) -> None:
        self._session = session
        self._installation_key = installation_key

    async def replace(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        secret: str,
    ) -> CredentialReference:
        secret_bytes = secret.encode("utf-8")
        if not secret_bytes:
            raise ValueError("Credential secret must not be empty")
        if len(secret_bytes) > _MAX_SECRET_BYTES:
            raise ValueError("Credential secret exceeds the supported size")

        connection = await ProviderConnectionRepository(self._session).get_connection(
            principal_id=principal_id,
            connection_id=connection_id,
        )
        existing = await self._session.scalar(
            select(AIProviderCredential).where(
                AIProviderCredential.principal_id == principal_id,
                AIProviderCredential.connection_id == connection_id,
            )
        )
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()

        credential_id = uuid4()
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._installation_key.load_or_create()).encrypt(
            nonce,
            secret_bytes,
            _associated_data(
                principal_id=principal_id,
                connection_id=connection_id,
                credential_id=credential_id,
                key_version=_KEY_VERSION,
            ),
        )
        credential = AIProviderCredential(
            id=credential_id,
            principal_id=principal_id,
            connection_id=connection_id,
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=_KEY_VERSION,
        )
        self._session.add(credential)
        reference = CredentialReference(f"{_CREDENTIAL_REFERENCE_PREFIX}{credential_id}")
        connection.credential_reference = reference.value
        await self._session.flush()
        return reference

    async def reveal(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        reference: CredentialReference,
    ) -> str:
        credential_id = _parse_reference(reference.value)
        connection = await ProviderConnectionRepository(self._session).get_connection(
            principal_id=principal_id,
            connection_id=connection_id,
            enabled_only=True,
        )
        if connection.credential_reference != reference.value:
            raise CredentialUnavailable("Credential reference is no longer active")
        credential = await self._session.scalar(
            select(AIProviderCredential).where(
                AIProviderCredential.id == credential_id,
                AIProviderCredential.principal_id == principal_id,
                AIProviderCredential.connection_id == connection_id,
            )
        )
        if credential is None:
            raise CredentialUnavailable("Credential is unavailable")
        if credential.key_version != _KEY_VERSION:
            raise CredentialUnavailable("Credential key version is unsupported")
        try:
            plaintext = AESGCM(self._installation_key.load_or_create()).decrypt(
                credential.nonce,
                credential.ciphertext,
                _associated_data(
                    principal_id=principal_id,
                    connection_id=connection_id,
                    credential_id=credential.id,
                    key_version=credential.key_version,
                ),
            )
        except InvalidTag as exc:
            raise CredentialIntegrityError("Credential authentication failed") from exc
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CredentialIntegrityError("Credential plaintext is invalid") from exc

    async def delete(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
    ) -> None:
        connection = await ProviderConnectionRepository(self._session).get_connection(
            principal_id=principal_id,
            connection_id=connection_id,
        )
        credential = await self._session.scalar(
            select(AIProviderCredential).where(
                AIProviderCredential.principal_id == principal_id,
                AIProviderCredential.connection_id == connection_id,
            )
        )
        if credential is not None:
            await self._session.delete(credential)
        connection.credential_reference = None
        await self._session.flush()


def _parse_reference(value: str) -> UUID:
    if not value.startswith(_CREDENTIAL_REFERENCE_PREFIX):
        raise ValueError("Credential reference has an invalid format")
    raw = value.removeprefix(_CREDENTIAL_REFERENCE_PREFIX)
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ValueError("Credential reference has an invalid identifier") from exc


def _associated_data(
    *,
    principal_id: UUID,
    connection_id: UUID,
    credential_id: UUID,
    key_version: int,
) -> bytes:
    return (
        f"bukmatika-provider-credential:v{key_version}:"
        f"{principal_id}:{connection_id}:{credential_id}"
    ).encode("ascii")
