# Provider-Neutral AI Consumer Release Gate v2

Status: **HARD RELEASE BLOCKER**

Tracking issue: #174

## Purpose

Bukmatika must not treat Ollama, any cloud vendor, or any single model family as the shape of the AI subsystem. Providers are replaceable adapters behind Bukmatika-owned contracts. Consumer AI release is blocked until provider neutrality, user control, privacy routing, credential safety, failure behavior, cost behavior, and real runtime evidence are proven.

Passing an Ollama proof, adding a provider dropdown, or implementing a second adapter does not satisfy this gate by itself.

## 1. Canonical ownership

- Core product/research/orchestration code depends only on Bukmatika model capabilities/contracts, never provider-specific APIs, SDK objects, response shapes, model names, error codes, or endpoint semantics.
- A single provider registry owns provider discovery, capability metadata, construction, readiness, and model inventory.
- Provider adapters may not become alternate orchestration authorities.
- Local and cloud providers are first-class peers behind the same runtime boundary.
- Initial provider families must include a local adapter (Ollama) and cloud adapter contracts suitable for OpenAI, Google Gemini, Anthropic, and Z.AI.
- Adding another conforming provider must not require changing core research, planning, grounding, citation, personalization, or autonomy logic.

## 2. Provider and model identity

- Provider identity and model identity are distinct durable concepts.
- Runtime identity records provider, model, routing type, and provider-reported/model-version identity when available.
- Aliases such as `latest` must not silently destroy auditability. The effective model/version returned by the provider must be recorded when available.
- Model disappearance, renaming, deprecation, access revocation, and capability changes must fail visibly rather than silently switching semantics.

## 3. Capability-driven routing

- Provider/model capability metadata must cover at minimum structured output, text generation, streaming, embeddings, tool calling, vision/multimodal support, context limits, output limits, and routing type where supported.
- Core code asks for a capability; adapters translate that requirement to provider-specific APIs.
- Generation and embeddings are independently routable. No provider may be assumed to supply both.
- Unsupported capability requests fail closed before transmitting user content.
- Capability metadata must be refreshable/versionable so provider changes do not leave stale assumptions permanently embedded in user profiles.

## 4. Multi-provider user configuration

- Users can configure multiple providers concurrently rather than selecting one global provider.
- Users can assign models by role/capability, including at minimum primary/research, fast, reasoning, embeddings, and fallback where supported.
- Explicit user selection wins over automatic routing.
- Automatic routing may operate only inside providers/models the user explicitly enabled and the applicable privacy/budget policy permits.
- Provider order, fallback order, and role assignments are inspectable and editable.
- Removing a provider must not strand an opaque hidden dependency.

## 5. No silent egress or fallback

- Bukmatika must never move a request from local to cloud, or from one cloud provider to another, merely because the preferred provider is unavailable.
- Cross-provider fallback requires an explicit user-approved policy that identifies eligible providers and applicable data classes.
- A local-only request must remain local or fail.
- A provider outage, rate limit, quota exhaustion, model removal, policy denial, or invalid response must produce an actionable failure unless an already-approved fallback is eligible.
- Retry logic must not duplicate chargeable requests beyond defined retry/idempotency policy.

## 6. Privacy, data classification, and cloud egress

Before any cloud request leaves Bukmatika:

- it passes the canonical context-minimization/privacy/policy authority;
- the provider and model are authorized for the request's data classification;
- the user has enabled that provider for that class of content;
- the exact minimized payload is the only payload available to the adapter;
- provider adapters cannot fetch additional user/library context themselves.

Required user-controllable policy includes at minimum:

- AI disabled;
- local-only;
- cloud allowed for public/evidence data only;
- cloud allowed for private library/research context where explicitly enabled.

The UI must make clear when content can leave the device/installation. Provider discovery/readiness/model listing must not transmit book text, research questions, annotations, reading history, personalization context, or credentials to unrelated providers.

## 7. Third-party data handling disclosure

- Each cloud provider connection exposes its routing type and that data is being sent to an external service.
- Bukmatika distinguishes its own privacy guarantees from a provider's retention/training/account terms.
- Provider-specific data handling constraints can be surfaced without baking vendor policy text into core orchestration.
- Disconnecting a provider removes Bukmatika's usable credential/reference and disables future egress through that connection.
- Bukmatika must not claim that deleting local data erases copies already transmitted to an external provider unless that erasure is actually supported and performed.

## 8. Credential and connection security

- Cloud credentials are never returned to normal client/UI reads after submission.
- Secrets are not stored in browser state, logs, model activity events, analytics, exported libraries, backups unless explicitly designed and encrypted for that purpose, or `.bukmatika` portability bundles.
- Credential storage has a single canonical owner with least-privilege access.
- API errors and diagnostics must redact authentication material and sensitive headers.
- Provider connections support validation, rotation/replacement, revocation/disconnect, and an explicit invalid-credential state.
- Custom provider endpoints, if ever supported, require a separate SSRF/redirect/network-boundary design. Arbitrary base URLs must not slip in through a generic provider configuration field.

## 9. Provider transport security

- Cloud adapters enforce HTTPS except explicitly local providers with separately constrained loopback/local transport.
- Redirect behavior is explicit and safe; credentials must not be forwarded to an untrusted redirect target.
- Proxy/environment inheritance must not silently reroute private model traffic unless explicitly supported by policy.
- Timeouts, request-size bounds, output-size bounds, and cancellation behavior are provider-neutral runtime concerns with adapter-specific enforcement.

## 10. Cost, quota, and rate-limit behavior

- Cloud usage must expose enough normalized metadata for consumer-visible provider/model activity and troubleshooting.
- Where providers return token/usage data, Bukmatika records normalized input/output usage without recording hidden prompts or secret material.
- If cost estimates are surfaced, they must be labeled estimates and tied to provider/model pricing metadata with an as-of/version boundary.
- Rate limits, quota exhaustion, and billing/account-disabled responses have distinct normalized failure states.
- Automatic retry/fallback must respect user-defined budget and provider policy.
- No background/autonomous pathway may create unbounded paid model usage.

## 11. Response normalization and grounding integrity

- Every adapter normalizes provider output into Bukmatika-owned response contracts before core code consumes it.
- Structured-output parsing, schema validation, truncation detection, refusal/blocked-response handling, and malformed-output behavior are consistent across providers.
- Provider-native citations or tool traces never replace Bukmatika's canonical evidence/citation authority.
- A model/provider may synthesize from canonical evidence but may not mint trusted Bukmatika evidence identifiers.
- Grounding and fabricated-ID rejection remain provider-independent release invariants.

## 12. Streaming and cancellation

- If streaming is supported, streaming is a canonical runtime capability rather than provider-specific UI plumbing.
- Cancellation/disconnect propagates to the provider when supported and prevents continued hidden work where practicable.
- Partial streamed output must not be treated as a successfully completed grounded answer unless final validation succeeds.
- Usage/audit state distinguishes started, completed, cancelled, failed, and fallback attempts.

## 13. Embedding-specific safety

- Embedding providers/models are independently configured and identified.
- Semantic-search evidence records the embedding provider/model identity used for the evaluation/proof.
- Dimension/model incompatibility fails closed rather than mixing incomparable vectors.
- A change of embedding model during one request/evaluation cannot create a mixed vector space.
- Semantic quality promotion is model-specific evidence; passing model A does not automatically promote model B.

## 14. UX gate

The consumer UI must surface:

- configured provider connections without exposing secret values;
- local vs cloud routing;
- provider readiness and credential validity;
- available models and capabilities;
- role assignments;
- fallback policy;
- AI-disabled and local-only states;
- which classes of user data each configured cloud provider may receive;
- rate-limit/quota/account errors with actionable recovery;
- the effective provider/model used for completed AI activity.

Ollama-specific UI may exist inside the Ollama provider surface, not as the canonical AI settings model. Provider setup must not require editing environment variables for ordinary consumer use.

## 15. Non-AI product independence

Core library, catalog, reader, imports, source provenance, lexical search, annotations, organization, portability, and other non-AI product features remain usable when:

- AI is disabled;
- no provider is configured;
- all cloud providers are disconnected;
- the local provider is offline.

Provider initialization failure must not prevent Bukmatika itself from starting unless the operator explicitly configured a deployment policy that requires AI readiness.

## 16. Observability and audit

For each model attempt, Bukmatika can identify at minimum:

- task/capability;
- provider;
- effective model/version identity where available;
- local/cloud routing;
- outcome state;
- latency;
- normalized usage where available;
- whether fallback occurred and why;
- policy decision that authorized external egress.

Observability must not log API keys, authorization headers, full private prompts, raw private evidence, or unnecessary user context.

## 17. Runtime-evidence gate

Existing proofs become provider-neutral contracts:

```bash
bukmatika-grounded-ai-proof --provider <provider> --model <model>
```

```bash
bukmatika-semantic-recall-burn --provider <provider> --model <model>
```

Acceptance belongs to Bukmatika, not to Ollama.

Before consumer AI readiness is green:

1. grounded-generation proof passes against at least one real local or cloud configured provider;
2. the same canonical grounded path is exercised against a second independently implemented provider adapter to prove interchangeability;
3. semantic-retrieval quality proof passes against at least one real configured embedding provider/model;
4. provider/model identity and policy authorization are observable in the proof ledger;
5. proof tooling does not contain provider-specific bypass logic that avoids the production registry/runtime.

A mocked second provider is sufficient for contract unit tests, but not for the interchangeability release proof.

## 18. Adversarial provider burns

Required burns include:

- unknown provider;
- valid provider with invalid credential;
- provider unreachable;
- provider rate-limited;
- provider quota/billing disabled;
- configured model missing/deprecated;
- provider returns malformed structured output;
- provider returns an oversized/truncated response;
- provider returns a refusal/blocked response;
- primary provider fails while fallback is not approved;
- primary provider fails with an approved fallback;
- local-only request while only cloud providers are ready;
- private-context request to a provider authorized only for public data;
- cancellation during streaming;
- provider response attempts to fabricate evidence/citation IDs;
- credential/authorization-header leakage checks across logs, errors, activity ledger, exports, and backups.

## 19. Code acceptance

- Remove `Literal["none", "ollama"]` as the canonical provider contract.
- Remove `ModelConfigurationMode.OLLAMA` as the canonical user configuration model.
- Remove hard-coded Ollama inspection/routing from principal model configuration ownership.
- Remove canonical UI types that constrain routing to `"local"`.
- Introduce provider registry and provider/model capability metadata.
- Introduce canonical connection/credential ownership for cloud providers.
- Preserve fail-closed behavior for unknown, unconfigured, unavailable, invalid-credential, unsupported-capability, quota-limited, or policy-disallowed providers.
- Preserve context minimization, grounding, citation validity, model-call budgets, auditability, and bounded autonomy contracts.
- Add regression tests proving a second provider can be registered/selected without modifying core research/orchestration logic.
- Add tests proving AI-disabled operation and local-only policy continue to work.
- Add tests proving unapproved fallback never causes cloud egress.
- Add tests proving provider/model identity survives through user-visible status and activity evidence.

## 20. Current concrete coupling confirmed by audit

At `main@2d38bf99cc754a8caac8543d94fc96fbcb6d83b2` the gate is definitely unmet:

- `Settings.model_provider` and `Settings.embedding_provider` are `Literal["none", "ollama"]`.
- The factory rejects every provider except Ollama.
- Principal configuration exposes `ModelConfigurationMode.OLLAMA` and directly calls Ollama inventory.
- The web control center's canonical configuration union is `"installation_default" | "disabled" | "ollama"`, constrains routing to `"local"`, calls `/v1/ai/local/models`, and tells the user that only local Ollama models can be selected.
- The canonical gateway currently exposes readiness and structured generation only, so capability negotiation, streaming, cancellation, usage normalization, and general provider semantics still need an explicit canonical design.

## Release rule

Do not mark consumer AI readiness green while canonical configuration/runtime/UX is vendor-shaped, while provider credentials or fallback policy lack canonical ownership, or while real provider interchangeability has not been demonstrated.

Do not weaken this gate merely to preserve the previous release schedule. Provider flexibility is part of the consumer product contract.
