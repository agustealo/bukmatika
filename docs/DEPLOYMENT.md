# Production deployment

Bukmatika ships one canonical self-hosted container deployment for the non-AI product path. It packages the existing PostgreSQL, API, OCR, and Next.js authorities behind a single same-origin ingress. It does not create a second application architecture and it does not weaken local-model routing to make containers convenient.

## Deployment shape

The production Compose stack contains:

- **PostgreSQL 18**, private to the Compose network with a durable named volume;
- a one-shot **migration** service that runs the canonical Alembic chain before the API is allowed to start;
- the **Bukmatika API**, including acquisition, delegation, privacy-erasure, and runtime-readiness authorities already owned by the API process;
- a dedicated **OCR worker** using the same API image, PostgreSQL state, Tesseract runtime, and durable book-storage volume;
- the production **Next.js web** application, built with relative API URLs;
- a small **Caddy ingress** that multiplexes `/v1/*`, `/health`, and `/ready` to the API and all other paths to the web application.

Only the ingress publishes a host port. PostgreSQL, API, Web, and OCR services are not exposed directly. The ingress binds to `127.0.0.1` and serves plain HTTP because TLS/HSTS remain an installation/infrastructure responsibility. Remote installations must terminate HTTPS in a trusted reverse proxy in front of the loopback ingress.

## Prerequisite

Install Docker with the Compose plugin. No host Python packages, Node packages, PostgreSQL server, Tesseract package, or application virtual environment are required for the production stack. Python 3 is required only to run the repository-owned deployment command.

## First start

From the repository root:

```text
python scripts/deploy.py init
python scripts/deploy.py check
python scripts/deploy.py up
```

`init` creates `.env.production` only when it is missing, generates a URL-safe PostgreSQL password with the operating-system CSPRNG, and sets mode `0600` where supported. It never overwrites an existing production environment file.

`check` validates the environment security contract and renders the exact production Compose configuration.

`up` builds the application images, starts PostgreSQL, runs migrations to `head`, starts the API/OCR/Web services, starts the same-origin ingress after API and Web health checks pass, and verifies both `/ready` and the web root through that ingress.

The default local deployment is available at:

```text
http://127.0.0.1:8080
```

## Operations

Use the repository command instead of ad-hoc Compose mutations:

```text
python scripts/deploy.py status
python scripts/deploy.py logs
python scripts/deploy.py logs --follow --tail 500
python scripts/deploy.py down
```

`down` intentionally does **not** pass `--volumes`. The PostgreSQL database and acquired/imported book storage therefore survive ordinary application shutdown and restart.

To update an installation, check out the exact reviewed Bukmatika revision you intend to deploy and run `python scripts/deploy.py up` again. The stack rebuilds the API/Web images and runs the canonical forward migration chain before the new API starts. Back up durable data before upgrades. Bukmatika does not pretend that arbitrary database downgrades are safe; application rollback must respect the migration compatibility of the target revision.

## Remote HTTPS deployment

Do not change `BUKMATIKA_BIND_ADDRESS`; the deployment command deliberately rejects public binds. Put an HTTPS reverse proxy or load balancer on the host/network edge and forward it to `http://127.0.0.1:8080`.

Then edit the generated `.env.production`:

```text
BUKMATIKA_PUBLIC_ORIGIN=https://books.example.com
BUKMATIKA_LOCAL_SESSION_SECURE_COOKIE=true
```

Run `python scripts/deploy.py check` after every environment change and then `python scripts/deploy.py up`.

The public origin must be an origin only, with no path/query/fragment. Remote `http://` origins are rejected. HTTPS deployments must use secure session cookies. HSTS and certificate lifecycle remain the responsibility of the external TLS terminator, matching the application security contract.

## Local AI boundary

The container deployment intentionally pins:

```text
BUKMATIKA_MODEL_PROVIDER=none
BUKMATIKA_EMBEDDING_PROVIDER=none
```

Bukmatika's Ollama generation and embedding adapters accept only `localhost` or loopback IP origins. A Docker host alias or bridge-network address is not loopback, and the production deploy command refuses attempts to enable Ollama through this stack. Core discovery, acquisition, catalog, library, reading, lexical research, personalization controls, and evidence workflows continue to operate without AI.

Supporting local models from a container would require a separately reviewed transport that preserves the existing local-only trust guarantee. Do not weaken the loopback validator or use unrestricted host networking as a deployment shortcut.

## Durable data

Two named volumes are authoritative:

- `bukmatika-prod_bukmatika-postgres-data` for PostgreSQL;
- `bukmatika-prod_bukmatika-storage` for acquired/imported source bytes and derived local storage.

Treat both as part of an installation backup. Product-level `.bukmatika` export/import and privacy export/erasure remain separate user-facing data controls and do not replace infrastructure backup.

The deployment CLI has no volume-destruction command. Deleting these volumes requires an explicit operator action outside the normal deployment workflow.

## Release proof

The GitHub **Deployment quality** job exercises the production path on every PR and `main` push. It generates a fresh production environment, validates Compose, builds both application images, starts the real PostgreSQL/migration/API/OCR/Web/ingress stack, waits for API readiness and the web root through the same-origin ingress, and tears the containers down without deleting volumes.

This gate proves packaging and first-boot wiring. It does not substitute for the separately tracked real installed-model proofs, semantic-model acceptance burn, branch protection, or broader market-readiness evaluation.
