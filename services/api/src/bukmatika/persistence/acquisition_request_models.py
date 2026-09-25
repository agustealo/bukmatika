from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class AcquisitionPolicy(Base, TimestampMixin):
    __tablename__ = "acquisition_policies"
    __table_args__ = (
        CheckConstraint(
            "approval_mode IN ('always_ask','auto_eligible')",
            name="ck_acquisition_policy_approval_mode",
        ),
    )

    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    approval_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="always_ask")


class AcquisitionRequest(Base, TimestampMixin):
    __tablename__ = "acquisition_requests"
    __table_args__ = (
        UniqueConstraint("principal_id", "asset_id", name="uq_acquisition_request_principal_asset"),
        CheckConstraint(
            "approval_mode IN ('always_ask','auto_eligible')",
            name="ck_acquisition_request_approval_mode",
        ),
        Index("ix_acquisition_requests_asset_active", "asset_id", "cancelled_at", "approved_at"),
        Index("ix_acquisition_requests_principal_updated", "principal_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    acquisition_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("acquisitions.id", ondelete="SET NULL"),
    )
    approval_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
