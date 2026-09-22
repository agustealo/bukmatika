from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class UserModel(Base, TimestampMixin):
    __tablename__ = "user_models"
    __table_args__ = (
        UniqueConstraint("principal_id", name="uq_user_model_principal"),
        CheckConstraint("autonomy_level BETWEEN 0 AND 3", name="ck_user_model_autonomy_level"),
        CheckConstraint(
            "model_provider_override IS NULL OR model_provider_override IN ('none','ollama')",
            name="ck_user_model_model_provider_override",
        ),
        CheckConstraint(
            "(model_provider_override IS NULL AND model_name_override IS NULL) OR "
            "(model_provider_override = 'none' AND model_name_override IS NULL) OR "
            "(model_provider_override = 'ollama' AND model_name_override IS NOT NULL "
            "AND length(btrim(model_name_override)) > 0)",
            name="ck_user_model_model_override_consistency",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    ai_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    learning_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_provider_override: Mapped[str | None] = mapped_column(String(16))
    model_name_override: Mapped[str | None] = mapped_column(String(255))


class PreferenceClaim(Base, TimestampMixin):
    __tablename__ = "preference_claims"
    __table_args__ = (
        CheckConstraint(
            "source IN ('explicit','inferred')",
            name="ck_preference_claim_source",
        ),
        CheckConstraint(
            "status IN ('active','contradicted','superseded','expired','deleted')",
            name="ck_preference_claim_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_preference_claim_confidence",
        ),
        CheckConstraint(
            "evidence_count >= 0",
            name="ck_preference_claim_evidence_count",
        ),
        CheckConstraint(
            "decay_half_life_days IS NULL OR decay_half_life_days > 0",
            name="ck_preference_claim_decay",
        ),
        Index("ix_preference_claims_principal_status", "principal_id", "status", "updated_at"),
        Index(
            "uq_preference_claim_active_scope",
            "principal_id",
            "key",
            "scope_type",
            "scope_value",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_model_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_models.id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, default="global")
    scope_value: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_reinforced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decay_half_life_days: Mapped[float | None] = mapped_column(Float)
    influence: Mapped[dict[str, bool]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {"ranking": True, "presentation": True, "automation": False},
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PreferenceClaimEvidence(Base):
    __tablename__ = "preference_claim_evidence"

    preference_claim_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("preference_claims.id", ondelete="CASCADE"),
        primary_key=True,
    )
    interaction_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("interaction_events.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class Goal(Base, TimestampMixin):
    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','completed','cancelled')",
            name="ck_goal_status",
        ),
        Index("ix_goals_principal_status", "principal_id", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="research")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed','approved','running','completed','failed','cancelled')",
            name="ck_plan_status",
        ),
        Index("ix_plans_principal_status", "principal_id", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    goal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("goals.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    user_request: Mapped[str] = mapped_column(Text, nullable=False)
    planner_version: Mapped[str] = mapped_column(String(64), nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    context_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ActionDecision(Base):
    __tablename__ = "action_decisions"
    __table_args__ = (
        CheckConstraint(
            (
                "decision IN ('allow','allow_with_notification',"
                "'require_approval','deny')"
            ),
            name="ck_action_decision_value",
        ),
        Index("ix_action_decisions_plan", "plan_id", "evaluated_at"),
        UniqueConstraint("plan_id", "step_id", name="uq_action_decision_plan_step"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OutcomeEvent(Base):
    __tablename__ = "outcome_events"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('helped','accepted','ignored','rejected','undone','corrected','failed')",
            name="ck_outcome_event_value",
        ),
        Index("ix_outcome_events_principal_time", "principal_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plans.id", ondelete="SET NULL")
    )
    action_decision_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("action_decisions.id", ondelete="SET NULL")
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
