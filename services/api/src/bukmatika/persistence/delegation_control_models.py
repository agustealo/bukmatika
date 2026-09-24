from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class AIDelegationConsent(Base, TimestampMixin):
    __tablename__ = "ai_delegation_consents"
    __mapper_args__: dict[str, object] = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_ai_delegation_consent_status",
        ),
        CheckConstraint(
            "scope = 'read_only'",
            name="ck_ai_delegation_consent_scope",
        ),
        CheckConstraint(
            "prior_autonomy_level BETWEEN 0 AND 1",
            name="ck_ai_delegation_consent_prior_level",
        ),
        UniqueConstraint("principal_id", name="uq_ai_delegation_consent_principal"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="read_only")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prior_autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False)
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
