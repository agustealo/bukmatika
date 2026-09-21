# Delivery Roadmap

The roadmap is organized by vertical slices. A slice is complete only when the real user path works end-to-end.

## Phase 0 - Foundation

- [x] Product thesis and guardrails.
- [x] Canonical architecture and rights states.
- [x] Repository initialization.
- [x] Adaptive AI architecture and autonomy invariants.
- [ ] CI quality gates.
- [ ] Database migrations.
- [ ] Local development bootstrap.
- [ ] Structured logging and request correlation.
- [ ] Canonical semantic interaction-event vocabulary.

## Phase 1 - Real discovery

Goal: a user can search a topic and receive normalized, rights-classified real results.

- [x] Canonical search/domain contracts.
- [x] Open Library adapter.
- [x] Discovery API endpoint.
- [ ] Internet Archive adapter.
- [ ] Project Gutenberg catalog adapter.
- [ ] Library of Congress adapter.
- [ ] Provider health and rate-limit telemetry.
- [ ] Work/edition duplicate resolver backed by PostgreSQL.
- [ ] Search result persistence.
- [ ] Explicit discovery preferences: language, format, era, rights state, source type.

Acceptance: “Old World before Columbus” returns live source results with source provenance and no fake catalog rows. Explicit preferences can influence transparent ranking without bypassing neutral sorting.

## Phase 2 - Lawful acquisition

Goal: an eligible result becomes a durable verified asset.

- [ ] Rights evidence persistence and decision audit.
- [ ] Acquisition jobs and retry state machine.
- [ ] SSRF-safe downloader.
- [ ] MIME sniffing, size limits, checksum, quarantine.
- [ ] Storage abstraction.
- [ ] Project Gutenberg permitted file acquisition.
- [ ] Internet Archive eligible asset acquisition.
- [ ] Per-user acquisition preferences and approval policy.

Acceptance: public-domain/open/otherwise-authorized assets download reproducibly; unknown/borrow/restricted assets cannot cross the acquisition gate; AI or personalization cannot override the gate.

## Phase 3 - Catalog + processing

- [ ] PDF text extraction.
- [ ] EPUB extraction.
- [ ] TXT/HTML extraction.
- [ ] DOCX extraction.
- [ ] OCR for image-only scans.
- [ ] Metadata confidence/provenance.
- [ ] Cover handling.
- [ ] PostgreSQL full-text search.
- [ ] Semantic interaction/outcome event writer in canonical product domains.

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
- [ ] User goals and session continuity.
- [ ] Explicit preference management.

## Phase 5 - Adaptive research intelligence

Goal: introduce one bounded AI orchestration system that becomes more useful through evidence while remaining inspectable, reversible, and optional.

### AI foundation

- [ ] PostgreSQL persistence for `UserModel`, `PreferenceClaim`, `InteractionEvent`, `OutcomeEvent`, `Goal`, `Plan`, and `ActionDecision`.
- [ ] Canonical `ModelGateway`; product domains cannot call model SDKs directly.
- [ ] Deterministic context assembler with privacy minimization.
- [ ] Typed capability/tool registry over canonical product services.
- [ ] Planner structured-output validation.
- [ ] Deterministic action policy and approval gate.
- [ ] AI activity ledger.

### Learning

- [ ] Explicit vs inferred preference authority.
- [ ] Confidence/evidence/scoping model.
- [ ] Contradiction resolution.
- [ ] Preference decay for inferred claims.
- [ ] Outcome-driven reinforcement and negative feedback.
- [ ] User correction/forget flow.
- [ ] Pause-learning control.

### Research intelligence

- [ ] Search within one book.
- [ ] Cross-book lexical retrieval.
- [ ] Semantic retrieval with one canonical embedding-provider interface.
- [ ] Grounded book Q&A with page/section citations.
- [ ] Compare sources/editions.
- [ ] Timeline/entity/concept views derived from canonical documents.
- [ ] Reader-aware AI context.
- [ ] Active research goals spanning sessions.
- [ ] Explainable personalized discovery/recommendations.

### AI & Personalization control center

- [ ] What Bukmatika knows about me.
- [ ] Explicit preferences.
- [ ] Inferred preferences with confidence/evidence.
- [ ] Active goals.
- [ ] Delegated tasks.
- [ ] Activity ledger.
- [ ] Per-feature autonomy controls.
- [ ] Export/delete personalization data.
- [ ] Disable AI while preserving normal library operation.

Acceptance:

- every AI research claim can resolve to canonical source coordinates when the task is source-grounded;
- material AI actions have complete ledger evidence;
- explicit user preferences outrank inferred claims;
- a corrected/deleted learned claim stops influencing behavior;
- AI-disabled mode leaves discovery, acquisition, cataloging, library, and reading functional;
- no independent agent memory or provider SDK appears outside the canonical orchestration/model boundary.

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
