# Adaptive AI System Architecture

## Purpose

Bukmatika's AI is not a chatbot attached to a bookshelf. It is a bounded personal library architect that helps the user discover, acquire, organize, read, and understand books while learning how that specific user prefers to work.

The AI must become more useful through use without becoming opaque, manipulative, or a second source of product truth.

Its loop is:

```text
observe -> interpret -> propose -> act within policy -> measure outcome -> learn -> adapt
```

The canonical product loop becomes:

```text
User intent
  -> Context assembler
  -> Personalization profile
  -> AI planner
  -> Product capability tools
      -> Discovery
      -> Catalog
      -> Rights
      -> Acquisition
      -> Processing
      -> Library
      -> Reader
      -> Research
  -> Policy / approval gate
  -> Action or recommendation
  -> Outcome event
  -> Learning engine
  -> Preference evidence
```

The AI never bypasses the rights engine, acquisition safety controls, catalog authority, or user approval policy.

## Product principle: learning by evidence

Bukmatika should distinguish what the user **said**, what the user **did**, and what the system **inferred**.

### Explicit preferences

Highest-authority user choices, for example:

- preferred languages;
- preferred formats;
- preferred edition age;
- primary-source vs secondary-source preference;
- reading density and explanation depth;
- citation format;
- whether OCR-heavy scans are acceptable;
- whether AI may automatically create collections;
- whether eligible acquisitions may auto-download;
- favorite subjects, periods, authors, regions, and source institutions.

Explicit preferences remain authoritative until the user changes them.

### Behavioral signals

Examples:

- repeatedly choosing EPUB over PDF when both exist;
- favoring facsimile scans for primary research;
- dismissing modern summaries;
- reading particular subjects deeply;
- frequently saving books from a specific institution;
- abandoning books with poor OCR;
- organizing books by period rather than author;
- asking for concise rather than expansive explanations.

Behavioral signals are evidence, not commands.

### Inferred preferences

The learning engine may derive a preference only when sufficient evidence exists. Every inference must carry:

- value;
- confidence;
- evidence count;
- first observed timestamp;
- last reinforced timestamp;
- scope;
- provenance event IDs;
- decay policy;
- whether the inference may affect ranking, presentation, or automation.

A weak inference may influence a suggestion. It must not silently authorize consequential actions.

## Canonical AI domains

### UserModel

The durable personalized model for one user. It contains references to explicit preferences and learned preference claims. It is not a free-form prompt blob.

### PreferenceClaim

A typed, inspectable claim such as:

```text
format.epub = preferred
scope = discovery
source = inferred
confidence = 0.86
support = 14 behavior events
```

Claims may be explicit, inferred, contradicted, superseded, expired, or deleted.

### InteractionEvent

An append-only record of a product interaction meaningful enough to support personalization. Examples include search submitted, result opened, edition selected, acquisition approved, acquisition rejected, book started, book abandoned, annotation created, AI suggestion accepted, AI suggestion dismissed, preference corrected.

Events should record product semantics, not raw surveillance exhaust. Keystroke logging, arbitrary cursor tracking, unrelated browsing telemetry, and hidden device profiling are out of scope.

### Goal

A durable user objective such as:

- build a reading list on pre-Columbian Atlantic contact theories;
- collect first-hand travel accounts from 1450-1550;
- finish three books this month;
- compare English translations of one work.

Goals may span multiple sessions and allow the AI to maintain continuity.

### SessionContext

Short-lived working context for the current interaction. It contains the active goal, selected books, current reader location, recent commands, temporary filters, and capabilities available to the AI.

### Plan

A structured set of proposed steps produced by the planner. Plans reference typed product capabilities rather than arbitrary code execution.

### ActionDecision

The policy result for one planned action:

- `allow`
- `allow_with_notification`
- `require_approval`
- `deny`

### OutcomeEvent

Records whether an action or suggestion helped, was ignored, rejected, undone, or corrected. This closes the learning loop.

## One orchestrator, many capabilities

There must be one canonical AI orchestration authority. Individual product areas expose capabilities but do not embed independent agents with their own memory or policy.

Examples of typed capabilities:

```text
discovery.search(intent)
discovery.expand_query(intent)
catalog.resolve(candidate)
library.create_collection(spec)
library.add_work(collection_id, work_id)
reader.open(document_id, locator)
research.search(selection, query)
research.answer(selection, question)
acquisition.request(asset_id)
preferences.propose(claim)
goals.update(goal_id, change)
```

This keeps business logic in the product domains and leaves the AI responsible for interpretation, planning, and adaptation.

## Context assembler

The model should never receive the entire user history. A deterministic context assembler selects the smallest useful context for the current task.

Inputs may include:

1. current user request;
2. active goal;
3. current screen and selected entities;
4. relevant explicit preferences;
5. high-confidence learned claims applicable to this domain;
6. recent session context;
7. canonical catalog/document records required for the task;
8. policy and tool availability.

The assembler must expose why a remembered preference was included. This enables a UI affordance such as:

> Prioritized EPUB because you usually select EPUB when both formats are available.

## Learning engine

### Signal hierarchy

Use weighted evidence rather than treating every click equally:

1. explicit user statement or setting;
2. explicit correction of AI behavior;
3. repeated accepted choices;
4. repeated implicit behavior;
5. isolated behavior.

Negative evidence matters. A user repeatedly undoing an automatic organization action should rapidly reduce confidence in that automation preference.

### Contradiction handling

Preferences are not universal truths. The user might prefer EPUB for novels and searchable PDF facsimiles for historical source material.

Claims therefore support scopes such as:

- global;
- discovery;
- research;
- reader;
- subject;
- collection;
- format;
- device;
- goal.

More specific, recent, explicit evidence wins over broad inferred evidence.

### Decay

Inferred preferences decay when not reinforced. Explicit preferences do not decay automatically.

Decay prevents a six-month-old temporary behavior from becoming permanent personality.

### Cold start

Do not force a long onboarding questionnaire.

Start with a handful of useful optional choices, then learn gradually. The first experience must work with an empty UserModel.

## Autonomy ladder

Personalization and autonomy are separate controls.

### Level 0 - Reactive

AI acts only when directly asked.

### Level 1 - Suggestive

AI proposes searches, collections, metadata fixes, reading paths, or related works. User approves actions.

### Level 2 - Assisted

AI may perform reversible low-risk organization actions under explicit standing preferences, while clearly reporting them.

### Level 3 - Delegated

User can delegate bounded goals such as:

> Keep this collection organized by century and flag newly discovered public-domain primary sources.

Delegation must have defined scope, allowed capabilities, budget/resource limits, and an easy stop control.

The product should begin at Levels 0-1. Higher autonomy ships only after audit logging, undo, policy controls, and user-facing activity history exist.

## Consequence policy

AI reasoning never directly authorizes consequential operations.

Examples requiring deterministic policy:

- downloading remote assets;
- deleting library material;
- exporting private annotations;
- changing durable preferences;
- starting expensive OCR/model work;
- making network requests to new domains;
- bulk-changing catalog organization.

Rights and security rules always outrank personalization.

## AI activity ledger

Every material AI-assisted action should be inspectable through a user-facing activity history:

- what it did;
- why it did it;
- which preference/goal influenced it;
- what product records were affected;
- whether the action is reversible;
- model/provider used when relevant;
- user approval state;
- result/outcome.

This is both a trust feature and a debugging instrument.

## Memory controls

Users need a dedicated **AI & Personalization** surface with:

- What Bukmatika knows about me;
- explicit preferences;
- inferred preferences and confidence;
- active goals;
- delegated tasks;
- AI activity ledger;
- corrections;
- forget individual claims;
- clear learned behavior by scope;
- pause learning;
- disable AI while retaining the normal library product;
- export/delete personalization data.

Deleting a learned preference must remove its active claim. Product telemetry retention, if any, must follow the privacy policy rather than silently reconstructing the deleted claim.

## Model/provider architecture

Model access sits behind one canonical `ModelGateway`.

Responsibilities:

- task-specific model selection;
- provider configuration;
- structured-output validation;
- token/resource budgets;
- retries and timeouts;
- privacy routing policy;
- observability;
- capability fallback.

Product domains must never call model SDKs directly.

Use the cheapest deterministic mechanism that solves the task. Metadata normalization, filtering, rights decisions, deduplication, ranking rules, and ordinary full-text search do not require an LLM.

LLMs are appropriate for tasks such as:

- interpreting complex research intent;
- query expansion;
- summarizing source-grounded text;
- planning multi-step research flows;
- synthesizing across selected documents;
- explaining recommendations.

Embeddings are introduced only for semantic retrieval and must not become a second memory authority.

## Recommendation architecture

Recommendations should be explainable compositions of signals rather than one opaque recommender score.

Candidate signals include:

- relevance to active goal;
- explicit interests;
- learned topical affinity;
- preferred era/language/format;
- source quality;
- rights availability;
- edition quality;
- novelty vs already-owned works;
- diversity across viewpoints/sources;
- reading behavior;
- collection gaps.

The UI should be able to answer **Why this?** with concrete reasons.

Personalization may change ordering, but the user must still be able to switch to neutral/date/title/source sorts.

## Research companion behavior

Inside a book, the AI should understand the current reading location and optionally provide:

- define/explain;
- summarize selected pages;
- identify people, places, events, and concepts;
- connect the passage to other owned books;
- show competing accounts;
- build notes or study questions;
- trace citations;
- continue an active research goal.

Every factual synthesis derived from books must resolve to source coordinates.

## Proactive intelligence

Proactive behavior should be sparse and useful, not notification confetti.

Examples:

- a newly indexed public-domain edition substantially improves OCR quality for a book the user owns;
- a collection has three duplicate editions that can be consolidated;
- a research goal has a clear source gap;
- a book the user is reading references another work already in their library;
- a newly discovered authorized source matches an explicitly tracked topic.

No proactive network acquisition occurs without the applicable autonomy and rights policies.

## Privacy architecture

Personalization data is private user data.

Requirements:

- tenant/user ownership on every durable personalization entity;
- no cross-user learning from private activity by default;
- no model-training use implied by normal product usage;
- context minimization before remote model calls;
- redact unrelated private annotations from model context;
- explicit policy for local vs remote model routing;
- export and deletion support;
- auditable retention rules.

## Evaluation

The AI cannot be considered successful because responses sound intelligent.

Measure:

### Personalization quality

- suggestion acceptance rate by feature;
- correction/undo rate;
- preference prediction calibration;
- cold-start usefulness;
- stale-preference rate;
- diversity/novelty of recommendations.

### Agent reliability

- plan completion rate;
- invalid tool-call rate;
- policy-denied action attempts;
- grounded citation validity;
- unnecessary model-call rate;
- action rollback rate.

### Trust

- percentage of AI actions with complete ledger evidence;
- successful preference deletion/forget verification;
- percentage of recommendations with inspectable reasons;
- unauthorized autonomy incidents, target: zero.

## Initial implementation sequence

1. Define persistence for `UserModel`, `PreferenceClaim`, `InteractionEvent`, `Goal`, `Plan`, `ActionDecision`, and `OutcomeEvent`.
2. Add the canonical event vocabulary and event writer to product domains.
3. Implement explicit preference management before inference.
4. Implement deterministic context assembly.
5. Introduce `ModelGateway` and one planner contract.
6. Add Level 0-1 AI orchestration using discovery and library capabilities.
7. Implement outcome collection and confidence-based preference inference.
8. Ship the AI & Personalization control center and activity ledger.
9. Add reader-aware research assistance.
10. Only then evaluate bounded delegated autonomy.

## Non-negotiable invariants

- The AI is not a second catalog.
- The AI is not the rights authority.
- The AI is not the download authority.
- The AI cannot silently promote an inference into a durable explicit preference.
- User corrections outrank learned behavior.
- Personalization must be inspectable and reversible.
- Core reading/library features work when AI is disabled.
- One orchestration authority, one model gateway, one preference authority.
- No production fake memories, fake recommendations, or mock AI behavior.
