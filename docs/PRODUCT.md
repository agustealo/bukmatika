# Product Definition

## Promise

Bukmatika turns a research intent into a lawful, organized, searchable personal library that becomes more useful as it learns how the individual reader researches, reads, organizes, and reasons.

A user should be able to type:

> Find primary and secondary books about the Old World before Columbus, prefer public-domain English editions, and organize them by region and century.

The product should then:

1. understand the request;
2. combine it with relevant user preferences and active goals;
3. produce a visible search/research plan;
4. query supported catalogs and approved web discovery providers;
5. merge duplicate works and editions;
6. show why each result is relevant;
7. classify access and rights state with evidence;
8. acquire only eligible files;
9. verify and normalize those files;
10. extract searchable text;
11. place the work into a useful library structure;
12. support reading, annotation, citation, and cross-book research;
13. learn from meaningful corrections and repeated choices without silently taking control away from the user.

## Product surfaces

### Discover

A single intent box with optional advanced filters for title, author, subject, language, date range, source, format, and rights state. Search progress is transparent: users can see which source adapters responded, failed, or were rate-limited.

Discovery becomes progressively personal. Bukmatika may learn that a user usually prefers EPUB, facsimile primary sources, English translations, or works from a particular period, but personalized ranking must always remain explainable and switchable to neutral sorting.

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
- personalization explanation when applicable;
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
- Active research goals
- Needs metadata review
- Needs OCR

The AI may suggest organization, identify duplicate editions, or create a proposed research collection. Durable or bulk organization follows deterministic policy and the user's explicit approval controls. It is not implicitly authorized by a research delegation or learned preference.

### Reader

The reader must handle EPUB, PDF, text, and normalized HTML without forcing every format through the same rendering path. Reading state, highlights, notes, bookmarks, and citations belong to the canonical document model rather than to a viewer-specific component.

The reader-aware assistant can operate on current context without losing source grounding. Examples include explaining a selected passage, tracing a cited work, finding another owned book that discusses the same event, or continuing an active research goal.

### Research

Research mode operates only on text that belongs to the user library or is otherwise authorized for processing. Answers must resolve back to book, edition, section/page, and source provenance.

The AI can maintain durable goals across sessions, such as building a collection, comparing translations, identifying contradictory historical accounts, or filling known gaps in a bibliography.

Research also contains Bukmatika's first bounded Level 2 delegation lane. A user can take a research question and explicitly selected owned books, draft a model-generated plan, inspect the deterministic policy decisions, select eligible read-only `research.search` steps, set finite budgets, create an exact approval request, approve or reject it, explicitly start it, monitor it, and stop it. Approval is scoped to that exact bounded run rather than to the capability forever.

### AI & Personalization

A dedicated control center makes the adaptive system visible and governable.

It includes:

- **What Bukmatika knows about me**
- explicit preferences
- inferred preferences with confidence and evidence
- active research/reading goals
- bounded delegated tasks and their status
- explicit Level 2 read-only consent and revoke control
- exact delegation approve/reject/start/stop controls
- AI activity ledger
- corrections and undo history
- scoped autonomy controls
- pause learning
- clear learned behavior by scope
- forget individual learned claims
- export/delete personalization data
- disable AI without disabling the normal library/reader

The product must be able to answer questions such as:

> Why did you rank this edition first?

> Why did you create this collection?

> What have you learned about how I research?

> Forget that I prefer modern translations.

> What exactly did I approve this delegated research run to do?

## Adaptive AI experience

Bukmatika should feel increasingly familiar without becoming presumptuous.

### Cold start

The first run works with no profile. The user may optionally choose a few high-value preferences, but there is no long personality questionnaire.

### Learn through use

Meaningful product actions produce semantic learning evidence. Repeated edition selections, accepted suggestions, corrected recommendations, abandoned poor-OCR scans, organization choices, and explicit feedback can refine preferences.

### Explain adaptation

When personalization changes an outcome, the product exposes a concise reason, for example:

> Ranked this higher because it is a public-domain English edition and you usually choose searchable EPUBs for secondary sources.

### Respect context

Preferences may differ by task. A user may prefer clean EPUB for ordinary reading while preferring original facsimile PDFs for primary-source research. The model must support scoped preferences rather than flattening the person into one permanent profile.

### Bounded autonomy

Bukmatika separates personalization from authority. Learning what a user prefers does not silently grant permission to act.

- **Level 0, Reactive:** perform work only in direct response to the user, within deterministic policy.
- **Level 1, Suggestive:** propose useful searches, organization, related works, research steps, or reversible preference changes. Durable actions remain separately controlled.
- **Level 2, Bounded delegated:** execute only an explicitly selected, exact, finite delegated plan after current Level 2 consent, exact per-run approval, and explicit start. The current production lane is read-only `research.search` over explicitly selected owned books.
- **Level 3, Reserved:** broader delegation such as standing goals, dynamic replanning, delegated writes, or consequential capabilities remains closed until separately designed and proven.

Level 2 consent is not standing capability permission. The model cannot approve, start, expand, reorder, rebudget, or silently replan its own delegated work. Every delegated run has fixed runtime/retry/attempt ceilings, current policy/state/context revalidation, durable stop/revoke behavior, and inspectable audit evidence.

Rights, safety, privacy, ownership, acquisition controls, capability contracts, and user approval always outrank learned preferences or delegated goals.

## Differentiators

### Intent-to-library

The user asks for a body of knowledge, not a filename. Bukmatika can fan the request into title, subject, person, place, date, language, and file-format searches.

### Personal research architect

Bukmatika can maintain goals, learn how the user works, plan across discovery/library/reader/research capabilities, and improve through outcomes. This is one canonical adaptive system rather than separate chatbots hidden in each screen.

Its autonomy is also inspectable. A bounded delegated research run is represented by durable plan, policy, consent, approval, budget, attempt, dispatch, stop, and outcome state instead of an opaque background agent.

### Rights-aware acquisition

The rights decision is a first-class product object. “Found on the internet” is never treated as permission to download.

### Edition intelligence

Bukmatika separates Work from Edition. A scanned 1898 PDF, a cleaned EPUB, and an OCR text can represent the same intellectual work while retaining their own provenance and quality scores.

### Provenance graph

Metadata, files, extracted text, citations, learned claims, AI actions, and AI-derived annotations retain links to their evidence. The system can answer “where did this title/date/license/file/recommendation come from?”

### Knowledge atlas

A future research layer can organize a collection into timelines, places, people, concepts, contradictions, citations, and source relationships. This should be built from the canonical catalog, not as a parallel AI database.

### Explainable recommendations

Personalization changes candidate ordering using inspectable signals rather than an opaque master score. Users can always switch to non-personalized sorting and ask why an item was recommended.

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
- Canonical personalization domain.
- Explicit vs inferred preference separation.
- Confidence, scope, provenance, and decay for learned preferences.
- User-visible AI activity ledger.
- Goal continuity across sessions.
- Broader per-feature autonomy controls beyond the proven Level 2 research lane.
- One canonical model/provider gateway.
- Context minimization for remote model calls.
- Evaluation for recommendation quality, grounding, correction rate, unnecessary model usage, and delegated-run reliability.
- Separate design and proof before adding any second delegated capability, delegated write, Level 3 authority, or standing approval.

## Product guardrails

Bukmatika does not defeat DRM, authentication, paywalls, lending controls, CAPTCHAs, or technical access restrictions. It does not infer download permission from a search-engine result or a file extension. Unknown rights remain unknown until trustworthy evidence changes that state.

The AI does not become a second catalog, rights authority, download authority, or hidden user-control plane. User corrections outrank learned behavior, learned preferences are inspectable and reversible, and the core library remains functional with AI disabled.

A delegation cannot authorize itself. Model output is always a proposal until deterministic policy and the applicable user-control gates permit it. Current Level 2 is deliberately read-only and cannot silently grow into acquisition, durable writes, arbitrary tools, or broader agent authority.
