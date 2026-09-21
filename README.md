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

The repository is being initialized as a production-oriented foundation. See `docs/PRODUCT.md`, `docs/ARCHITECTURE.md`, `docs/RIGHTS_AND_SOURCES.md`, and `docs/ROADMAP.md` as the implementation lands.

## Non-goals

- Circumventing DRM, authentication, paywalls, lending controls, or access restrictions.
- Downloading works merely because a file URL is discoverable.
- Treating “free to access” as equivalent to “free to redistribute.”
- Bulk-harvesting third-party services in violation of their published API or robot policies.

## Working name

**Bukmatika** evokes a machine for books, but the product should feel less like a database and more like a living research library: find, verify, collect, understand.
