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

Local AI is optional. The API installation defaults to no model provider, and ordinary discovery, acquisition, catalog, library, reader, and evidence-building paths must continue to work without one. Installation owners may still set `BUKMATIKA_MODEL_PROVIDER=ollama` and `BUKMATIKA_OLLAMA_MODEL` as the default local model. An authenticated local profile may then inherit that default, disable model use for itself, or select another model that the loopback Ollama runtime reports as installed from the **AI & Personalization** surface. Profile model changes are durable and take effect for subsequent requests without restarting the API.

The consumer setup flow cannot change the Ollama address or route private context elsewhere. `BUKMATIKA_OLLAMA_BASE_URL`, `BUKMATIKA_MODEL_TIMEOUT_SECONDS`, and `BUKMATIKA_MODEL_READINESS_TIMEOUT_SECONDS` remain installation-controlled, the Ollama adapter accepts loopback HTTP origins only, and model HTTP clients ignore environment proxy routing. Installed-model discovery uses only Ollama's local model-list metadata endpoint and sends no book text, questions, annotations, prompts, or personalization context. When AI is disabled for a profile, Bukmatika does not probe the local model runtime.

Runtime state distinguishes unconfigured, unreachable, invalid, missing-model, and ready conditions. The reader only offers grounded local synthesis in the ready state and otherwise stays on canonical evidence retrieval. Model-backed answers remain subject to the same principal ownership, action policy, evidence, citation-validation, and activity-ledger rules as the rest of the product.

### Consumer local-model setup

Open **AI & Personalization -> Local AI runtime**. The profile may:

1. **Use installation default** to inherit the deployment's configured provider/model.
2. **Use selected model** to choose one of the models reported by the local Ollama runtime.
3. **Disable model for this profile** without affecting any other local profile.

The model picker does not install or pull models. If Ollama reports no installed models, install the intended model through the local Ollama tooling and refresh the card. If AI assistance is disabled in the control center, model discovery is intentionally blocked until AI is enabled again.

### Verify a real local model

The release-proof commands are installation-scoped rather than profile-scoped. Configure the installation default with an installed Ollama model before running them.

After the API package is installed and the configured Ollama model is present locally, run:

```text
bukmatika-ai-smoke
```

The command uses the same canonical `ModelGateway` contract as the application. It first requires the installation-default provider and selected model to report ready, then sends a public, context-free structured-output verification request to the actual model. Success prints provider/model/routing identity plus the exact verification token. Unconfigured, unreachable, invalid, missing-model, transport, or structured-response failures exit non-zero. The smoke command does not use book text, reader context, annotations, or personal data.

For the full grounded-path release proof, run against a migrated Bukmatika database and the same ready installation-default local model:

```text
bukmatika-grounded-ai-proof
```

This command creates a synthetic principal, library entry, document, section, and chunk inside one rollback-only PostgreSQL transaction. It then executes the real `GroundedResearchSynthesisService`, requires canonical evidence citations, verifies the persisted `research.answer` plan, model-completion audit metadata, and AI activity-ledger projection, rolls the probe transaction back, and confirms the synthetic principal did not persist. Successful output contains only provider/model/routing identity, counts, citation IDs, and proof flags; it never emits the probe passage or question.

The presence of these commands is not itself release evidence. Phase 5 only closes the real-runtime gate after `bukmatika-grounded-ai-proof` is actually executed successfully against an installed local model and that result is recorded for the release candidate.

## Non-goals

- Circumventing DRM, authentication, paywalls, lending controls, or access restrictions.
- Downloading works merely because a file URL is discoverable.
- Treating “free to access” as equivalent to “free to redistribute.”
- Bulk-harvesting third-party services in violation of their published API or robot policies.

## Working name

**Bukmatika** evokes a machine for books, but the product should feel less like a database and more like a living research library: find, verify, collect, understand.
