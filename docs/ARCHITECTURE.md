# Architecture

## Canonical flow

```text
User intent
  -> Search planner
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
```

Discovery and acquisition are intentionally separate. A source may be searchable without being downloadable.

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

## Search quality

Ranking should combine independently inspectable signals:

- textual relevance;
- subject/date/language fit;
- metadata completeness;
- source reliability;
- rights confidence;
- edition quality;
- format preference;
- duplicate consolidation.

Do not hide a black-box score behind a single number. Keep signals available for diagnostics and later tuning.

## Multi-user evolution

The domain model should include ownership boundaries from the beginning, but deployment can begin single-user/local-first. Personal annotations, uploaded assets, and reading history must never leak between users in hosted mode.
