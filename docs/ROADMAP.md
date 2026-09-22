# Delivery Roadmap

The roadmap is organized by vertical slices. A slice is complete only when the real user path works end-to-end.

## Current engineering checkpoint - 2026-09-22

- Base entering the current slice: `main@ceba77a38ca88f8703d04097e4f149b069b733a4`.
- Post-merge Quality #156 passed API Ruff, strict MyPy, Alembic migrations, the full PostgreSQL suite, web typecheck, and production web build.
- Grounded reader research is live behind the canonical `ModelGateway` with a loopback-only Ollama provider, citation validation, policy-gated execution, AI activity evidence, and evidence-only fallback when AI is unavailable.
- Active slice: make local-model availability truthful at runtime by distinguishing configured, unreachable, invalid, missing-model, and ready states before advertising or executing `research.answer`.
- Do not advance to delegated autonomy merely because local synthesis works. Consumer setup, real-runtime proof, recovery behavior, and the remaining Phase 5 research surfaces come first.

## Phase 0 - Foundation

- [x] Product thesis and guardrails.
- [x] Canonical architecture and rights states.
- [x] Repository initialization.
- [x] Adaptive AI architecture and autonomy invariants.
- [x] CI quality gates.
- [x] Database migrations.
- [ ] Local development bootstrap.
- [ ] Structured logging and request correlation.
- [x] Canonical semantic interaction-event vocabulary.

## Phase 1 - Real discovery

Goal: a user can search a topic and receive normalized, rights-classified real results.

- [x] Canonical search/domain contracts.
- [x] Open Library adapter.
- [x] Discovery API endpoint.
- [x] Internet Archive adapter.
- [x] Project Gutenberg catalog adapter.
- [x] Library of Congress adapter.
- [ ] Provider health and rate-limit telemetry.
- [x] Work/edition duplicate resolver backed by PostgreSQL.
- [x] Search result persistence.
- [ ] Explicit discovery preferences: language, format, era, rights state, source type.

Acceptance: “Old World before Columbus” returns live source results with source provenance and no fake catalog rows. Explicit preferences can influence transparent ranking without bypassing neutral sorting.

## Phase 2 - Lawful acquisition

Goal: an eligible result becomes a durable verified asset.

- [x] Rights evidence persistence and decision audit.
- [x] Acquisition jobs and retry state machine.
- [x] SSRF-safe downloader.
- [x] MIME sniffing, size limits, checksum, quarantine.
- [x] Storage abstraction.
- [x] Project Gutenberg permitted file acquisition.
- [x] Internet Archive eligible asset acquisition.
- [ ] Per-user acquisition preferences and approval policy.

Acceptance: public-domain/open/otherwise-authorized assets download reproducibly; unknown/borrow/restricted assets cannot cross the acquisition gate; AI or personalization cannot override the gate.

## Phase 3 - Catalog + processing

- [x] PDF text extraction.
- [x] EPUB extraction.
- [x] TXT/HTML extraction.
- [x] DOCX extraction.
- [x] OCR for image-only scans.
- [ ] Metadata confidence/provenance.
- [ ] Cover handling.
- [x] PostgreSQL full-text search.
- [x] Semantic interaction/outcome event writer in canonical product domains.

## Phase 4 - Consumer library + reader

- [ ] Polished discovery workspace.
- [ ] Result dossier and edition picker.
- [ ] Download queue/status center.
- [ ] Library shelves/collections.
- [ ] EPUB reader.
- [ ] PDF reader.
- [ ] Highlights, notes, bookmarks, progress.
- [ ] Keyboard and accessibility pass.
- [ ] Responsive/mobile behavior.
- [x] User goals and session continuity.
- [x] Explicit preference management.

Phase 4 remains deliberately conservative in this sheet until each consumer surface receives an explicit product-level acceptance pass. Backend capability existing is not sufficient to mark a consumer experience complete.

## Phase 5 - Adaptive research intelligence

Goal: introduce one bounded AI orchestration system that becomes more useful through evidence while remaining inspectable, reversible, and optional.

### AI foundation

- [x] PostgreSQL persistence for `UserModel`, `PreferenceClaim`, `InteractionEvent`, `OutcomeEvent`, `Goal`, `Plan`, and `ActionDecision`.
- [x] Canonical `ModelGateway`; product domains cannot call model SDKs directly.
- [x] Deterministic context assembler with privacy minimization.
- [x] Typed capability/tool registry over canonical product services.
- [x] Planner structured-output validation.
- [x] Deterministic action policy and approval gate.
- [x] AI activity ledger.
- [x] Local provider readiness contract distinguishing configuration from actual runtime/model readiness.
- [ ] Consumer-facing local model setup/recovery flow.
- [ ] Real local-runtime release proof using an installed model, not a mock transport.

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
- [ ] Semantic retrieval with one canonical embedding-provider interface.
- [x] Grounded book Q&A with page/section citations.
- [ ] Compare sources/editions.
- [ ] Timeline/entity/concept views derived from canonical documents.
- [x] Reader-aware AI context.
- [x] Active research goals spanning sessions.
- [ ] Explainable personalized discovery/recommendations.

### AI & Personalization control center

- [x] What Bukmatika knows about me.
- [x] Explicit preferences.
- [x] Inferred preferences with confidence/evidence.
- [x] Active goals.
- [ ] Delegated tasks.
- [x] Activity ledger.
- [ ] Per-feature autonomy controls.
- [x] Export/delete personalization data.
- [x] Disable AI while preserving normal library operation.

Acceptance:

- every AI research claim can resolve to canonical source coordinates when the task is source-grounded;
- material AI actions have complete ledger evidence;
- explicit user preferences outrank inferred claims;
- a corrected/deleted learned claim stops influencing behavior;
- AI-disabled mode leaves discovery, acquisition, cataloging, library, and reading functional;
- no independent agent memory or provider SDK appears outside the canonical orchestration/model boundary.

### Phase 5 exit gate before Phase 6

Phase 6 remains closed until all of the following are true:

1. the local provider reports actual runtime/model readiness rather than configuration presence;
2. a consumer can understand and recover from unconfigured, runtime-offline, invalid-runtime, and missing-model states;
3. the reader falls back to ordinary evidence retrieval if readiness changes between status inspection and execution;
4. one real installed Ollama model completes the grounded-answer path against canonical persisted evidence;
5. citation validation, AI-off behavior, ownership isolation, privacy minimization, and activity-ledger proofs remain green on the exact release candidate;
6. no new agent, memory, graph, embedding, or orchestration subsystem is introduced unless a remaining product requirement proves it necessary.

## Phase 6 - Bounded delegated autonomy

Goal: permit opt-in standing goals only after the reactive/suggestive system is trustworthy.

- [ ] Autonomy Levels 0-3 with Level 0-1 default.
- [ ] Delegation scopes and capability allowlists.
- [ ] Resource/token/network/OCR budgets.
- [ ] Approval rules by action consequence.
- [ ] Reversible-action framework and undo evidence.
- [ ] Stop/pause delegated goal control.
- [ ] Sparse proactive intelligence for high-value events.
- [ ] Adversarial tests proving rights/safety/privacy rules outrank delegation.

Acceptance: a delegated goal can never expand its own authority, exceed declared resource boundaries, or bypass user/rights/security policy.

## Phase 7 - Interop + market readiness

- [ ] OPDS export/server.
- [ ] BibTeX/CSL JSON/RIS citation export.
- [ ] Import existing local libraries.
- [ ] Backup/restore.
- [ ] Privacy/export/delete controls.
- [ ] Installer/deployment path.
- [ ] Observability and crash diagnostics.
- [ ] Security review and hostile-file burn tests.
- [ ] Source adapter contract tests.
- [ ] Consumer onboarding and empty-state polish.
- [ ] Personalization calibration/evaluation suite.
- [ ] Grounding/citation validity tests.
- [ ] Unnecessary-model-call budget tests.
- [ ] AI privacy/context-leakage burn tests.

AI remains an optional product layer. Core discovery, acquisition, cataloging, organization, and reading must remain functional without an AI provider.
