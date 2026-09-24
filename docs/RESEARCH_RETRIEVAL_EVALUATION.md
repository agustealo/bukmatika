# Research Retrieval Evaluation

Bukmatika does not add semantic retrieval because an architecture checklist says it should. PostgreSQL full-text search remains the canonical retrieval path until measured recall on representative research tasks demonstrates a material gap.

## Authority boundary

The evaluator is read-only. It calls the same principal-scoped `ResearchRepository.search_owned_passages()` path used by product research. It does not create another search implementation, copy book text, persist embeddings, create vector or graph records, or call a model.

Every expected relevant passage must already exist as canonical Bukmatika evidence. Before scoring a case, the evaluator verifies the expected `document_id`, `section_id`, `chunk_id`, `char_start`, and `char_end` against the selected principal-owned documents. Missing, stale, cross-scope, or coordinate-mismatched expectations make the suite invalid rather than counting as retrieval misses.

## Suite contract

A suite is a JSON object with:

- `principal_id`: the principal whose owned library is being evaluated.
- `minimum_case_recall`: explicit minimum recall required for every case, from `0.0` to `1.0`.
- `minimum_macro_recall`: explicit minimum average recall required across the suite, from `0.0` to `1.0`.
- `cases`: one to 200 representative research cases.

Each case contains:

- `case_id`: stable human-maintained identifier.
- `query`: the exact research query to send through canonical lexical retrieval.
- `library_entry_ids`: one to 20 selected owned library entries.
- `expected`: one to 50 canonical relevant passage targets.
- `limit`: retrieval depth, default `20`, maximum `100`.

Each expected target contains the exact canonical `document_id`, `section_id`, `chunk_id`, `char_start`, and `char_end`.

## Metrics

The evaluator reports per-case recall, first relevant rank, reciprocal rank, missed chunk IDs, and the retrieved canonical coordinates. It also reports micro recall, macro recall, and mean reciprocal rank for the full suite.

A suite passes only when every case meets `minimum_case_recall` and the suite meets `minimum_macro_recall`. The CLI exits `0` on pass, `1` on measured recall failure, and `2` when the suite itself is invalid.

Run an authored suite with:

`bukmatika-research-recall-eval path/to/suite.json`

## Representative public-domain baseline

The first representative burn uses four real public-domain works sourced from Project Gutenberg and passes them through Bukmatika's built-in TXT parser, canonical chunker, principal-owned catalog records, and production PostgreSQL retrieval path:

- Thomas Paine, *Common Sense* (1776), Project Gutenberg #147.
- Frederick Douglass, *Narrative of the Life of Frederick Douglass, an American Slave* (1845), Project Gutenberg #23.
- Mary Wollstonecraft, *A Vindication of the Rights of Woman* (1792), Project Gutenberg #3420.
- W. E. B. Du Bois, *The Souls of Black Folk* (1903), Project Gutenberg #408.

The suite contains 12 research queries. Eight are lexical-native queries whose relevant wording is present in the source text. Four are natural paraphrases whose concepts are present but whose wording does not satisfy the current `websearch_to_tsquery('simple', ...)` match contract.

Quality #330 on exact candidate head `ed551aee90d82e9975da6695ba6386c3817fb51d` established the baseline:

- lexical-native recall: **8/8**, with every expected passage at rank 1;
- natural-paraphrase recall: **0/4**;
- micro recall: **8/12 (0.6667)**;
- macro recall: **8/12 (0.6667)**;
- mean reciprocal rank: **8/12 (0.6667)**;
- full API suite: **326 passed**;
- API Ruff, strict MyPy, Alembic migration chain, web typecheck, and production web build: **green**.

This is measured evidence of a meaningful paraphrase-recall gap. It is not evidence that chunk identity, ownership fencing, source provenance, or lexical ranking are broken, and it does not by itself justify a vector database.

The next retrieval slice must first inspect improvements that keep PostgreSQL and the canonical research repository authoritative, especially query normalization/construction and ranked lexical fallback behavior. Any improvement must rerun this same corpus so gains and regressions are visible rather than anecdotal.

## Semantic retrieval gate

A failed suite is evidence of a lexical retrieval gap, not automatic permission to add a vector database. First inspect missed queries for fixable normalization, query construction, ranking, chunking, or metadata problems inside the existing PostgreSQL path.

Only if a representative suite still demonstrates a material recall gap after those fixes should Bukmatika design one canonical embedding-provider interface. Any semantic implementation must preserve principal ownership, exact source provenance, the existing research API authority, and AI-disabled core behavior. Embedding persistence or a vector index requires its own measured justification and must not become bibliographic or research truth.
