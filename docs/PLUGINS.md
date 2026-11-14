# Bukmatika Plugin Architecture

Bukmatika integrations are plugins, not hard-coded source branches.

## Ownership rule

Bukmatika owns plugin lifecycle, identity, permissions, secret isolation, audit, and
execution boundaries. A plugin owns the semantics of the source it integrates with.

That means connection settings are source-specific. Bukmatika must not collapse
unrelated systems into one universal credential/configuration form.

## Capability model

A plugin declares immutable identity and capabilities through `PluginManifest`.
Capability-specific factories and protocols provide executable behavior. Core code
asks for a capability instead of branching on a vendor/source name.

Initial capability families include:

- discovery search
- asset acquisition
- content read
- content publish
- AI generation
- AI embeddings
- export

One plugin may implement multiple capabilities.

## Connection ownership

Connection schemas belong to plugins.

The shared runtime may render safe declarative fields and provide common secret
storage, but it must not invent or interpret source-specific settings.

Examples:

- **Project Gutenberg** currently requires no user connection.
- **Ollama** owns local runtime/model configuration.
- **OpenAI** will own its API credential/model settings.
- **WordPress** owns WordPress connection modes, site/database settings, and publishing
  features.

### WordPress reference contract

A WordPress plugin may support:

- WordPress REST API with Application Password credentials
- local MySQL/MariaDB
- remote MySQL/MariaDB
- SSH tunneling only if a separate network/security design authorizes it

WordPress-specific configuration may include site URL, host, port, database name,
table prefix, username, and opaque credential references.

WordPress-specific capabilities may include reading and creating/updating posts,
categories, tags, excerpts/short descriptions, media/featured images, post status,
post types, and taxonomies.

Bukmatika core must not contain WordPress REST routes, table names, taxonomy rules,
or connection fields.

## Lifecycle

The target durable lifecycle is:

```text
available
  -> installed
  -> configured
  -> validated
  -> enabled
  -> disabled
  -> upgraded
  -> uninstalled
```

Built-in plugins participate in the same lifecycle even when their executable code
ships with Bukmatika.

Uninstall must destroy plugin credentials and disable plugin-owned schedules/actions,
while preserving canonical imported library/content data and required historical
provenance/audit records.

## Security boundaries

- no user-supplied Python/module path execution
- no arbitrary code download during install
- executable plugins come only from trusted/approved packages
- plugin ID and version are immutable execution identity
- credentials stay behind Bukmatika's encrypted credential store
- a plugin cannot read another plugin's credentials
- outbound hosts and redirect policy are capability/plugin specific
- SQL-capable plugins never receive Bukmatika's database credentials
- remote database integrations require explicit SSRF/private-network review

## Current implementation and consumer boundary

The bundled discovery sources use the canonical registry. The API implements
principal-scoped installation and uninstall, provider-specific connection storage,
and the canonical encrypted credential vault. A WordPress REST Application Password
may be configured only for a validated public HTTPS origin.

The consumer **Plugins** page at `/plugins` provides WordPress installation,
REST connection configuration, connection-health checks, and explicit uninstall.
Settings are WordPress-owned. Application Passwords are submitted on save, encrypted
server-side, and never returned in configuration responses.

Three separate checks are available:

1. Public REST reachability: no credential is transmitted.
2. Application Password identity: an authenticated WordPress identity response.
3. User capabilities: an authenticated, request-local read of explicit capability
   booleans, which can differ among WordPress accounts.

A successful check does not grant publishing authority. Checks are read-only, run
through restricted pinned-IP HTTPS, are principal-scoped, and have a 60-second
per-check cooldown. Saving or rotating a connection invalidates old observations.
Capability booleans are intentionally not reconstructed from stored health codes.

WordPress REST connections with a stored Application Password can now be explicitly
enabled for authenticated **read-only** `GET /v1/plugins/wordpress/posts?page=1`
browsing. Each bounded request checks remote `edit_posts` afresh and retrieves at
most 20 post summaries over pinned HTTPS. This does not authorize publishing.
Disabling the plugin revokes reads; saving or rotating credentials disables the
plugin, requiring reactivation.

**Still disabled:** WordPress create/update/delete/publish mutations,
direct MySQL/MariaDB access, SSH tunnels, and consumer release packaging.
These require explicit write policy, end-to-end acceptance, connection lifecycle and
network/security proofs. The plugin architecture remains tracked by issue #184.

The application must not claim WordPress consumer publishing readiness until
these remaining gates are satisfied.
