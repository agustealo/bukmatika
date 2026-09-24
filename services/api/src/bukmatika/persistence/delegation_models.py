from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class AIDelegation(Base, TimestampMixin):
    __tablename__ = "ai_delegations"
    __mapper_args__: ClassVar[dict[str, object]] = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed','approved','rejected','running','stop_requested',"
            "'stopped','completed','failed','cancelled')",
            name="ck_ai_delegation_status",
        ),
        CheckConstraint(
            "max_runtime_seconds BETWEEN 30 AND 3600",
            name="ck_ai_delegation_runtime_budget",
        ),
        CheckConstraint(
            "max_retries_per_step BETWEEN 0 AND 3",
            name="ck_ai_delegation_retry_budget",
        ),
        CheckConstraint(
            "max_total_attempts BETWEEN 1 AND 48",
            name="ck_ai_delegation_attempt_budget",
        ),
        CheckConstraint("attempts_used >= 0", name="ck_ai_delegation_attempts_used"),
        CheckConstraint("current_step_index >= 0", name="ck_ai_delegation_step_index"),
        Index("ix_ai_delegations_principal_status", "principal_id", "status", "updated_at"),
        Index("ix_ai_delegations_plan", "plan_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="proposed")
    selected_step_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    delegation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    max_runtime_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_retries_per_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_total_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(128))


class AIDelegationApproval(Base):
    __tablename__ = "ai_delegation_approvals"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_ai_delegation_approval_decision",
        ),
        UniqueConstraint("delegation_id", name="uq_ai_delegation_approval_delegation"),
        Index(
            "ix_ai_delegation_approvals_principal_time",
            "principal_id",
            "decided_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    delegation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ai_delegations.id", ondelete="CASCADE"), nullable=False
    )
    delegation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AIDelegationAttempt(Base):
    __tablename__ = "ai_delegation_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('authorized','completed','failed','cancelled')",
            name="ck_ai_delegation_attempt_status",
        ),
        CheckConstraint("attempt_number >= 1", name="ck_ai_delegation_attempt_number"),
        UniqueConstraint(
            "delegation_id",
            "step_id",
            "attempt_number",
            name="uq_ai_delegation_step_attempt",
        ),
        Index(
            "ix_ai_delegation_attempts_delegation_status",
            "delegation_id",
            "status",
            "authorized_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    delegation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ai_delegations.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="authorized")
    error_code: Mapped[str | None] = mapped_column(String(128))
    authorized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
