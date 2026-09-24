from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.delegation_control_models import AIDelegationConsent
from bukmatika.persistence.delegation_models import AIDelegation

_ACTIVE_STATUSES = ("proposed", "approved", "running", "stop_requested")


class DelegationControlRepository:
    """Principal-scoped persistence authority for Level 2 consent and active delegation state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def consent(
        self,
        *,
        principal_id: UUID,
        lock: bool = False,
    ) -> AIDelegationConsent | None:
        statement = select(AIDelegationConsent).where(
            AIDelegationConsent.principal_id == principal_id
        )
        if lock:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def create_consent(
        self,
        *,
        principal_id: UUID,
        policy_version: str,
        prior_autonomy_level: int,
    ) -> AIDelegationConsent:
        consent = AIDelegationConsent(
            principal_id=principal_id,
            status="active",
            scope="read_only",
            policy_version=policy_version,
            prior_autonomy_level=prior_autonomy_level,
        )
        self._session.add(consent)
        await self._session.flush()
        return consent

    async def active_delegations(
        self,
        *,
        principal_id: UUID,
        lock: bool = False,
    ) -> list[AIDelegation]:
        statement = (
            select(AIDelegation)
            .where(
                AIDelegation.principal_id == principal_id,
                AIDelegation.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(AIDelegation.created_at, AIDelegation.id)
        )
        if lock:
            statement = statement.with_for_update()
        return list((await self._session.scalars(statement)).all())

    async def revoke_consent_and_stop_active(
        self,
        *,
        principal_id: UUID,
        reason: str,
    ) -> tuple[AIDelegationConsent | None, list[AIDelegation]]:
        del reason
        consent = await self.consent(principal_id=principal_id, lock=True)
        now = datetime.now(UTC)
        if consent is not None and consent.status == "active":
            consent.status = "revoked"
            consent.revoked_at = now

        changed: list[AIDelegation] = []
        for delegation in await self.active_delegations(principal_id=principal_id, lock=True):
            if delegation.status in {"proposed", "approved"}:
                delegation.status = "cancelled"
                delegation.stopped_at = now
                changed.append(delegation)
            elif delegation.status == "running":
                delegation.status = "stop_requested"
                delegation.stop_requested_at = now
                changed.append(delegation)
        await self._session.flush()
        return consent, changed
