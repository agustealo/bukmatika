# Product Definition

## Promise

Bukmatika turns a research intent into a lawful, organized, searchable personal library.

A user should be able to type:

> Find primary and secondary books about the Old World before Columbus, prefer public-domain English editions, and organize them by region and century.

The product should then:

1. understand the request;
2. produce a visible search plan;
3. query supported catalogs and approved web discovery providers;
4. merge duplicate works and editions;
5. show why each result is relevant;
6. classify access and rights state with evidence;
7. acquire only eligible files;
8. verify and normalize those files;
9. extract searchable text;
10. place the work into a useful library structure;
11. support reading, annotation, citation, and cross-book research.

## Product surfaces

### Discover

A single intent box with optional advanced filters for title, author, subject, language, date range, source, format, and rights state. Search progress is transparent: users can see which source adapters responded, failed, or were rate-limited.

### Result dossier

Each result is a work/edition dossier rather than a naked link. It includes:

- title and contributors;
- work vs edition identity;
- publication and language metadata;
- available formats;
- source and canonical landing page;
- access state;
- rights state and supporting evidence;
- relevance explanation;
- duplicate/alternate editions;
- acquisition action when eligible.

### Library

Library organization combines explicit collections with smart shelves:

- Recently added
- Reading
- Finished
- Public domain
- By subject
- By period
- By place
- By author
- Needs metadata review
- Needs OCR

### Reader

The reader must handle EPUB, PDF, text, and normalized HTML without forcing every format through the same rendering path. Reading state, highlights, notes, bookmarks, and citations belong to the canonical document model rather than to a viewer-specific component.

### Research

Research mode operates only on text that belongs to the user library or is otherwise authorized for processing. Answers must resolve back to book, edition, section/page, and source provenance.

## Differentiators

### Intent-to-library

The user asks for a body of knowledge, not a filename. Bukmatika can fan the request into title, subject, person, place, date, language, and file-format searches.

### Rights-aware acquisition

The rights decision is a first-class product object. “Found on the internet” is never treated as permission to download.

### Edition intelligence

Bukmatika separates Work from Edition. A scanned 1898 PDF, a cleaned EPUB, and an OCR text can represent the same intellectual work while retaining their own provenance and quality scores.

### Provenance graph

Metadata, files, extracted text, citations, and AI-derived annotations retain links to their evidence. The system can answer “where did this title/date/license/file come from?”

### Knowledge atlas

A future research layer can organize a collection into timelines, places, people, concepts, contradictions, citations, and source relationships. This should be built from the canonical catalog, not as a parallel AI database.

## Missing gaps that are now explicit requirements

- Rights and license evidence.
- Work/edition/asset distinction.
- Duplicate resolution.
- Provenance and metadata confidence.
- Safe file inspection before parsing.
- OCR for scanned historical books.
- Corrupt-file and partial-download recovery.
- Provider rate limiting and backoff.
- Search cancellation and resumable acquisition jobs.
- Accessibility and keyboard-first reader behavior.
- Offline behavior and local cache ownership.
- Export/import and data portability.
- Citation formats.
- Privacy boundaries for private libraries.
- Source health monitoring.
- Abuse controls for arbitrary URL ingestion.
- Clear behavior when rights are unknown.

## Product guardrails

Bukmatika does not defeat DRM, authentication, paywalls, lending controls, CAPTCHAs, or technical access restrictions. It does not infer download permission from a search-engine result or a file extension. Unknown rights remain unknown until trustworthy evidence changes that state.
