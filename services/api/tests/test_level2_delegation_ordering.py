from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from test_ai_delegation import _plan, _principal, _proposal, _scope, _step

from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_control import DelegationOperatorControlService
from bukmatika.ai.domain import CapabilityName
from bukmatika.persistence.delegation_models import AIDelegation


async def test_active_delegations_surface_newest_first(session: AsyncSession) -> None:
    principal = await _principal(session, "level2-newest-first")
    first_plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    second_plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    first = await control.propose(
        principal_id=principal.id,
        plan_id=first_plan.id,
        request=_proposal("research"),
    )
    second = await control.propose(
        principal_id=principal.id,
        plan_id=second_plan.id,
        request=_proposal("research"),
    )

    first_row = await session.get(AIDelegation, first.delegation_id)
    second_row = await session.get(AIDelegation, second.delegation_id)
    assert first_row is not None
    assert second_row is not None

    now = datetime.now(UTC)
    first_row.created_at = now - timedelta(minutes=1)
    second_row.created_at = now
    await session.flush()

    status = await DelegationOperatorControlService(
        session_scope_factory=_scope(session),
        delegation_service=control,
    ).status(principal_id=principal.id)

    assert [item.delegation_id for item in status.active_delegations] == [
        second.delegation_id,
        first.delegation_id,
    ]
