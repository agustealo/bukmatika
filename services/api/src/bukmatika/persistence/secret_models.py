from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class EncryptedSecret(Base, TimestampMixin):
    __tablename__ = "encrypted_secrets"
    __table_args__ = (
        CheckConstraint(
            "owner_kind ~ '^[a-z0-9][a-z0-9_.-]{0,63}        CheckConstraint("key_version > 0", name="ck_encrypted_secret_key_version"),
        CheckConstraint("length(btrim(aad_scheme)) > 0", name="ck_encrypted_secret_aad_scheme"),
        UniqueConstraint(
            "principal_id",
            "owner_kind",
            "owner_id",
            "slot",
            name="uq_encrypted_secret_owner_slot",
        ),
        Index(
            "ix_encrypted_secrets_owner",
            "principal_id",
            "owner_kind",
            "owner_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    aad_scheme: Mapped[str] = mapped_column(String(64), nullable=False, default="owner_slot_v1")
",
            name="ck_encrypted_secret_owner_kind",
        ),
        CheckConstraint(
            "slot ~ '^[a-z0-9][a-z0-9_.-]{0,63}        CheckConstraint("key_version > 0", name="ck_encrypted_secret_key_version"),
        CheckConstraint("length(btrim(aad_scheme)) > 0", name="ck_encrypted_secret_aad_scheme"),
        UniqueConstraint(
            "principal_id",
            "owner_kind",
            "owner_id",
            "slot",
            name="uq_encrypted_secret_owner_slot",
        ),
        Index(
            "ix_encrypted_secrets_owner",
            "principal_id",
            "owner_kind",
            "owner_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    aad_scheme: Mapped[str] = mapped_column(String(64), nullable=False, default="owner_slot_v1")
",
            name="ck_encrypted_secret_slot",
        ),
        CheckConstraint("key_version > 0", name="ck_encrypted_secret_key_version"),
        CheckConstraint("length(btrim(aad_scheme)) > 0", name="ck_encrypted_secret_aad_scheme"),
        UniqueConstraint(
            "principal_id",
            "owner_kind",
            "owner_id",
            "slot",
            name="uq_encrypted_secret_owner_slot",
        ),
        Index(
            "ix_encrypted_secrets_owner",
            "principal_id",
            "owner_kind",
            "owner_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    aad_scheme: Mapped[str] = mapped_column(String(64), nullable=False, default="owner_slot_v1")
