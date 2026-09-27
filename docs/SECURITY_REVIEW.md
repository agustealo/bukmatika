# Security Review

## Acceptance record

- Review date: 2026-09-27
- Reviewed production base: `main@1c306bb38c08a3214ec703b97ee8518c838d19fc`
- Post-merge quality evidence: Quality #744 green across API, Web, Browser, and Deployment quality on the reviewed base.
- Scope: repository-owned application, storage, ingestion, browser/session, AI-context, privacy, and canonical self-hosted deployment boundaries.
- Result: no unresolved P0/P1 security defect was identified in the reviewed boundary.

This is an engineering security acceptance review, not a third-party penetration test, compliance certification, or guarantee that future changes cannot introduce vulnerabilities. Any change to the reviewed trust boundaries must continue to pass the governing tests and may require this review to be refreshed.

## Threat model and trust boundaries

Bukmatika processes untrusted remote metadata, URLs, downloaded book bytes, imported local files, portability archives, browser requests, and optional local-model output. Those inputs are never treated as authority by themselves.

The principal trust boundaries are:

1. **Browser and session boundary**: cookie-authenticated browser mutations must originate from the exact configured web origin, while non-browser API clients remain usable without an Origin header.
2. **Principal ownership boundary**: library, research, personalization, delegation, privacy, and account data are principal-scoped before projection or mutation.
3. **Network acquisition boundary**: unattended acquisition accepts only HTTPS public-network targets after DNS validation and IP pinning; every redirect is revalidated.
4. **Rights boundary**: discovery does not imply acquisition authority. The canonical rights engine remains the final unattended-download gate.
5. **File and archive boundary**: bytes are size-bounded, format-verified, content-addressed, and parsed with hostile-archive limits before becoming canonical document content.
6. **Storage boundary**: canonical stored-object resolution is confined to the object namespace and resolved path must remain beneath the configured storage root.
7. **AI boundary**: model and embedding providers remain optional and loopback-only; model-facing context is minimized by the canonical gateway and cannot grant execution authority.
8. **Deployment boundary**: the repository-owned production stack exposes only a loopback same-origin ingress; remote TLS termination is external and remote plain HTTP/public binds are rejected by deployment validation.

## Browser and session security

The local session authority generates high-entropy opaque tokens and stores only SHA-256 token digests. Authentication rejects empty, oversized, expired, and revoked tokens. Cookies are `HttpOnly` and `SameSite=Lax`; the canonical deployment validator requires `Secure` cookies for HTTPS origins and rejects the incompatible secure-cookie setting for loopback HTTP.

Cookie-authenticated browser writes are fenced to the exact configured origin. Missing Origin remains valid for deliberate non-browser clients, while mismatched, `null`, and duplicate browser Origin headers fail closed. Safe methods remain mutation-free under the same contract.

Cookie-bound API responses and responses that set a session cookie are forced to `private, no-store` with `Pragma: no-cache`, while anonymous responses retain endpoint-owned caching policy.

The production web application disables the framework identification header and sets repository-owned security headers including `Content-Security-Policy` restrictions for base URI, framing, and object embedding; `Referrer-Policy: no-referrer`; `X-Content-Type-Options: nosniff`; `X-Frame-Options: DENY`; and a restrictive `Permissions-Policy`.

## Acquisition and SSRF boundary

The acquisition network layer requires HTTPS, rejects credential-bearing URLs, rejects private/loopback IP literals, fails closed when DNS answers contain any non-public address, and pins the request to the validated public IP while preserving the original Host/SNI identity.

Redirects do not inherit trust from the initial URL. Every redirect target is resolved, validated, and pinned again. Transfer handling also rejects declared oversized responses before body persistence and validates resumable `Content-Range` boundaries so interrupted or changed resources cannot be silently stitched together.

Downloaded bytes must pass format verification before canonical use. PDF magic and media type are checked; archive formats are inspected for traversal and compression abuse. Failed verification remains a quarantinable failure rather than becoming trusted content.

## Hostile-file and archive handling

EPUB and DOCX parser burns cover:

- duplicate normalized member paths;
- absolute or traversal-style unsafe archive paths, including unused members;
- external EPUB spine resources;
- expanded-content byte ceilings;
- archive member-count ceilings;
- invalid archive limit configuration.

`.bukmatika` portability archives independently reject:

- duplicate archive members;
- symlink members and symlink input bundles;
- member-count abuse;
- compression-ratio abuse;
- missing required metadata;
- manifest assets without exactly one byte/omission disposition;
- assets represented as both included and omitted;
- manifest/index identity disagreement;
- payload hash tampering.

Canonical stored-object lookup accepts only the `objects/` namespace, rejects absolute paths and `..`, resolves the real filesystem target, proves that it remains beneath the configured storage root, and requires a regular file.

## Privacy and ownership boundary

Whole-account export and erasure are separate from personalization-only controls. Account erasure requires explicit confirmation, durable retryable byte cleanup, and preservation of bytes still referenced by another principal. Account export is read-only and cannot advance pending storage-erasure work.

The browser quality rail proves replacement-principal isolation after erasure. Principal-owned research evidence also rejects cross-principal reader context before evidence construction.

Structured request/runtime diagnostics intentionally exclude raw request bodies, query strings, cookies, authorization secrets, database connection strings, SQL, book content, model prompts, and private job payloads. Request IDs and stable error/status codes provide correlation without becoming a second private-data store.

## AI and autonomy security boundary

Local model generation and embedding routes are constrained to loopback providers. Container deployment deliberately keeps model and embedding providers disabled instead of relaxing that trust boundary.

For grounded research, canonical evidence is rebuilt server-side from principal-owned persisted content. Request-local evidence IDs are validated, every grounded claim requires at least one well-formed citation, duplicate citation IDs are rejected, and fabricated evidence IDs fail before the answer is returned. Invalid model citations are recorded as bounded audit failures rather than accepted as claims.

The canonical `ModelRequest` validator minimizes grounded-answer context to the question plus `{evidence_id, text}` pairs before the model gateway receives it. Richer catalog/coordinate metadata may exist inside application execution, but does not cross the model boundary.

Model-call budget burns prove AI-disabled and deterministic/evidence-only paths do not make unnecessary model requests. Bounded Level 2 delegation remains read-only and requires explicit consent, exact proposal approval, explicit start, fixed budgets, policy/context revalidation, and durable stop/revoke fencing. Level 3 and delegated writes remain closed.

## Deployment security boundary

The canonical production Compose topology keeps PostgreSQL, API, OCR worker, and Web private behind a same-origin Caddy ingress. The ingress binds to `127.0.0.1` by default and the deployment validator rejects any non-loopback bind.

Remote deployments must declare an HTTPS public origin and enable secure session cookies. TLS termination and HSTS for remote access belong to the operator-controlled reverse proxy in front of Bukmatika's loopback ingress. This is an explicit deployment responsibility, not an implicit promise by the internal Caddy listener.

Production environment initialization creates the database secret with an exclusive file create and restrictive mode where supported. Model and embedding providers must remain disabled in the container deployment contract to preserve the loopback-only local-model boundary.

## Residual risks and open gates

The following are intentionally **not** closed by this review:

- **Repository governance**: `main` remains unprotected. Issue #108 tracks required pull requests, required Quality checks, and force-push/deletion protection. Application code cannot substitute for repository administration policy.
- **Real local model release proof**: `bukmatika-grounded-ai-proof` still must succeed against an actually installed local model on the intended release candidate. Existing unit/integration coverage and the proof command itself are not substitutes for that evidence.
- **Real embedding quality proof**: `bukmatika-semantic-recall-burn` still must satisfy its acceptance gate against a real configured local Ollama embedding model before ordinary consumer semantic-search promotion.
- **External TLS/HSTS**: remote access depends on a correctly configured external TLS reverse proxy because the repository-owned production ingress is deliberately loopback-only.
- **Third-party assurance**: this review is not an external penetration test, supply-chain audit, or regulatory certification.
- **Future autonomy**: this review grants no authority to Level 3, delegated writes, autonomous acquisition, standing approvals, arbitrary code/shell/SQL/filesystem tools, or open-ended scheduler agents.

## Acceptance rule for future changes

A future change that modifies session/authentication behavior, public network access, ingestion/parsing, storage resolution, portability archives, privacy erasure, model context, delegation authority, or production exposure must preserve the corresponding fail-closed tests and should refresh this review when the trust boundary materially changes.
