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

## Current migration state

The first plugin-runtime slice moves the four bundled discovery sources behind the
plugin registry while preserving existing discovery ranking and source behavior.

Durable installation/configuration/uninstall state is a follow-on slice. Until that
lands, the bundled registry is executable plugin identity, not a claim that the full
consumer plugin lifecycle is complete.

Tracking issue: #184.
