from dataclasses import dataclass

from bukmatika.domain import RightsEvidence, RightsState


_AUTO_ACQUIRE = {
    RightsState.PUBLIC_DOMAIN,
    RightsState.OPEN_LICENSE,
    RightsState.AUTHORIZED_DOWNLOAD,
}

_DENY_PRIORITY = {
    RightsState.RESTRICTED: 100,
    RightsState.BORROW_ONLY: 80,
    RightsState.PREVIEW_ONLY: 70,
    RightsState.UNKNOWN: 10,
}

_ALLOW_PRIORITY = {
    RightsState.PUBLIC_DOMAIN: 60,
    RightsState.OPEN_LICENSE: 60,
    RightsState.AUTHORIZED_DOWNLOAD: 50,
}


@dataclass(frozen=True, slots=True)
class RightsDecision:
    state: RightsState
    unattended_acquisition_allowed: bool
    evidence: tuple[RightsEvidence, ...]
    reason: str


class RightsEngine:
    """Canonical unattended-acquisition policy.

    Adapters supply evidence. This engine makes the decision so provider code can never
    silently become an authorization bypass.
    """

    def decide(self, evidence: list[RightsEvidence]) -> RightsDecision:
        if not evidence:
            return RightsDecision(
                state=RightsState.UNKNOWN,
                unattended_acquisition_allowed=False,
                evidence=(),
                reason="No rights evidence was supplied.",
            )

        ordered = tuple(sorted(evidence, key=self._priority, reverse=True))
        state = ordered[0].state
        allowed = state in _AUTO_ACQUIRE
        return RightsDecision(
            state=state,
            unattended_acquisition_allowed=allowed,
            evidence=ordered,
            reason=(
                "Highest-priority rights evidence permits unattended acquisition."
                if allowed
                else "Rights evidence does not permit unattended acquisition."
            ),
        )

    @staticmethod
    def _priority(item: RightsEvidence) -> float:
        if item.state in _DENY_PRIORITY:
            base = _DENY_PRIORITY[item.state]
        else:
            base = _ALLOW_PRIORITY.get(item.state, 0)
        return base + item.confidence
