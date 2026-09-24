from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.delegation_models import AIDelegationAttempt
from bukmatika.persistence.delegation_result_models import AIDelegationAttemptResult


@dataclass(frozen=True, slots=True)
class StoredDelegationResult:
    result: AIDelegationAttemptResult
    attempt: AIDelegationAttempt


class DelegationResultRepository:
    """Durable principal-scoped result receipts for completed delegated attempts."""

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
                select(AIDelegationAttemptResult, AIDelegationAttempt)
                .join(
                    AIDelegationAttempt,
                    AIDelegationAttempt.id == AIDelegationAttemptResult.attempt_id,
                )
                .where(
                    AIDelegationAttemptResult.principal_id == principal_id,
                    AIDelegationAttempt.principal_id == principal_id,
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
        return [StoredDelegationResult(result=result, attempt=attempt) for result, attempt in rows]
