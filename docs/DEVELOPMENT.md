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

## Observability contract

Bukmatika emits structured JSON logs to standard output. HTTP requests use one bounded correlation authority:

- a valid incoming `X-Request-ID` may be retained when it is ASCII, at most 128 characters, and limited to the accepted identifier character set;
- otherwise Bukmatika generates a new request ID and returns it in `X-Request-ID`;
- the web origin may read the response header through the canonical CORS configuration;
- request access events record the request ID, HTTP method, canonical route template, status code, duration, and safe code-frame locations for uncaught failures;
- request access events never include raw query strings, concrete route parameter values, request or response bodies, cookies, authorization material, annotation/book text, or exception messages;
- downstream structured logs may obtain the request ID from request-bound context rather than copying private payload data into log fields;
- Uvicorn's duplicate raw-target access logger is disabled because raw targets may contain query parameters;
- Uvicorn's generic ASGI exception record is stripped of the raw traceback/message after Bukmatika emits the correlated safe failure event;
- HTTPX/HTTPCore transport logging is held at warning-or-higher because its INFO request line contains the complete outbound URL and may include user research terms;
- `BUKMATIKA_LOG_LEVEL` controls the application log threshold and defaults to `INFO`.

When adding diagnostic fields, prefer identifiers, bounded state names, counts, timings, error classes, and source-code frame locations without local values. Do not make private user content a logging shortcut.

## Code organization

- `apps/web`: consumer web application.
- `services/api`: API, discovery, rights, acquisition, processing, catalog.
- `docs`: product and architecture authorities.

Do not split into additional services until deployment or scaling evidence makes the boundary necessary.
