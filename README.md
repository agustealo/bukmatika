# Bukmatika

**A rights-aware discovery, acquisition, catalog, and reading engine for the open book web.**

Bukmatika turns a natural-language research request into a trustworthy personal library. A reader can ask for something broad such as **“books about the Old World before Columbus”** and Bukmatika can plan searches across supported catalogs and the wider web, discover relevant editions, verify what may legally be acquired, download authorized files, normalize metadata, deduplicate editions, extract searchable text, and organize the result into a polished reading library.

Bukmatika is not a piracy crawler. Automated acquisition is limited to works that the source and rights evidence indicate are public-domain, openly licensed, author/publisher-authorized, or otherwise legally downloadable. Borrow-only, preview-only, restricted, and unknown-rights results remain discoverable but are not silently downloaded.

## Product thesis

Most ebook apps begin **after** the user already owns the file. Search engines find pages, not personal libraries. Digital archives expose enormous collections, but each has different metadata, formats, search syntax, and access rules.

Bukmatika bridges those worlds:

`intent -> search plan -> source discovery -> rights decision -> edition resolution -> acquisition -> extraction -> catalog -> read/research`

The core product is not “a scraper with a bookshelf.” It is a **book acquisition and knowledge operating system** with provenance.

## Core capabilities

- Natural-language topic, title, author, period, place, language, and format search.
- Federated source adapters instead of one brittle scraper.
- General-web discovery for authorized downloadable books and source landing pages.
- Rights-aware acquisition policy with evidence and provenance.
- PDF, EPUB, TXT, HTML/web, DOCX, and other supported document ingestion.
- Exact-file hashing plus work/edition-level deduplication.
- Metadata normalization for title, author, publisher, date, language, subjects, identifiers, license, and source.
- Full-text extraction and indexing.
- OCR pipeline for image-only public-domain scans.
- Personal collections, tags, smart shelves, reading status, progress, highlights, and notes.
- Semantic search across a user’s library.
- Grounded “ask this book” and cross-book research with page/section citations.
- Citation export and durable source provenance.
- Offline-capable reading surfaces and OPDS-compatible interoperability as the product matures.

## Initial source strategy

Adapters should prefer documented machine-readable interfaces and respect each provider’s access rules. Initial targets:

1. Open Library / Internet Archive metadata and public-access availability.
2. Project Gutenberg catalog/robot endpoints permitted for automation.
3. Library of Congress digitized books and collections API.
4. Standard Ebooks through its supported feed/access model when project access is approved.
5. Additional public-domain and open-access catalogs through explicit adapters.
6. General-web discovery as a discovery layer, never as a license bypass.

## Rights states

Every candidate has an explicit acquisition state:

- `public_domain`
- `open_license`
- `authorized_download`
- `borrow_only`
- `preview_only`
- `unknown`
- `restricted`

Only the first three are eligible for unattended download. A source adapter must attach evidence for the rights decision.

## Architecture principles

- **Canonical domain model:** Work, Edition, Asset, SourceRecord, RightsEvidence, Acquisition, Document, Collection, Annotation.
- **Adapter boundary:** providers never leak source-specific response shapes into product code.
- **Policy before download:** discovery and acquisition are intentionally separate.
- **Provenance everywhere:** every asset and metadata field can be traced back to a source.
- **Local-first where practical:** downloaded books remain useful without a network connection.
- **No duplicate authorities:** one canonical catalog, one rights engine, one acquisition pipeline.
- **Progressive complexity:** PostgreSQL first; add infrastructure only when measured load requires it.
- **Real integrations only:** no production mock providers or fake catalog data.

## Repository status

Bukmatika now has a real discovery, lawful-acquisition, processing, reader/research, personalization, and bounded AI spine under active consumer hardening. The exact implementation state and remaining gates live in `docs/ROADMAP.md`; architecture authority lives in `docs/ARCHITECTURE.md` and `docs/AI_ARCHITECTURE.md`.

Local AI is optional. The API defaults to no model provider, and ordinary discovery, acquisition, catalog, library, reader, and evidence-building paths must continue to work without one. When `BUKMATIKA_MODEL_PROVIDER=ollama` and `BUKMATIKA_OLLAMA_MODEL` name an installed local model, Bukmatika probes the loopback Ollama runtime before advertising model-backed research. Runtime state distinguishes unconfigured, unreachable, invalid, missing-model, and ready conditions. The reader only offers grounded local synthesis in the ready state and otherwise stays on canonical evidence retrieval.

The Ollama integration is loopback-only and does not use environment proxy routing. `BUKMATIKA_MODEL_TIMEOUT_SECONDS` limits generation calls and `BUKMATIKA_MODEL_READINESS_TIMEOUT_SECONDS` independently bounds the lightweight runtime/model readiness probe. Model-backed answers remain subject to the same principal ownership, action policy, evidence, citation-validation, and activity-ledger rules as the rest of the product.

### Verify a real local model

After the API package is installed and the configured Ollama model is present locally, run:

```text
bukmatika-ai-smoke
```

The command uses the same canonical `ModelGateway` as the application. It first requires the provider and selected model to report ready, then sends a public, context-free structured-output verification request to the actual model. Success prints provider/model/routing identity plus the exact verification token. Unconfigured, unreachable, invalid, missing-model, transport, or structured-response failures exit non-zero. The smoke command does not use book text, reader context, annotations, or personal data.

This smoke proves the installed model runtime itself. Phase 5 still requires a recorded real-runtime grounded-answer proof against canonical persisted research evidence before delegated autonomy can open.

## Non-goals

- Circumventing DRM, authentication, paywalls, lending controls, or access restrictions.
- Downloading works merely because a file URL is discoverable.
- Treating “free to access” as equivalent to “free to redistribute.”
- Bulk-harvesting third-party services in violation of their published API or robot policies.

## Working name

**Bukmatika** evokes a machine for books, but the product should feel less like a database and more like a living research library: find, verify, collect, understand.
