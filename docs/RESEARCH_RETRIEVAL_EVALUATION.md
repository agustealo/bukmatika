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

## Semantic retrieval gate

A failed suite is evidence of a lexical retrieval gap, not automatic permission to add a vector database. First inspect missed queries for fixable normalization, query construction, ranking, chunking, or metadata problems inside the existing PostgreSQL path.

Only if a representative suite still demonstrates a material recall gap after those fixes should Bukmatika design one canonical embedding-provider interface. Any semantic implementation must preserve principal ownership, exact source provenance, the existing research API authority, and AI-disabled core behavior. Embedding persistence or a vector index requires its own measured justification and must not become bibliographic or research truth.
