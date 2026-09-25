# Discovery Contract

Bukmatika discovery is a federated, rights-aware search path. Provider retrieval, neutral ranking, explicit user preferences, and acquisition policy are separate authorities.

## Search constraints versus preferences

`SearchIntent` keeps hard search constraints separate from soft ranking preferences.

Hard intent fields such as `language`, `year_from`, and `year_to` may be translated by adapters into provider query constraints. They can therefore change which records a provider returns.

`SearchIntent.preferences` never changes provider participation and never becomes an acquisition rule. It can express preferred:

- languages;
- formats;
- publication era;
- rights states;
- source providers.

All enabled providers are still queried within the canonical provider/session budgets. Preferences are applied only after provider results are normalized and deduplicated.

## Neutral score

The neutral score remains the primary relevance authority. It is composed from the adapter-owned source score plus the existing bounded rights/metadata bonuses. Explicit preferences do not rewrite that score.

## Preference boost

Each matched preference dimension contributes `0.01`:

- language match: `+0.01`;
- format match: `+0.01`;
- era match: `+0.01`;
- rights-state match: `+0.01`;
- source match: `+0.01`.

The total preference boost is therefore capped at `0.05`. A candidate more than `0.05` stronger on neutral quality cannot be overtaken purely by preferences.

Language values are normalized for common ISO/name aliases while unknown values retain normalized literal matching. Format and source matching are case-insensitive and source names normalize spaces/hyphens to the canonical underscore form.

## Transparency

Every returned candidate has a `ranking` explanation keyed by `source:source_record_id` containing:

- `neutral_score`;
- `preference_boost`;
- `total_score`;
- the preference dimensions that matched.

The Discover surface exposes the same distinction so a user can see whether ranking moved because of neutral quality or an explicit preference.

## Rights and acquisition invariants

Preferences do not:

- mutate or synthesize `RightsEvidence`;
- change an asset's acquisition eligibility;
- suppress enabled providers;
- turn borrow-only, preview-only, restricted, or unknown material into unattended-download candidates;
- create standing personalization or AI authority.

The rights engine remains the sole unattended-acquisition authority. Discovery preferences only influence the ordering of already-discovered candidates.
