# Research Retrieval Evaluation

Bukmatika does not add semantic retrieval because an architecture checklist says it should. PostgreSQL full-text search remains the canonical default retrieval path, and semantic retrieval must earn its place through measured recall on representative research tasks.

## Authority boundary

The evaluator is read-only. Its lexical suite calls the same principal-scoped `ResearchRepository.search_owned_passages()` path used by ordinary product research. It does not copy book text, persist embeddings, create vector or graph records, or call a model.

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

## Representative public-domain corpus

The representative burn uses four real public-domain works sourced from Project Gutenberg and passes them through Bukmatika's built-in TXT parser, canonical chunker, principal-owned catalog records, and production PostgreSQL retrieval path:

- Thomas Paine, *Common Sense* (1776), Project Gutenberg #147.
- Frederick Douglass, *Narrative of the Life of Frederick Douglass, an American Slave* (1845), Project Gutenberg #23.
- Mary Wollstonecraft, *A Vindication of the Rights of Woman* (1792), Project Gutenberg #3420.
- W. E. B. Du Bois, *The Souls of Black Folk* (1903), Project Gutenberg #408.

The suite contains 12 research queries. Eight are lexical-native queries whose relevant wording is present in the source text. Four are natural paraphrases whose concepts are present but whose wording stresses lexical retrieval.

## Baseline and PostgreSQL convergence

Quality #330 on exact candidate `ed551aee90d82e9975da6695ba6386c3817fb51d` established the original baseline:

- lexical-native recall: **8/8**, with every expected passage at rank 1;
- natural-paraphrase recall: **0/4**;
- micro recall: **8/12 (0.6667)**;
- macro recall: **8/12 (0.6667)**;
- mean reciprocal rank: **8/12 (0.6667)**.

PR #62 then improved the existing PostgreSQL path rather than adding semantic infrastructure. The production path remains strict-first: it uses the original `websearch_to_tsquery('simple', ...)` result whenever that produces matches and only applies a bounded lexical OR fallback when a plain natural-language query returns zero results. Explicit web-search syntax is never broadened, and short high-frequency terms are discarded when enough discriminative terms are available.

Quality #346 on exact candidate `75bcd69cfb1c677ee17a9ef397dd0fe8aac6804d` and post-merge Quality #347 on `main@332452f4c1ff3c630246b29dbe340dec579fe309` established the current PostgreSQL-only result:

- lexical-native recall: **8/8**, all rank 1;
- natural-paraphrase recall: **3/4**, all recovered passages rank 1;
- micro recall: **11/12 (0.9167)**;
- macro recall: **11/12 (0.9167)**;
- mean reciprocal rank: **11/12 (0.9167)**.

The remaining miss is the Paine paraphrase:

`state authority exists because people are morally imperfect`

Its expected canonical passage contains the concept in materially different wording, including `government by our wickedness`, and has no useful lexical overlap with the query after noisy short terms are removed.

An adversarial burn briefly appeared to produce 12/12 by allowing the generic token `are` to connect the query to the target passage. That result was rejected as false confidence. The benchmark intentionally preserves the Paine miss rather than gaming lexical recall.

## Optional semantic retrieval boundary

The measured 11/12 result is sufficient evidence to evaluate semantic retrieval, but it is not justification for a vector database, graph store, persistent embedding index, second catalog, or hidden model dependency in ordinary search.

The semantic slice therefore follows these rules:

1. `/v1/research/search` remains PostgreSQL-only and continues to work when AI is disabled.
2. semantic retrieval is exposed as a separate explicit `/v1/research/semantic-search` request.
3. embeddings use one canonical `EmbeddingGateway` sibling to the generation `ModelGateway`; embedding models are configured independently from chat/generation models.
4. the initial provider is loopback-only Ollama using the current batch `/api/embed` contract.
5. embeddings exist only for the duration of one request and are never written to PostgreSQL, a vector database, files, a graph, user memory, or personalization state.
6. candidate passages are reconstructed from canonical principal-owned `DocumentChunk` records with exact Work/Edition/Asset/Document/section/locator/character provenance.
7. the full selected candidate set must fit an explicit chunk ceiling; oversized selections fail visibly before any embedding request instead of being silently truncated.
8. AI-disabled principals and explicit local-model disablement fail before provider readiness or embedding calls.
9. provider/model identity must remain stable throughout one request, embedding dimensions must agree, and non-finite or zero-magnitude vectors fail closed.
10. ranking is deterministic after cosine similarity, with canonical IDs and chunk ordinals used only as stable tie breakers.

## Semantic acceptance gate

Unit tests with a deterministic probe embedding gateway may prove routing, privacy fencing, candidate bounds, canonical provenance, batching, ranking determinism, and failure behavior. They do **not** prove real semantic quality.

Semantic retrieval is not considered recall-complete until a real configured embedding model is burned against the same representative corpus and demonstrates all of the following:

- the Paine no-overlap paraphrase is recovered at an acceptable rank;
- the other 11 cases do not regress materially;
- every returned passage still resolves to exact canonical source coordinates;
- cross-principal selections fail before any private text reaches the provider;
- AI-disabled operation performs zero embedding-runtime calls;
- request-local resource ceilings hold on realistic multi-book selections;
- exact-head API/web quality gates are green.

Only after that measurement may the project decide whether request-local semantic scoring is useful enough to expose in the consumer UI. Persistent embeddings or a vector index remain a separate future decision that requires its own latency, corpus-size, restart, storage, invalidation, privacy, and recall evidence.
