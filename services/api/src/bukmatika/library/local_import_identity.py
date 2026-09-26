import hashlib
from uuid import UUID


def local_import_source_key(principal_id: UUID, sha256: str) -> str:
    """Derive the principal-bound durable source identity for a local import."""

    digest_bytes = bytes.fromhex(sha256)
    if len(digest_bytes) != 32:
        raise ValueError("Local import SHA-256 must contain exactly 32 bytes")
    digest = hashlib.sha256()
    digest.update(principal_id.bytes)
    digest.update(digest_bytes)
    return digest.hexdigest()


__all__ = ["local_import_source_key"]
