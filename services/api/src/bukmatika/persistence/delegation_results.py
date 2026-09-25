from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.delegation_models import AIDelegation, AIDelegationAttempt
from bukmatika.persistence.delegation_result_models import AIDelegationAttemptResult
from bukmatika.persistence.personalization_models import Plan

_TERMINAL_NONRESULT_STATUSES = ("rejected", "stopped", "failed", "cancelled")


@dataclass(frozen=True, slots=True)
class StoredDelegationResult:
    result: AIDelegationAttemptResult
    attempt: AIDelegationAttempt
    delegation: AIDelegation


@dataclass(frozen=True, slots=True)
class StoredTerminalDelegation:
    delegation: AIDelegation
    plan: Plan
    attempt: AIDelegationAttempt | None


class DelegationResultRepository:
    """Durable principal-scoped result receipts and terminal delegation outcomes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def store_for_attempt(
        self,
        *,
        attempt: AIDelegationAttempt,
        capability: str,
        receipt: dict[str, object],
    ) -> AIDelegationAttemptResult:
        if attempt.status != "authorized":
            raise RuntimeError("Delegated result can only be recorded for an authorized attempt")
        result = await self._session.scalar(
            select(AIDelegationAttemptResult)
            .where(AIDelegationAttemptResult.attempt_id == attempt.id)
            .with_for_update()
        )
        if result is None:
            result = AIDelegationAttemptResult(
                principal_id=attempt.principal_id,
                delegation_id=attempt.delegation_id,
                attempt_id=attempt.id,
                capability=capability,
                receipt=receipt,
            )
            self._session.add(result)
        else:
            if (
                result.principal_id != attempt.principal_id
                or result.delegation_id != attempt.delegation_id
            ):
                raise RuntimeError("Delegated result receipt ownership is inconsistent")
            result.capability = capability
            result.receipt = receipt
        await self._session.flush()
        return result

    async def recent_completed(
        self,
        *,
        principal_id: UUID,
        limit: int,
    ) -> list[StoredDelegationResult]:
        rows = (
            await self._session.execute(
                select(AIDelegationAttemptResult, AIDelegationAttempt, AIDelegation)
                .join(
                    AIDelegationAttempt,
                    AIDelegationAttempt.id == AIDelegationAttemptResult.attempt_id,
                )
                .join(
                    AIDelegation,
                    and_(
                        AIDelegation.id == AIDelegationAttempt.delegation_id,
                        AIDelegation.principal_id == AIDelegationAttempt.principal_id,
                    ),
                )
                .where(
                    AIDelegationAttemptResult.principal_id == principal_id,
                    AIDelegationAttempt.principal_id == principal_id,
                    AIDelegation.principal_id == principal_id,
                    AIDelegationAttempt.status == "completed",
                    AIDelegationAttempt.finished_at.is_not(None),
                )
                .order_by(
                    AIDelegationAttempt.finished_at.desc(),
                    AIDelegationAttemptResult.created_at.desc(),
                )
                .limit(limit)
            )
        ).all()
        return [
            StoredDelegationResult(result=result, attempt=attempt, delegation=delegation)
            for result, attempt, delegation in rows
        ]

    async def recent_terminal(
        self,
        *,
        principal_id: UUID,
        limit: int,
    ) -> list[StoredTerminalDelegation]:
        terminal_at = func.coalesce(
            AIDelegation.completed_at,
            AIDelegation.stopped_at,
            AIDelegation.updated_at,
        )
        rows = (
            await self._session.execute(
                select(AIDelegation, Plan)
                .join(
                    Plan,
                    and_(
                        Plan.id == AIDelegation.plan_id,
                        Plan.principal_id == AIDelegation.principal_id,
                    ),
                )
                .where(
                    AIDelegation.principal_id == principal_id,
                    Plan.principal_id == principal_id,
                    AIDelegation.status.in_(_TERMINAL_NONRESULT_STATUSES),
                )
                .order_by(terminal_at.desc(), AIDelegation.id.desc())
                .limit(limit)
            )
        ).all()
        if not rows:
            return []

        delegation_ids = [delegation.id for delegation, _ in rows]
        attempts = list(
            (
                await self._session.scalars(
                    select(AIDelegationAttempt)
                    .where(
                        AIDelegationAttempt.principal_id == principal_id,
                        AIDelegationAttempt.delegation_id.in_(delegation_ids),
                    )
                    .order_by(
                        AIDelegationAttempt.delegation_id,
                        AIDelegationAttempt.finished_at.desc().nullslast(),
                        AIDelegationAttempt.authorized_at.desc(),
                        AIDelegationAttempt.id.desc(),
                    )
                )
            ).all()
        )
        latest_attempt: dict[UUID, AIDelegationAttempt] = {}
        for attempt in attempts:
            latest_attempt.setdefault(attempt.delegation_id, attempt)
        return [
            StoredTerminalDelegation(
                delegation=delegation,
                plan=plan,
                attempt=latest_attempt.get(delegation.id),
            )
            for delegation, plan in rows
        ]
