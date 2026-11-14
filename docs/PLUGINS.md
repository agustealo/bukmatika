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
REST connection configuration, explicit enable/disable, connection-health checks, and
explicit uninstall. Settings are WordPress-owned. Application Passwords are submitted
on save, encrypted server-side, and never returned in configuration responses.

Installation remains disabled by default and saving a connection never activates a
plugin implicitly. Enabling a connection-backed plugin requires the current declared
connection schema/mode, complete non-secret settings, and every declared encrypted
secret slot. Activation is principal-scoped and row-locked with configuration/uninstall
state transitions. It only makes the plugin eligible to enter the canonical
`PluginExecutionPolicy`; it does not authorize any content mutation.

Three separate checks are available:

1. Public REST reachability: no credential is transmitted.
2. Application Password identity: an authenticated WordPress identity response.
3. User capabilities: an authenticated, request-local read of explicit capability
   booleans, which can differ among WordPress accounts.

A successful check does not grant publishing authority. Checks are read-only, run
through restricted pinned-IP HTTPS, are principal-scoped, and have a 60-second
per-check cooldown. WordPress capability checks persist their exact observed booleans
in a source-owned snapshot so the consumer can restore the last observation after
reload without performing another network request. The snapshot and its health receipt
share one timestamp and evidence code. Saving or rotating a connection invalidates both
the generic probe receipts and all prior capability snapshots while holding the same
connection row lock used by authenticated probes.

Persisted capability snapshots are evidence only. They are never a write grant and
must not substitute for live capability/policy revalidation when a future mutation is
attempted.

The publishing domain now owns provider-neutral publication identity, immutable
revisions, stable destinations, idempotent delivery intents, and durable attempt state.
A destination may reference the live WordPress connection, but uninstalling the plugin
or deleting credentials must not erase historical delivery provenance. WordPress write
policy composes the canonical `PluginExecutionPolicy` with a fresh authenticated
capability observation while installation/connection locks are held. Draft creation
requires live `edit_posts`; immediate publication additionally requires live
`publish_posts`. No persisted capability snapshot is accepted as write authority.

**Still disabled:** WordPress publishing mutations, direct MySQL/MariaDB access, SSH
tunnels, and consumer release packaging. Connection-backed plugin activation is now
available, but every executable capability still passes the canonical plugin execution
policy and any source/action-specific live policy required for that operation. Publishing
still requires fresh write-capability enforcement, durable publication/delivery receipts,
retry safety, and end-to-end WordPress acceptance. The plugin architecture remains
tracked by issue #184.

The application must not claim WordPress consumer publishing readiness until
these remaining gates are satisfied.
