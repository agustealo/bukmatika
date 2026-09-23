from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base


class ActionExecutionReceipt(Base):
    """Completed durable action result, unique per planned action decision."""

    __tablename__ = "action_execution_receipts"
    __table_args__ = (
        UniqueConstraint(
            "action_decision_id",
            name="uq_action_execution_receipt_action_decision",
        ),
        Index(
            "ix_action_execution_receipts_principal_time",
            "principal_id",
            "executed_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    action_decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("action_decisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
