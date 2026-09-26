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


@dataclass(frozen=True, slots=True)
class LocalImportRightsDecision:
    state: RightsState
    permissions: dict[str, bool]
    jurisdiction: str
    policy_version: str
    reason: str


class RightsEngine:
    """Canonical policy for network acquisition and private user-supplied retention.

    Provider evidence controls unattended network acquisition. User-supplied local bytes use a
    separate private-retention decision that never upgrades unknown copyright/license state into
    download, export, or sharing authority.
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

    def decide_user_supplied_local_import(self) -> LocalImportRightsDecision:
        return LocalImportRightsDecision(
            state=RightsState.UNKNOWN,
            permissions={
                "discover": True,
                "display_metadata": True,
                "download": False,
                "retain": True,
                "process": True,
                "ocr": True,
                "export": False,
                "share": False,
            },
            jurisdiction="US",
            policy_version="local-import-private-v1",
            reason=(
                "The user explicitly supplied these bytes for private local retention and "
                "processing. Copyright and license status remain unknown; no download, export, "
                "or sharing authority is inferred."
            ),
        )

    @staticmethod
    def _priority(item: RightsEvidence) -> float:
        if item.state in _DENY_PRIORITY:
            base = _DENY_PRIORITY[item.state]
        else:
            base = _ALLOW_PRIORITY.get(item.state, 0)
        return base + item.confidence


__all__ = ["LocalImportRightsDecision", "RightsDecision", "RightsEngine"]
