# Delivery Roadmap

The roadmap is organized by vertical slices. A slice is complete only when the real user path works end-to-end.

## Phase 0 - Foundation

- [x] Product thesis and guardrails.
- [x] Canonical architecture and rights states.
- [x] Repository initialization.
- [ ] CI quality gates.
- [ ] Database migrations.
- [ ] Local development bootstrap.
- [ ] Structured logging and request correlation.

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

Acceptance: “Old World before Columbus” returns live source results with source provenance and no fake catalog rows.

## Phase 2 - Lawful acquisition

Goal: an eligible result becomes a durable verified asset.

- [ ] Rights evidence persistence and decision audit.
- [ ] Acquisition jobs and retry state machine.
- [ ] SSRF-safe downloader.
- [ ] MIME sniffing, size limits, checksum, quarantine.
- [ ] Storage abstraction.
- [ ] Project Gutenberg permitted file acquisition.
- [ ] Internet Archive public-access asset acquisition.

Acceptance: public-domain/open assets download reproducibly; unknown/borrow/restricted assets cannot cross the acquisition gate.

## Phase 3 - Catalog + processing

- [ ] PDF text extraction.
- [ ] EPUB extraction.
- [ ] TXT/HTML extraction.
- [ ] DOCX extraction.
- [ ] OCR for image-only scans.
- [ ] Metadata confidence/provenance.
- [ ] Cover handling.
- [ ] PostgreSQL full-text search.

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

## Phase 5 - Research intelligence

- [ ] Search within one book.
- [ ] Cross-book lexical retrieval.
- [ ] Semantic retrieval with one canonical embedding provider interface.
- [ ] Grounded book Q&A with page/section citations.
- [ ] Compare sources/editions.
- [ ] Timeline/entity/concept views derived from canonical documents.

AI features are optional product layers. Core discovery, acquisition, cataloging, and reading must remain functional without an AI provider.

## Phase 6 - Interop + market readiness

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
