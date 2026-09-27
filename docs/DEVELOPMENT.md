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

## Local development bootstrap

The canonical local bootstrap keeps application runtimes native for fast reload while Docker Compose owns only PostgreSQL. Local AI remains optional and is never installed, pulled, or started by the bootstrap.

Prerequisites:

- Python 3.12 through 3.14;
- Node.js 24 or newer plus npm;
- Docker with the Compose plugin.

From the repository root, validate prerequisites without changing local state:

```text
python scripts/dev_bootstrap.py --check
```

Then prepare the development environment:

```text
python scripts/dev_bootstrap.py
```

The bootstrap:

1. validates Python, Node.js, npm, Docker Compose, and `compose.yaml`;
2. copies `.env.example` to `.env` only when `.env` does not already exist;
3. creates `.venv` only when the canonical virtual environment is absent;
4. installs the API in editable development mode and installs web dependencies;
5. starts the PostgreSQL 18 development service bound only to `127.0.0.1:5432`;
6. waits for `pg_isready` rather than assuming container startup means database readiness;
7. runs the canonical Alembic migration chain to `head`;
8. prints the exact API and web development commands.

The database uses a named Compose volume so ordinary `docker compose down` preserves local data. Removing local database data is therefore an explicit destructive action rather than a bootstrap side effect.

## Observability contract

Bukmatika emits structured JSON logs to standard output. HTTP requests use one bounded correlation authority:

- a valid incoming `X-Request-ID` may be retained when it is ASCII, at most 128 characters, and limited to the accepted identifier character set;
- otherwise Bukmatika generates a new request ID and returns it in `X-Request-ID`;
- the web origin may read the response header through the canonical CORS configuration;
- request access events record the request ID, HTTP method, canonical route template, status code, and duration;
- request access events never include raw query strings, concrete route parameter values, request or response bodies, cookies, authorization material, annotation/book text, or exception messages;
- downstream structured logs may obtain the request ID from request-bound context rather than copying private payload data into log fields;
- Uvicorn's duplicate raw-target access logger is disabled because raw targets may contain query parameters;
- the structured handler is attached only to Bukmatika-owned and selected server logger families rather than promoting the process root logger to the application log level;
- raw `httpx` and `httpcore` transport logging is disabled because full outbound URLs can contain user-derived search parameters; bounded domain telemetry is the diagnostic authority for provider, acquisition, model, and readiness failures;
- `BUKMATIKA_LOG_LEVEL` controls the application log threshold and defaults to `INFO`.

Runtime probes deliberately separate liveness from readiness:

- `GET /health` is a cheap process-liveness check and does not touch PostgreSQL or external providers;
- `GET /ready` verifies canonical PostgreSQL connectivity plus every background worker enabled inside the API process;
- readiness returns HTTP `200` only when all registered checks are healthy and HTTP `503` when the process is alive but degraded;
- public readiness payloads contain only stable component names, `ok`/`failed` state, and bounded error codes such as `DATABASE_UNAVAILABLE`, `WORKER_CRASHED`, `WORKER_CANCELLED`, or `WORKER_EXITED`;
- worker crashes emit a single `runtime.worker.failed` structured event with worker name, bounded error code, and exception class only; exception messages, job payloads, book identifiers, and private context are never copied into runtime diagnostics;
- database readiness transitions emit bounded unavailable/recovered events and never expose the database URL, credentials, SQL text, or driver exception message;
- expected worker cancellation during process shutdown is recorded as a normal stop rather than a crash.

Discovery provider telemetry follows the same privacy boundary. Each attempted provider emits one `discovery.provider.completed` event containing only the provider name, bounded status/error code, elapsed time, result count, upstream HTTP status when available, and a clamped `Retry-After` duration when supplied. Provider exceptions are converted to stable public error descriptions before entering `source_errors`; upstream URLs, response bodies, exception messages, and user search text are not copied into discovery telemetry or error summaries. A `429` is surfaced explicitly as `rate_limited`; other HTTP, transport, timeout, and provider failures retain distinct bounded error codes without creating a second persistent health authority.

When adding diagnostic fields, prefer identifiers, bounded state names, counts, timings, and error classes. Do not make private user content a logging shortcut.

## Browser request security

Bukmatika's browser session is an HttpOnly cookie, so CORS alone is not the authorization boundary for state-changing requests. Browser writes are therefore fenced independently at the outer HTTP boundary:

- browser requests using methods other than `GET`, `HEAD`, or `OPTIONS` must carry exactly one valid `Origin` header matching `BUKMATIKA_WEB_ORIGIN`;
- another port on `localhost` or `127.0.0.1` is a different origin and cannot perform cookie-authenticated writes even though it may be same-site for cookie policy purposes;
- `Origin: null`, malformed origins, duplicate Origin headers, and remote origins fail closed with HTTP `403` and stable code `ORIGIN_NOT_ALLOWED`;
- non-browser local clients may omit `Origin` so CLI, health automation, and server-to-server tooling remain usable without browser-specific headers;
- safe methods and CORS preflight remain outside the write fence; CORS continues to control which browser origin may read API responses;
- rejected browser writes still pass through request correlation, so the response and bounded access event retain the canonical `X-Request-ID`;
- this origin fence supplements `SameSite=Lax`, HttpOnly cookies, and CORS. Do not remove it on the assumption that CORS prevents a malicious browser from sending a request.

Any new browser-authenticated mutation must remain behind this global boundary. Do not add route-local exceptions or a second CSRF authority without a separately reviewed protocol requirement.

The production Next.js surface also publishes a narrow response-hardening contract on every route:

- `Content-Security-Policy` denies framing and object embedding and restricts base-URL mutation without constraining Next.js script/style execution;
- `X-Frame-Options: DENY` preserves frame denial for older user agents in addition to CSP `frame-ancestors 'none'`;
- `X-Content-Type-Options: nosniff` disables MIME sniffing;
- `Referrer-Policy: no-referrer` keeps Bukmatika paths and local identifiers out of outbound referrer metadata;
- `Permissions-Policy` disables camera, microphone, geolocation, payment, and USB capabilities that the product does not use;
- Next.js framework disclosure through `X-Powered-By` is disabled;
- HSTS is deliberately not emitted by the application because the canonical local development path is HTTP. HTTPS deployment/infrastructure remains responsible for transport enforcement.

Keep this response policy intentionally narrow. Do not add script/style/source CSP directives without proving compatibility with the production Next.js build and every consumer route.

## Code organization

- `apps/web`: consumer web application.
- `services/api`: API, discovery, rights, acquisition, processing, catalog.
- `docs`: product and architecture authorities.

Do not split into additional services until deployment or scaling evidence makes the boundary necessary.
