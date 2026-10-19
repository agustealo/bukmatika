import contextlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.secret_models import EncryptedSecret

_REFERENCE_PREFIX = "credential:"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_MAX_SECRET_BYTES = 16_384
_KEY_VERSION = 1
_GENERIC_AAD_SCHEME = "owner_slot_v1"
_LEGACY_AI_AAD_SCHEME = "ai_provider_v1"
_SECRET_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")


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


@dataclass(frozen=True, slots=True)
class SecretOwner:
    principal_id: UUID
    owner_kind: str
    owner_id: UUID
    slot: str

    def __post_init__(self) -> None:
        _validate_secret_token(self.owner_kind, field="owner kind")
        _validate_secret_token(self.slot, field="slot")


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
            offset = 0
            while offset < len(key):
                written = os.write(descriptor, key[offset:])
                if written <= 0:
                    raise CredentialKeyUnavailable("Credential key write was incomplete")
                offset += written
            os.fsync(descriptor)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(self._path)
            raise
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


class DatabaseSecretStore:
    """Canonical encrypted secret owner for all principal-scoped connection types."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        installation_key: InstallationCredentialKey,
    ) -> None:
        self._session = session
        self._installation_key = installation_key

    async def replace(self, *, owner: SecretOwner, secret: str) -> CredentialReference:
        secret_bytes = secret.encode("utf-8")
        if not secret_bytes:
            raise ValueError("Credential secret must not be empty")
        if len(secret_bytes) > _MAX_SECRET_BYTES:
            raise ValueError("Credential secret exceeds the supported size")

        await self.delete(owner=owner)
        credential_id = uuid4()
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._installation_key.load_or_create()).encrypt(
            nonce,
            secret_bytes,
            _generic_associated_data(
                owner=owner,
                credential_id=credential_id,
                key_version=_KEY_VERSION,
            ),
        )
        credential = EncryptedSecret(
            id=credential_id,
            principal_id=owner.principal_id,
            owner_kind=owner.owner_kind,
            owner_id=owner.owner_id,
            slot=owner.slot,
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=_KEY_VERSION,
            aad_scheme=_GENERIC_AAD_SCHEME,
        )
        self._session.add(credential)
        await self._session.flush()
        return CredentialReference(f"{_REFERENCE_PREFIX}{credential_id}")

    async def reveal(
        self,
        *,
        owner: SecretOwner,
        reference: CredentialReference,
    ) -> str:
        credential_id = _parse_reference(reference.value)
        credential = await self._session.scalar(
            select(EncryptedSecret).where(
                EncryptedSecret.id == credential_id,
                EncryptedSecret.principal_id == owner.principal_id,
                EncryptedSecret.owner_kind == owner.owner_kind,
                EncryptedSecret.owner_id == owner.owner_id,
                EncryptedSecret.slot == owner.slot,
            )
        )
        if credential is None:
            raise CredentialUnavailable("Credential is unavailable")
        if credential.key_version != _KEY_VERSION:
            raise CredentialUnavailable("Credential key version is unsupported")

        associated_data = _associated_data_for_record(owner=owner, credential=credential)
        try:
            plaintext = AESGCM(self._installation_key.load_or_create()).decrypt(
                credential.nonce,
                credential.ciphertext,
                associated_data,
            )
        except InvalidTag as exc:
            raise CredentialIntegrityError("Credential authentication failed") from exc
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CredentialIntegrityError("Credential plaintext is invalid") from exc

    async def delete(self, *, owner: SecretOwner) -> None:
        await self._session.execute(
            delete(EncryptedSecret).where(
                EncryptedSecret.principal_id == owner.principal_id,
                EncryptedSecret.owner_kind == owner.owner_kind,
                EncryptedSecret.owner_id == owner.owner_id,
                EncryptedSecret.slot == owner.slot,
            )
        )
        await self._session.flush()

    async def delete_owner(
        self,
        *,
        principal_id: UUID,
        owner_kind: str,
        owner_id: UUID,
    ) -> None:
        _validate_secret_token(owner_kind, field="owner kind")
        await self._session.execute(
            delete(EncryptedSecret).where(
                EncryptedSecret.principal_id == principal_id,
                EncryptedSecret.owner_kind == owner_kind,
                EncryptedSecret.owner_id == owner_id,
            )
        )
        await self._session.flush()


def _parse_reference(value: str) -> UUID:
    if not value.startswith(_REFERENCE_PREFIX):
        raise ValueError("Credential reference has an invalid format")
    raw = value.removeprefix(_REFERENCE_PREFIX)
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ValueError("Credential reference has an invalid identifier") from exc


def _associated_data_for_record(
    *,
    owner: SecretOwner,
    credential: EncryptedSecret,
) -> bytes:
    if credential.aad_scheme == _GENERIC_AAD_SCHEME:
        return _generic_associated_data(
            owner=owner,
            credential_id=credential.id,
            key_version=credential.key_version,
        )
    if credential.aad_scheme == _LEGACY_AI_AAD_SCHEME:
        if owner.owner_kind != "ai_provider_connection" or owner.slot != "api_key":
            raise CredentialIntegrityError("Legacy credential ownership is invalid")
        return _legacy_ai_associated_data(
            principal_id=owner.principal_id,
            connection_id=owner.owner_id,
            credential_id=credential.id,
            key_version=credential.key_version,
        )
    raise CredentialUnavailable("Credential authentication scheme is unsupported")


def _generic_associated_data(
    *,
    owner: SecretOwner,
    credential_id: UUID,
    key_version: int,
) -> bytes:
    return (
        f"bukmatika-secret:v{key_version}:"
        f"{owner.principal_id}:{owner.owner_kind}:{owner.owner_id}:{owner.slot}:{credential_id}"
    ).encode("ascii")


def _legacy_ai_associated_data(
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


def _validate_secret_token(value: str, *, field: str) -> None:
    if _SECRET_TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"Secret {field} must be a lowercase identifier using letters, digits, '.', '_', or '-'"
        )
