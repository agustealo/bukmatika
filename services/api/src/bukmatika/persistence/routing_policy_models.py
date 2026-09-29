from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class AIRoutingPolicy(Base, TimestampMixin):
    __tablename__ = "ai_routing_policies"
    __table_args__ = (
        UniqueConstraint("principal_id", name="uq_ai_routing_policy_principal"),
        UniqueConstraint("user_model_id", name="uq_ai_routing_policy_user_model"),
        CheckConstraint(
            "model_selection_mode IN ('installation_default','profile')",
            name="ck_ai_routing_policy_selection",
        ),
        CheckConstraint(
            "cloud_egress_policy IN ('local_only','public_only','private_context')",
            name="ck_ai_routing_policy_egress",
        ),
        Index("ix_ai_routing_policies_principal", "principal_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    user_model_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_models.id", ondelete="CASCADE"), nullable=False
    )
    model_selection_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="installation_default"
    )
    cloud_egress_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="local_only"
    )
