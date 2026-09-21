from bukmatika.domain import RightsEvidence, RightsState
from bukmatika.rights import RightsEngine


def test_no_evidence_is_fail_closed() -> None:
    decision = RightsEngine().decide([])
    assert decision.state is RightsState.UNKNOWN
    assert decision.unattended_acquisition_allowed is False


def test_restricted_evidence_overrides_permissive_evidence() -> None:
    decision = RightsEngine().decide(
        [
            RightsEvidence(
                state=RightsState.PUBLIC_DOMAIN,
                source="catalog",
                basis="Catalog public-domain flag",
                confidence=0.9,
            ),
            RightsEvidence(
                state=RightsState.RESTRICTED,
                source="asset",
                basis="Exact asset record marks access restricted",
                confidence=0.9,
            ),
        ]
    )
    assert decision.state is RightsState.RESTRICTED
    assert decision.unattended_acquisition_allowed is False


def test_public_domain_evidence_can_authorize_unattended_acquisition() -> None:
    decision = RightsEngine().decide(
        [
            RightsEvidence(
                state=RightsState.PUBLIC_DOMAIN,
                source="authoritative_source",
                basis="Explicit public-domain statement",
                confidence=1.0,
            )
        ]
    )
    assert decision.state is RightsState.PUBLIC_DOMAIN
    assert decision.unattended_acquisition_allowed is True
