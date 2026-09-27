# Delivery Roadmap

The roadmap is organized by vertical slices. A slice is complete only when the real user path works end-to-end.

## Current engineering checkpoint - 2026-09-27

- Current production base: `main@2d38bf99cc754a8caac8543d94fc96fbcb6d83b2` after merged PR #173.
- Post-merge Quality #746 is green on that exact production commit across API, Web, Browser, and Deployment quality.
- **Consumer AI release is now additionally blocked by the provider-neutral runtime gate in `docs/PROVIDER_NEUTRAL_AI_RELEASE_GATE.md`, tracked by issue #174.** Passing Ollama-only runtime proofs is no longer sufficient. Bukmatika must prove provider-neutral configuration/runtime/UX, canonical credential and egress policy ownership, no silent local-to-cloud fallback, and real interchangeability across independently implemented providers before consumer AI readiness can be marked green.
- Phase 0 foundation is implementation-complete: the canonical local bootstrap owns prerequisite validation, non-destructive environment setup, PostgreSQL 18 Compose lifecycle, dependency installation, database readiness, and migrations; structured request correlation provides canonical `X-Request-ID` propagation and privacy-bounded JSON access logging. PR #130 further fences structured logging to Bukmatika-owned/server logger families so raw HTTP client URLs cannot become a second telemetry authority.
- Phase 1 listed discovery capabilities are implementation-complete: all four canonical source adapters remain independently degradable; provider health/rate-limit telemetry is bounded and privacy-safe; normalized results persist through the PostgreSQL catalog; and explicit language, format, era, rights-state, and source preferences can transparently nudge ranking by at most `0.05` without suppressing providers, rewriting rights evidence, or changing acquisition eligibility.
- Phase 2 now has principal-owned acquisition intent and approval policy over the installation-wide exact-asset transfer authority. PR #133 added `always_ask` / `auto_eligible` policy, exact principal request/approve/cancel state, shared-transfer cancellation fencing, and a real Chromium lifecycle while preserving the `RightsEngine` as the final unattended-acquisition authority.
- Phase 3 metadata confidence/provenance is inspectable through the existing `MetadataAssertion` / `SourceObservation` authority, with conflicting provider claims preserved rather than collapsed. PR #134 proves that projection in PostgreSQL and Chromium without exposing raw provider payloads or introducing a second provenance store.
- Phase 3 cover handling is complete through PRs #135-#136: provider cover metadata is normalized into canonical provenance, safely materialized server-side through the existing SSRF-safe downloader, bounded/sanitized with Pillow, cached only as disposable Bukmatika-owned presentation bytes, served through authenticated endpoints, and proven in real Chromium without browser requests to provider URLs.
- Phase 4 product acceptance is complete for the listed consumer surfaces. PR #137 proves result dossier and exact edition selection, #139 proves the aggregate Status Center, #142 proves collections/tags/smart shelves, #143 proves PDF/EPUB Reader navigation plus progress/bookmarks/highlights/notes, #144 proves the live Discovery workspace, and #146 proves keyboard/focus/reduced-motion plus phone/tablet responsive behavior. The first #146 browser burn caught a real 391px Library shell at a 390px viewport; the corrected bounded mobile navigation then passed the full Chromium rail on the exact merged production line.
- PR #148 closes explainable personalized discovery/recommendations as a deterministic read projection over canonical catalog metadata and principal-owned ranking preferences. It returns exact reason contributions, preserves stronger explicit preference authority, excludes only the requesting principal's owned works, works with AI disabled, and leaves live federated ranking, rights, acquisition, and autonomy unchanged. The real Chromium journey proves explanation rendering, ownership isolation, retry-stable fixtures, and canonical dossier handoff.
- Phase 7 interop and market-readiness work has materially advanced without creating parallel authorities: PR #150 ships canonical CSL JSON/BibTeX/RIS citation export, PR #151 centralizes discovery adapter contract coverage, PRs #153-#154 harden hostile EPUB/DOCX archive handling and archive member ceilings, PR #155 ships authenticated principal-scoped OPDS 1.2, PR #156 proves consumer backup/restore through the existing `.bukmatika` portability and destination-local rights authorities, PR #158 imports private local books through canonical storage/processing/Reader paths without leaking them into shared discovery, and PR #160 adds process liveness/readiness plus privacy-bounded worker/database crash diagnostics.
- PR #168 closes the installer/deployment implementation path with one repository-owned self-hosted production stack: PostgreSQL, API, OCR, and Web stay private behind a same-origin loopback Caddy ingress; migrations run before application startup; acquired/imported book bytes and PostgreSQL data use separate durable volumes; API/Web images run with the proven production contracts; deployment initialization generates the database secret atomically with restrictive permissions; remote plain HTTP/public binds are rejected; and container deployment preserves the loopback-only Ollama trust boundary by keeping model and embedding providers disabled rather than weakening provider routing.
- Whole-account privacy is now a separate proven authority from personalization-only reset/export. PR #162 composes canonical library/personalization serializers into a versioned account export, adds explicit `DELETE`-confirmed principal erasure, durable retryable byte cleanup, cross-principal dedupe preservation, secret omission, and a Chromium replacement-principal isolation journey.
- The browser/session security boundary has been materially hardened without changing product authority. PR #163 fences cookie-authenticated browser writes to the exact configured web origin, PR #164 adds production Next.js response hardening, and PR #166 keeps safe GETs mutation-free, forces cookie-bound API responses to `private, no-store`, and makes post-erasure replacement-session creation deterministic. The first #166 Chromium run caught the anonymous bootstrap race; the corrected exact head and post-merge production rail both passed.
- PR #165 closes consumer onboarding/empty-state polish with state-derived first-run guidance and a lightweight principal-scoped library-readiness projection. It deliberately creates no tutorial persistence or second onboarding state authority.
- PR #170 closes the AI privacy/context-leakage burn by proving the canonical model boundary minimizes grounded-answer context before provider execution. PR #171 locks deterministic model-call budgets and proves AI-disabled/evidence-only paths do not make unnecessary model requests. PR #172 locks personalization calibration thresholds, confidence bounds, feedback floors/ceilings, decay behavior, recommendation weighting, and explicit-over-inferred authority into release evidence.
- Runtime diagnostics separate cheap process liveness from operational readiness: `/health` remains dependency-free, while `/ready` verifies PostgreSQL plus enabled in-process acquisition/delegation workers and returns only stable component state/error codes. Unexpected worker exits, cancellation, and crashes are bounded structured events; database outage/recovery is transition-logged without exposing connection strings, SQL, exception messages, job payloads, book identifiers, or private context.
- The API gate covers development Compose validation, Ruff, strict MyPy, the current migration chain, and the complete PostgreSQL/API test suite. The Web gate covers TypeScript typecheck and production build. The Browser gate builds the production web app, starts the real API against PostgreSQL, installs Chromium, and runs the no-mock consumer journeys through the production surfaces. The Deployment gate generates a fresh production environment, validates the exact production Compose graph, builds the application images, runs migrations, boots PostgreSQL/API/OCR/Web behind the same-origin loopback ingress, verifies API readiness and the web root through that ingress, inspects service state, and tears the stack down without deleting durable volumes.
- The real Chromium rail proves Discovery ranking/provenance/degradation, explainable personalized recommendation fit, dossier and exact edition handoff, aggregate status, Library organization, private local-book import and owner isolation, PDF/EPUB Reader behavior, Research-to-Reader source handoff, balanced comparison with no-match sources preserved, stale cross-tab bookmark/highlight mutation rejection, principal-owned acquisition request/approval/cancel continuity, metadata provenance and sanitized-cover rendering, backup download/dry-run/restore into a fresh principal, whole-account export/erasure with deterministic replacement-principal isolation, first-run/onboarding state transitions, keyboard traversal and visible focus, reduced-motion behavior, and phone/tablet responsive layout without horizontal shell overflow.
- Grounded reader research is live behind the canonical `ModelGateway` with loopback-only Ollama, citation validation, policy-gated execution, AI activity evidence, runtime/model readiness gating, evidence-only fallback, consumer runtime/recovery status, a public real-model smoke command, and a rollback-only grounded-runtime proof command.
- Consumer research includes selected-book lexical retrieval, grounded Q&A, balanced source/edition comparison, explicit-highlight grounding, deterministic timelines, deterministic people/place/concept mention extraction, measured retrieval evaluation over canonical evidence, ranked PostgreSQL fallback, and explicit request-local semantic retrieval behind the canonical embedding gateway.
- Semantic retrieval implementation is shipped but remains unpromoted in ordinary consumer Research until the hardened semantic recall burn passes against a real configured embedding provider/model through the provider-neutral runtime gate. The request-local semantic path creates no persistent vector authority.
- The bounded Level 2 Research workflow is continuous in the Research surface: selected books and a question can produce an exact proposal, the user can approve or reject it, explicitly start or stop it, follow durable live state, recover active controls after reload or scope changes, see the newest active proposal first, and inspect terminal outcomes from the same server-backed delegation/result authorities.
- Research surfaces canonical local-AI readiness for AI-disabled, unconfigured, runtime-offline, invalid-runtime, and missing-model states without becoming a second settings authority. Evidence-only research remains available and setup routes back to the canonical AI control center. This local/Ollama-shaped control surface is now explicitly transitional and must be replaced by the provider-neutral consumer runtime gate before AI release readiness.
- Consumer library portability includes versioned manifests, deterministic dry-run/apply, rights-gated byte export/import, bounded `.bukmatika` transport, archive hardening, export omission transparency, a real Chromium backup/restore journey into a fresh principal, and private local-book import through the same canonical storage/processing/Reader authorities without widening shared catalog visibility.
- Bounded Level 2 read-only delegation remains limited to explicitly selected `research.search` steps. It requires principal-owned consent, exact per-run approval, explicit start, immutable selection/budget fingerprints, finite runtime/retry/attempt ceilings, stop/revoke controls, current-state/policy/context revalidation, durable PostgreSQL dispatch, claim leases, restart recovery, and audit evidence.
- The Level 2 lane is not standing permission. The model cannot approve, start, expand, reorder, rebudget, or silently replan delegated work.
- Level 3, delegated writes, autonomous acquisition, consequential delegation, standing approvals, arbitrary tool/code/shell/SQL/filesystem access, and open-ended scheduler agents remain closed.
- The grounded proof command existing is not proof that a real provider passed it. Phase 5 still carries a release-evidence gate until `bukmatika-grounded-ai-proof` is generalized through the production provider registry and executed successfully against the required real providers for the exact release candidate.
- The semantic recall command existing is not real-model quality evidence. Consumer semantic promotion remains blocked until `bukmatika-semantic-recall-burn` is provider-neutral and a real configured embedding provider/model meets the acceptance gate tracked in issue #5 and issue #174.
- Phase 7 repository security acceptance is recorded in `docs/SECURITY_REVIEW.md`. The review found no unresolved P0/P1 defect across session/browser boundaries, SSRF-safe acquisition, hostile-file ingestion, storage confinement, portability archives, privacy/ownership, AI context minimization, bounded delegation, and canonical deployment. It explicitly does not close repository branch protection, provider-neutral AI runtime proof, real embedding-quality proof, external TLS/HSTS operation, or third-party assurance.
- Repository governance remains an external settings gate: `main` is still unprotected, and issue #108 tracks requiring pull requests and the existing quality checks while blocking force pushes/deletion without pretending application code can substitute for branch protection. The 2026-09-26 no-op write/cleanup incident demonstrated this risk directly: GitHub accepted a direct contents write to `main` that a protected branch should have rejected.
- Retrieval quality work remains independent of autonomy expansion. Improve measured lexical/semantic research quality only where evidence proves a gap; do not add infrastructure simply because the Level 2 control spine now exists.

## Phase 0 - Foundation

- [x] Product thesis and guardrails.
- [x] Canonical architecture and rights states.
- [x] Repository initialization.
- [x] Adaptive AI architecture and autonomy invariants.
- [x] CI quality gates.
- [x] Database migrations.
- [x] Local development bootstrap.
- [x] Structured logging and request correlation.
- [x] Canonical semantic interaction-event vocabulary.

## Phase 1 - Real discovery

Goal: a user can search a topic and receive normalized, rights-classified real results.

- [x] Canonical search/domain contracts.
- [x] Open Library adapter.
- [x] Discovery API endpoint.
- [x] Internet Archive adapter.
- [x] Project Gutenberg catalog adapter.
- [x] Library of Congress adapter.
- [x] Provider health and rate-limit telemetry.
- [x] Work/edition duplicate resolver backed by PostgreSQL.
- [x] Search result persistence.
- [x] Explicit discovery preferences: language, format, era, rights state, source type.

Acceptance: “Old World before Columbus” returns live source results with source provenance and no fake catalog rows. Explicit preferences can influence transparent ranking without bypassing neutral sorting. The listed Phase 1 implementation capabilities are complete; broader consumer polish remains governed by Phase 4 acceptance.

## Phase 2 - Lawful acquisition

Goal: an eligible result becomes a durable verified asset.

- [x] Rights evidence persistence and decision audit.
- [x] Acquisition jobs and retry state machine.
- [x] SSRF-safe downloader.
- [x] MIME sniffing, size limits, checksum, quarantine.
- [x] Storage abstraction.
- [x] Project Gutenberg permitted file acquisition.
- [x] Internet Archive eligible asset acquisition.
- [x] Per-user acquisition preferences and approval policy.

Acceptance: public-domain/open/otherwise-authorized assets download reproducibly; unknown/borrow/restricted assets cannot cross the acquisition gate; AI or personalization cannot override the gate.

## Phase 3 - Catalog + processing

- [x] PDF text extraction.
- [x] EPUB extraction.
- [x] TXT/HTML extraction.
- [x] DOCX extraction.
- [x] OCR for image-only scans.
- [x] Metadata confidence/provenance.
- [x] Cover handling.
- [x] PostgreSQL full-text search.
- [x] Semantic interaction/outcome event writer in canonical product domains.

## Phase 4 - Consumer library + reader

- [x] Polished discovery workspace.
- [x] Result dossier and edition picker.
- [x] Download queue/status center.
- [x] Library shelves/collections.
- [x] EPUB reader.
- [x] PDF reader.
- [x] Highlights, notes, bookmarks, progress.
- [x] Keyboard and accessibility pass.
- [x] Responsive/mobile behavior.
- [x] User goals and session continuity.
- [x] Explicit preference management.

Phase 4 product acceptance is complete for the listed surfaces through real PostgreSQL + Chromium journeys. The accessibility acceptance is deliberately bounded to the behavior actually proven by the product rail: semantic labels/landmarks, keyboard traversal, visible focus, reduced-motion behavior, and phone/tablet responsive layout without horizontal shell overflow. It is not a claim of third-party WCAG certification.

## Phase 5 - Adaptive research intelligence

Goal: introduce one bounded AI orchestration system that becomes more useful through evidence while remaining inspectable, reversible, optional, and unable to self-authorize.

### AI foundation

- [x] PostgreSQL persistence for `UserModel`, `PreferenceClaim`, `InteractionEvent`, `OutcomeEvent`, `Goal`, `Plan`, and `ActionDecision`.
- [x] Canonical `ModelGateway`; product domains cannot call model SDKs directly.
- [x] Deterministic context assembler with privacy minimization.
- [x] Typed capability/tool registry over canonical product services.
- [x] Planner structured-output validation.
- [x] Deterministic action policy and approval gate.
- [x] AI activity ledger.
- [x] Local provider readiness contract distinguishing configuration from actual runtime/model readiness.
- [x] Consumer-facing runtime status and recovery guidance.
- [x] Consumer-editable local model configuration/setup flow.
- [x] Real-runtime smoke command requiring readiness plus actual structured model generation.
- [x] Transactional grounded-runtime proof command over canonical persisted evidence, plan/audit/ledger verification, and rollback validation.
- [x] Bounded Level 2 read-only delegation authority for `research.search` with consent, exact approval/start, budgets, stop/revoke, durable dispatch, restart/concurrency recovery, and audit evidence.
- [x] Consumer planning front door from selected books and research question to a bounded delegation proposal.
- [x] In-Research Level 2 lifecycle continuity through exact proposal review, approve/reject, explicit start/stop, durable live-state refresh, reload/scope recovery, newest-first active work, and terminal outcomes over the canonical delegation authority.
- [x] Research-local read-only AI readiness guidance over the canonical `/v1/ai/status` contract, with evidence-only fallback and setup routing to the canonical AI control center.
- [x] Shared capability argument/context contracts preflighted before approval and revalidated before permit/runtime execution.
- [ ] Provider-neutral, user-controlled AI runtime gate in `docs/PROVIDER_NEUTRAL_AI_RELEASE_GATE.md` / issue #174.
- [ ] Recorded successful provider-neutral grounded-answer release evidence on the exact release candidate.

### Learning

- [x] Explicit vs inferred preference authority.
- [x] Confidence/evidence/scoping model.
- [x] Contradiction resolution.
- [x] Preference decay for inferred claims.
- [x] Outcome-driven reinforcement and negative feedback.
- [x] User correction/forget flow.
- [x] Pause-learning control.

### Research intelligence

- [x] Search within one book.
- [x] Cross-book lexical retrieval.
- [x] Deterministic lexical-recall evaluation harness over canonical expected evidence.
- [x] Representative public-domain lexical/paraphrase recall baseline through the production parser, chunker, and PostgreSQL research path.
- [x] PostgreSQL query-construction/ranked-fallback improvements measured against the representative baseline.
- [x] Explicit request-local semantic retrieval behind one canonical embedding-provider interface, without a persistent vector database or second retrieval authority.
- [ ] Record real provider-neutral semantic-recall acceptance evidence before promoting semantic retrieval as a consumer quality improvement.
- [x] Grounded book Q&A with page/section citations.
- [x] Compare sources/editions.
- [x] Timeline/entity/concept views derived from canonical evidence.
- [x] Reader-aware AI context.
- [x] Active research goals spanning sessions.
- [x] Explainable personalized discovery/recommendations.

### AI & Personalization control center

- [x] What Bukmatika knows about me.
- [x] Explicit preferences.
- [x] Inferred preferences with confidence/evidence.
- [x] Active goals.
- [x] Bounded Level 2 delegated-task status and controls.
- [x] Activity ledger.
- [x] Local provider/model readiness and recovery surface.
- [x] Per-profile local model selection.
- [ ] Provider-neutral multi-provider/model connection management, role assignment, fallback policy, and local/cloud data-class policy required by issue #174.
- [x] Explicit Level 2 read-only consent plus approve/reject/start/stop controls.
- [ ] Broader per-feature autonomy controls beyond the proven Level 2 research lane.
- [x] Export/delete personalization data.
- [x] Disable AI while preserving normal library operation.

Acceptance:

- every AI research claim can resolve to canonical source coordinates when the task is source-grounded;
- material AI actions have complete ledger evidence;
- explicit user preferences outrank inferred claims;
- a corrected/deleted learned claim stops influencing behavior;
- AI-disabled mode leaves discovery, acquisition, cataloging, library, and reading functional;
- no independent agent memory or provider SDK appears outside the canonical orchestration/model boundary;
- a Level 2 delegation cannot widen its selected context, authority, capability set, or budgets after approval;
- stop/revoke/AI-off and stale-worker fencing remain effective across restart and concurrency;
- provider/model selection, cloud egress, fallback, credential ownership, usage evidence, and interchangeability satisfy the provider-neutral hard gate before consumer AI readiness is declared.

### Phase 5 exit gate before broader autonomy expansion

Broader autonomy remains closed until all applicable gates are proven for the proposed expansion. The current read-only `research.search` Level 2 lane is already proven and does not grant authority to any future capability.

For the remaining Phase 5 product/release work:

1. the provider-neutral AI runtime gate in `docs/PROVIDER_NEUTRAL_AI_RELEASE_GATE.md` and issue #174 is fully satisfied;
2. provider readiness reports actual provider/model/credential state rather than configuration presence;
3. a consumer can understand, configure, and recover from disabled, unconfigured, local-offline, invalid-credential, provider-unreachable, rate-limit/quota, missing/deprecated-model, and policy-denied states without editing deployment environment variables for ordinary use;
4. the reader falls back to ordinary evidence retrieval if model readiness changes between status inspection and execution, without silently changing provider or crossing a new local/cloud trust boundary;
5. the provider-neutral grounded-answer proof passes the exact production registry/runtime path against the required real provider implementations, with provider/model identity and egress policy evidence recorded for the exact release candidate;
6. one real configured embedding provider/model passes the hardened semantic recall acceptance gate before semantic retrieval is promoted in ordinary consumer Research, and that evidence is model-specific;
7. citation validation, AI-off behavior, ownership isolation, privacy minimization, credential redaction, activity-ledger projection, delegation fencing, rollback-clean proof data, and the no-mock browser journey remain green on the exact release candidate;
8. unapproved local-to-cloud or cross-cloud fallback is proven impossible;
9. no new agent, memory, graph, embedding, or orchestration subsystem is introduced unless a remaining product requirement proves it necessary.

## Phase 6 - Autonomy expansion, separately gated

Goal: evaluate capabilities beyond the proven Level 2 read-only research lane without inheriting authority from it.

### Proven foundation

- [x] Level 0 reactive and Level 1 suggestive policy model.
- [x] Level 2 explicit principal consent for bounded read-only delegation.
- [x] Exact delegation scope and fingerprint binding.
- [x] Fixed runtime/retry/attempt budgets outside model control.
- [x] Exact per-run approval and explicit start.
- [x] Durable stop/revoke and AI-off kill-switch behavior.
- [x] Durable attempt claims, stale-worker fencing, restart recovery, and PostgreSQL dispatch.
- [x] Adversarial tests proving current policy/state/context and capability contracts are rechecked before delegated work.

### Still closed

- [ ] Any second delegated capability until it independently defines and proves its argument/context contract and authority boundary.
- [ ] Delegated durable writes.
- [ ] Standing approvals or capability-wide permission.
- [ ] Dynamic autonomous replanning or self-expanded task scope.
- [ ] Consequential or non-reversible delegated actions.
- [ ] Autonomous acquisition.
- [ ] Level 3 authority.
- [ ] Sparse proactive intelligence that creates or starts delegated work without an explicit bounded user action.
- [ ] Arbitrary code, shell, SQL, filesystem, or unrestricted network tools.

Acceptance for any future expansion: the delegated capability can never expand its own authority, exceed declared resource boundaries, bypass user/rights/security/privacy policy, or inherit approval from a different capability or previous run. Reversibility/undo authority, approval scope, stop behavior, recovery behavior, and adversarial proof must be explicit before merge.

## Phase 7 - Interop + market readiness

- [x] OPDS export/server.
- [x] BibTeX/CSL JSON/RIS citation export.
- [x] Import existing local libraries.
- [x] Backup/restore.
- [x] Privacy/export/delete controls.
- [x] Installer/deployment path.
- [x] Observability and crash diagnostics.
- [x] Security review.
- [x] Hostile-file burn tests.
- [x] Source adapter contract tests.
- [x] Consumer onboarding and empty-state polish.
- [x] Personalization calibration/evaluation suite.
- [x] Grounding/citation validity tests.
- [x] Unnecessary-model-call budget tests.
- [x] AI privacy/context-leakage burn tests.
- [ ] Provider-neutral AI consumer release gate (#174).

AI remains an optional product layer. Core discovery, acquisition, cataloging, organization, and reading must remain functional without an AI provider.