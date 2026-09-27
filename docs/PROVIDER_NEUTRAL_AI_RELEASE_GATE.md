# Provider-Neutral AI Consumer Release Gate

Status: **HARD RELEASE BLOCKER**

Tracking issue: #174

## Purpose

Bukmatika must not make Ollama, or any other single model provider, part of the shape of the product core. AI providers are replaceable infrastructure behind a canonical Bukmatika runtime contract. Local and cloud providers must be peers, users must be able to configure multiple providers/models, and AI-backed features must remain optional to the rest of the product.

This gate blocks consumer AI release until the criteria below are proven.

## Canonical ownership

The Bukmatika core owns product capabilities, orchestration, grounding, citation contracts, privacy policy, context minimization, budgets, auditability, and bounded autonomy.

The AI runtime owns provider/model discovery, selection, readiness, invocation, failover, provider identity, and capability metadata.

Provider adapters own vendor-specific endpoints, authentication, request/response translation, model enumeration, provider error mapping, and transport details.

Core product code must never depend directly on vendor-specific APIs, endpoint shapes, provider SDK objects, or vendor model naming conventions.

## Provider registry contract

A single provider registry must be the canonical construction and discovery authority. The registry must support local and cloud adapters as first-class peers and permit new providers to be added without modifying research, reader, library, personalization, or orchestration business logic.

Initial provider families must include:

- Ollama as a local adapter.
- OpenAI-compatible cloud generation support.
- Google Gemini cloud generation support.
- Anthropic cloud generation support.
- Z.AI cloud generation support.

The implementation may share transport helpers where protocols overlap, but providers remain explicit identities for readiness, policy, telemetry, and user configuration.

## Capability contract

Provider/model selection is capability-driven rather than provider-driven. The runtime must represent, at minimum, whether a model supports:

- text generation,
- structured generation,
- streaming,
- tool/function calling,
- embeddings,
- vision or multimodal input where applicable,
- context-window metadata where known.

Generation and embeddings are independently routable. No provider is assumed to supply both.

## User configuration

Users must be able to configure multiple providers concurrently and select models according to their needs rather than according to what Bukmatika assumes is installed.

The canonical configuration model must support explicit role assignments including, where supported:

- primary/research,
- fast,
- reasoning,
- embeddings,
- fallback.

Explicit user selection wins over automatic routing. Automatic selection is allowed only inside the set of providers/models the user has configured and policy has approved.

The user must be able to disable AI entirely and must be able to enforce local-only operation.

## Privacy and egress policy

Every cloud-provider request must pass the canonical Bukmatika context-minimization and privacy/policy boundary before external egress.

Provider adapters must not bypass this authority. The runtime must preserve provider/model identity in audit/activity records while avoiding credential leakage.

Core library, catalog, reader, imports, provenance, lexical search, annotations, organization, and other non-AI product functionality must continue to operate when AI is disabled and when no external provider is configured.

## UX gate

Consumer settings must surface:

- all configured providers,
- local versus cloud routing,
- provider readiness,
- available/configured models,
- role assignments,
- disabled and local-only modes,
- actionable configuration errors,
- which provider/model is active for a role.

Ollama-specific controls may exist within the Ollama adapter experience, but the canonical settings surface must not be Ollama-shaped.

## Runtime evidence

Bukmatika acceptance contracts must be provider-neutral.

The grounded-generation proof must accept an explicit provider/model selection, conceptually:

```bash
bukmatika-grounded-ai-proof --provider <provider> --model <model>
```

The semantic-retrieval proof must accept an explicit embedding provider/model selection, conceptually:

```bash
bukmatika-semantic-recall-burn --provider <provider> --model <model>
```

The exact CLI may evolve, but acceptance belongs to Bukmatika rather than to any vendor.

Before consumer AI release, a release candidate must demonstrate:

1. the grounded-generation contract against at least one real configured generation provider/model;
2. the semantic-retrieval quality contract against at least one real configured embedding provider/model;
3. provider/model identity in the activity ledger;
4. fail-closed behavior for unknown, unavailable, unconfigured, or policy-disallowed providers.

An Ollama-only proof is insufficient to close this gate.

## Code acceptance

The gate remains open while any of the following remain canonical behavior:

- `Literal["none", "ollama"]` as the provider contract;
- `ModelConfigurationMode.OLLAMA` as the canonical user configuration model;
- hard-coded Ollama inventory or routing inside principal model-configuration ownership;
- product/business logic branching directly on provider names.

Required implementation outcomes:

- provider registry and adapter contract;
- provider/model capability metadata;
- provider-neutral user configuration and role assignment;
- independently routable generation and embeddings;
- secure credential/connection ownership for cloud providers;
- privacy/egress enforcement before cloud calls;
- provider-neutral runtime proof commands/contracts;
- regression coverage proving a second provider can be registered and selected without changing core research/orchestration logic;
- regression coverage proving AI-disabled and local-only operation continue to work;
- preservation of grounding, citation validity, model-call budgets, observability, auditability, and autonomy restrictions.

## Release rule

Do not mark consumer AI readiness green, promote AI-backed features as provider-flexible, or close the AI release boundary while this gate is open.

Ollama remains supported, but only as one adapter among interchangeable local and cloud providers.
