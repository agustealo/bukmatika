# Request-local semantic retrieval

Bukmatika's semantic retrieval path is optional and explicit. It exists to address measured natural-language paraphrase gaps that remain after PostgreSQL-native retrieval improvements, without replacing PostgreSQL or creating a second research authority.

## Product boundary

Ordinary `POST /v1/research/search` remains PostgreSQL-only. It does not probe, call, or depend on an embedding runtime.

Optional semantic retrieval uses `POST /v1/research/semantic-search`. It is available only when the principal's AI controls allow model use and an embedding provider/model is explicitly configured and ready.

## Authority

PostgreSQL remains authoritative for:

- principal ownership;
- selected library entries;
- Work, Edition, Asset, and Document identity;
- canonical `DocumentSection` and `DocumentChunk` text;
- section locators;
- character coordinates;
- research-event history.

An embedding provider may rank canonical chunks for one request. It cannot create or modify catalog, document, rights, reader, annotation, personalization, or research truth.

## Runtime flow

```text
explicit semantic-search request
        |
        v
principal AI/model-use gate
        |
        v
selected-library ownership validation
        |
        v
count canonical selected chunks
        |
        +--> over configured ceiling: fail visibly, no provider call
        |
        v
embedding-provider readiness
        |
        v
embed query once
        |
        v
embed canonical chunk text in bounded batches
        |
        v
validate stable provider/model identity + vector integrity
        |
        v
request-local cosine ranking
        |
        v
return canonical passage coordinates
        |
        v
discard vectors
```

## Configuration

Embedding configuration is deliberately independent from generation/chat configuration:

- `BUKMATIKA_EMBEDDING_PROVIDER=none|ollama`
- `BUKMATIKA_OLLAMA_EMBEDDING_MODEL=<installed embedding model>`
- `BUKMATIKA_EMBEDDING_TIMEOUT_SECONDS=30`
- `BUKMATIKA_SEMANTIC_SEARCH_MAX_CHUNKS=256`
- `BUKMATIKA_SEMANTIC_SEARCH_BATCH_SIZE=24`

The Ollama provider is loopback-only and uses the batch `POST /api/embed` endpoint. Automatic model download is not allowed.

## Resource and privacy controls

- embedding requests are bounded by input count, per-item characters, total characters, dimensions, candidate count, batch size, and timeout;
- candidate overflow fails before any private chunk text reaches the provider;
- cross-principal or unowned selections fail before provider calls;
- AI-disabled or explicit local-model-disabled principals fail before provider readiness checks;
- provider/model identity must remain stable through one request;
- non-finite, inconsistent-dimension, zero-magnitude, wrong-count, or wrong-model vectors fail closed;
- environment proxies are disabled for provider traffic;
- vectors are never persisted or added to user memory/personalization;
- no vector database, graph store, background embedding worker, or embedding cache exists in this slice.

## Real-model recall burn

The operational semantic benchmark is separate from the normal GitHub Quality workflow. CI proves the benchmark machinery with a deterministic embedding gateway, while the real burn requires an explicitly configured local embedding model.

Run:

```text
BUKMATIKA_EMBEDDING_PROVIDER=ollama \
BUKMATIKA_OLLAMA_EMBEDDING_MODEL=<installed-embedding-model> \
bukmatika-semantic-recall-burn
```

The command:

1. requires the configured embedding model to be ready before seeding benchmark data;
2. opens one PostgreSQL transaction and creates a unique ephemeral benchmark principal;
3. seeds the same representative Paine, Douglass, Wollstonecraft, and Du Bois corpus used by the PostgreSQL recall gate;
4. measures canonical PostgreSQL lexical retrieval and production request-local semantic retrieval against the same 12 exact targets;
5. reports provider/model/routing identity, per-case recall and rank, micro/macro recall, MRR, recovered cases, recall regressions, and rank regressions;
6. rolls the transaction back so benchmark principals, library rows, documents, events, and user-model state do not survive the run.

Exit status is `0` only when the semantic acceptance gate passes, `1` for a measured quality failure, and `2` for an invalid or unavailable operational setup.

The current acceptance contract requires:

- semantic macro recall of `1.0` across all 12 cases;
- semantic MRR of at least the PostgreSQL baseline `11/12`;
- no case losing recall relative to PostgreSQL;
- the Paine no-overlap paraphrase recovered within rank 5;
- any rank deterioration surfaced explicitly even when aggregate recall improves.

The burn does not download models and does not persist embeddings.

## Acceptance

The deterministic test gateway proves privacy, ownership, budget, routing, batching, ranking, provenance, benchmark comparison, and rollback contracts only. It is not evidence that a real embedding model solves the benchmark.

A real configured local embedding model must pass `bukmatika-semantic-recall-burn` before consumer semantic-search UX is considered complete. The remaining Paine no-overlap case must be recovered without materially regressing the other benchmark cases or losing canonical source coordinates.
