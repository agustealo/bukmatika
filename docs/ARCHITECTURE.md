# Architecture

## Canonical flow

```text
User intent
  -> Context assembler
      -> explicit preferences
      -> relevant learned preferences
      -> active goal/session context
  -> AI planner / search planner
  -> Policy + approval gate
  -> Discovery coordinator
      -> Source adapter(s)
      -> Web discovery adapter
  -> Candidate normalization
  -> Work / edition resolver
  -> Rights engine
  -> Result dossier
  -> Acquisition coordinator
  -> File safety + integrity
  -> Extract / OCR / normalize
  -> Catalog + index
  -> Reader / research
  -> Outcome event
  -> Learning engine
  -> Preference evidence
```

Discovery and acquisition are intentionally separate. A source may be searchable without being downloadable. Personalization may influence planning, ordering, explanations, and reversible organization behavior, but it may not override rights, safety, ownership, or approval policy.

See `docs/AI_ARCHITECTURE.md` for the canonical adaptive-AI design.

## Bounded contexts

### Discovery

Accepts a canonical `SearchIntent` and asks source adapters for `DiscoveryCandidate` records. Provider-specific response shapes stop at the adapter boundary.

### Catalog

Owns Work, Edition, Contributor, Identifier, Subject, Asset, and SourceRecord identity. It is the only authority for duplicate resolution.

### Rights

Owns `RightsEvidence`, `RightsState`, and acquisition eligibility. Source adapters may submit evidence, but adapters do not directly authorize downloads.

### Acquisition

Owns download jobs, retries, checksums, content-type verification, byte limits, redirects, and durable acquisition state.

### Document processing

Owns format detection, PDF/EPUB/DOCX/TXT/HTML extraction, OCR scheduling, normalized text, section/page mapping, and parser diagnostics.

### Library

Owns user collections, progress, bookmarks, notes, highlights, and user metadata overrides.

### Personalization

Owns explicit preferences, learned `PreferenceClaim` records, goals, interaction/outcome events, and learning state. It never stores bibliographic truth and never becomes an authorization system.

### AI orchestration

Owns context assembly, structured planning, model access, and typed capability invocation. There is one canonical orchestrator and one `ModelGateway`; product domains do not embed independent agents or call model providers directly.

### Research

Owns lexical/semantic retrieval and grounded answers. It references canonical document chunks and never becomes a second source of bibliographic truth.

## Runtime philosophy

Start with the fewest durable authorities:

- Web: Next.js + TypeScript.
- API/workers: Python + FastAPI.
- Database: PostgreSQL.
- Object/file storage: local filesystem in development behind a storage interface; S3-compatible storage when deployment requires it.
- Search: PostgreSQL full-text/trigram first. Add vector indexing only when semantic retrieval ships.
- Queue: database-backed jobs first. Introduce Redis or a dedicated broker only when measured concurrency justifies another runtime dependency.
- AI: one model gateway. Prefer deterministic code for deterministic jobs; invoke models only for tasks that genuinely require language/semantic reasoning.

This avoids paying an infrastructure tax before the product needs it.

## Canonical entities

### Work

The intellectual work. Stable across editions.

### Edition

A publication manifestation of a Work. Carries publisher/date/language/ISBN and similar edition-specific metadata.

### Asset

A concrete digital file or remote representation: PDF, EPUB, TXT, HTML, DOCX, OCR derivative, cover, etc.

### SourceRecord

The exact provider record from which metadata or an asset was discovered.

### RightsEvidence

A statement, machine field, license URI, public-domain flag, or other evidence used by the rights engine.

### Acquisition

A stateful attempt to obtain an eligible Asset.

### Document

The normalized readable/extractable representation tied to one Asset.

### UserModel

The durable container for one user's explicit and inferred personalization state. It references typed claims rather than storing a monolithic free-form profile.

### PreferenceClaim

An inspectable preference with value, source, confidence, scope, timestamps, provenance, and lifecycle state.

### InteractionEvent / OutcomeEvent

Append-only semantic events used to learn from meaningful product behavior and measure whether AI assistance helped.

### Goal / Plan / ActionDecision

Durable user objective, structured proposed execution, and deterministic policy result for an AI-planned action.

## Trust boundaries

Remote files are hostile input. The acquisition pipeline must enforce:

- HTTPS by default;
- redirect limits;
- DNS/IP checks to prevent SSRF into local/private ranges;
- content-length and streamed-byte limits;
- MIME sniffing independent of extension;
- archive expansion limits;
- checksum calculation;
- parser timeouts;
- quarantine on malformed or suspicious files;
- no execution of embedded scripts/macros;
- sandboxed conversion/OCR where external binaries are used.

AI/model calls form a separate privacy and action trust boundary:

- send only task-relevant context;
- never give models direct database or arbitrary network authority;
- expose typed product capabilities instead of arbitrary code execution;
- validate structured model output before use;
- run planned actions through deterministic policy;
- require approval for consequential actions according to the autonomy policy;
- log material AI actions and their reasons;
- keep user corrections and memory controls authoritative.

## Search quality

Ranking should combine independently inspectable signals:

- textual relevance;
- active-goal relevance;
- explicit user preferences;
- applicable high-confidence learned preferences;
- subject/date/language fit;
- metadata completeness;
- source reliability;
- rights confidence;
- edition quality;
- format preference;
- duplicate consolidation;
- novelty/diversity where requested.

Do not hide a black-box score behind a single number. Keep signals available for diagnostics and later tuning. Users must be able to choose neutral/date/title/source sorting when they do not want personalized ordering.

## Multi-user evolution

The domain model should include ownership boundaries from the beginning, but deployment can begin single-user/local-first. Personal annotations, uploaded assets, reading history, goals, learned preferences, and AI activity must never leak between users in hosted mode.
