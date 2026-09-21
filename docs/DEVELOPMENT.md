# Development Methodology

## Rules

1. Build vertical slices against real providers. Production mock providers are prohibited.
2. One canonical owner per domain concern. Compatibility layers must have a deletion plan.
3. Provider-specific data stops at adapter boundaries.
4. The rights engine is the sole unattended-acquisition authority.
5. A failing external source degrades the source, not the entire search request.
6. Every network integration has timeout, retry/backoff, identification, and rate-limit behavior.
7. Schema changes land through migrations.
8. User-visible claims about rights/access must be backed by stored evidence.
9. Runtime dependencies require a demonstrated product need.
10. Merge only when exact-head quality gates are green.

## Definition of done

A feature is done when:

- real logic is wired through the UI/API boundary;
- validation and unhappy paths are covered;
- logs contain actionable context without leaking private content;
- persistence ownership is unambiguous;
- tests prove the governing contract;
- documentation reflects the shipped behavior;
- no dead scaffold, sample provider, demo record, or duplicate authority remains.

## Code organization

- `apps/web`: consumer web application.
- `services/api`: API, discovery, rights, acquisition, processing, catalog.
- `docs`: product and architecture authorities.

Do not split into additional services until deployment or scaling evidence makes the boundary necessary.
