import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.secret_models import EncryptedSecret

_SECRET_REFERENCE_PREFIX = "secret:"
_LEGACY_CREDENTIAL_REFERENCE_PREFIX = "credential:"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_MAX_SECRET_BYTES = 16_384
_KEY_VERSION = 1
_GENERIC_AAD_VERSION = 2
_LEGACY_PROVIDER_AAD_VERSION = 1


class SecretUnavailable(LookupError):
    pass


class SecretIntegrityError(RuntimeError):
    pass


class SecretKeyUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SecretOwner:
    principal_id: UUID
    owner_kind: str
    owner_id: UUID
    slot: str

    def __post_init__(self) -> None:
        if not self.owner_kind.strip() or self.owner_kind != self.owner_kind.strip():
            raise ValueError("Secret owner_kind is invalid")
        if not self.slot.strip() or self.slot != self.slot.strip():
            raise ValueError("Secret slot is invalid")


@dataclass(frozen=True, slots=True)
class SecretReference:
    value: str

    def __post_init__(self) -> None:
        _parse_reference(self.value)


class InstallationSecretKey:
    """Installation-owned AES-256 key kept outside PostgreSQL and exports."""

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
            raise SecretKeyUnavailable("Secret key could not be created") from exc

        try:
            written = os.write(descriptor, key)
            if written != len(key):
                raise SecretKeyUnavailable("Secret key write was incomplete")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return key

    def _ensure_parent(self) -> None:
        try:
            self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._path.parent, 0o700)
        except OSError as exc:
            raise SecretKeyUnavailable("Secret key directory is unavailable") from exc

    def _read_existing(self) -> bytes:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise SecretKeyUnavailable("Secret key could not be opened safely") from exc

        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise SecretKeyUnavailable("Secret key must be a regular file")
            if metadata.st_mode & 0o077:
                raise SecretKeyUnavailable("Secret key permissions are too broad")
            key = os.read(descriptor, _KEY_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(key) != _KEY_BYTES:
            raise SecretKeyUnavailable("Secret key has an invalid length")
        return key


class DatabaseSecretStore:
    """Canonical encrypted secret owner for all principal-scoped integrations."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        installation_key: InstallationSecretKey,
    ) -> None:
        self._session = session
        self._installation_key = installation_key

    async def replace(self, *, owner: SecretOwner, secret: str) -> SecretReference:
        secret_bytes = secret.encode("utf-8")
        if not secret_bytes:
            raise ValueError("Secret must not be empty")
        if len(secret_bytes) > _MAX_SECRET_BYTES:
            raise ValueError("Secret exceeds the supported size")

        existing = await self._row(owner)
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()

        secret_id = uuid4()
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._installation_key.load_or_create()).encrypt(
            nonce,
            secret_bytes,
            _generic_associated_data(
                owner=owner,
                secret_id=secret_id,
                key_version=_KEY_VERSION,
            ),
        )
        row = EncryptedSecret(
            id=secret_id,
            principal_id=owner.principal_id,
            owner_kind=owner.owner_kind,
            owner_id=owner.owner_id,
            slot=owner.slot,
            ciphertext=ciphertext,
            nonce=nonce,
            aad_version=_GENERIC_AAD_VERSION,
            key_version=_KEY_VERSION,
        )
        self._session.add(row)
        await self._session.flush()
        return SecretReference(f"{_SECRET_REFERENCE_PREFIX}{secret_id}")

    async def reveal(
        self,
        *,
        owner: SecretOwner,
        reference: SecretReference,
    ) -> str:
        secret_id = _parse_reference(reference.value)
        row = await self._session.scalar(
            select(EncryptedSecret).where(
                EncryptedSecret.id == secret_id,
                EncryptedSecret.principal_id == owner.principal_id,
                EncryptedSecret.owner_kind == owner.owner_kind,
                EncryptedSecret.owner_id == owner.owner_id,
                EncryptedSecret.slot == owner.slot,
            )
        )
        if row is None:
            raise SecretUnavailable("Secret is unavailable")
        if row.key_version != _KEY_VERSION:
            raise SecretUnavailable("Secret key version is unsupported")

        associated_data = _associated_data(owner=owner, row=row)
        try:
            plaintext = AESGCM(self._installation_key.load_or_create()).decrypt(
                row.nonce,
                row.ciphertext,
                associated_data,
            )
        except InvalidTag as exc:
            raise SecretIntegrityError("Secret authentication failed") from exc
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretIntegrityError("Secret plaintext is invalid") from exc

    async def delete(self, *, owner: SecretOwner) -> None:
        row = await self._row(owner)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()

    async def delete_owner(
        self,
        *,
        principal_id: UUID,
        owner_kind: str,
        owner_id: UUID,
    ) -> None:
        await self._session.execute(
            delete(EncryptedSecret).where(
                EncryptedSecret.principal_id == principal_id,
                EncryptedSecret.owner_kind == owner_kind,
                EncryptedSecret.owner_id == owner_id,
            )
        )
        await self._session.flush()

    async def _row(self, owner: SecretOwner) -> EncryptedSecret | None:
        return await self._session.scalar(
            select(EncryptedSecret).where(
                EncryptedSecret.principal_id == owner.principal_id,
                EncryptedSecret.owner_kind == owner.owner_kind,
                EncryptedSecret.owner_id == owner.owner_id,
                EncryptedSecret.slot == owner.slot,
            )
        )


def _parse_reference(value: str) -> UUID:
    prefixes = (_SECRET_REFERENCE_PREFIX, _LEGACY_CREDENTIAL_REFERENCE_PREFIX)
    prefix = next((item for item in prefixes if value.startswith(item)), None)
    if prefix is None:
        raise ValueError("Secret reference has an invalid format")
    raw = value.removeprefix(prefix)
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ValueError("Secret reference has an invalid identifier") from exc


def _associated_data(*, owner: SecretOwner, row: EncryptedSecret) -> bytes:
    if row.aad_version == _GENERIC_AAD_VERSION:
        return _generic_associated_data(
            owner=owner,
            secret_id=row.id,
            key_version=row.key_version,
        )
    if (
        row.aad_version == _LEGACY_PROVIDER_AAD_VERSION
        and owner.owner_kind == "ai_provider_connection"
        and owner.slot == "credential"
    ):
        return (
            f"bukmatika-provider-credential:v{row.key_version}:"
            f"{owner.principal_id}:{owner.owner_id}:{row.id}"
        ).encode("ascii")
    raise SecretUnavailable("Secret authentication metadata is unsupported")


def _generic_associated_data(
    *,
    owner: SecretOwner,
    secret_id: UUID,
    key_version: int,
) -> bytes:
    return (
        f"bukmatika-secret:v{_GENERIC_AAD_VERSION}:k{key_version}:"
        f"{owner.principal_id}:{owner.owner_kind}:{owner.owner_id}:{owner.slot}:{secret_id}"
    ).encode("ascii")
