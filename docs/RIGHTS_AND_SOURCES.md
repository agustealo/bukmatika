# Rights and Source Policy

## Rule zero

Discovery is not permission.

A URL ending in `.pdf` or `.epub`, a search result containing the word “free,” or a publicly reachable file is not sufficient rights evidence.

## Rights states

| State | Meaning | Unattended acquisition |
| --- | --- | --- |
| `public_domain` | Reliable evidence indicates the edition/file is in the public domain for the deployment policy | yes |
| `open_license` | A recognized license permits the intended download/use | yes, within license terms |
| `authorized_download` | The rights holder/source explicitly authorizes download of the exact asset | yes, within authorization |
| `borrow_only` | Access is governed by a lending system | no |
| `preview_only` | Only preview access is authorized | no |
| `unknown` | Evidence is insufficient | no |
| `restricted` | Access/redistribution is restricted | no |

## Evidence hierarchy

Prefer, in order:

1. explicit structured rights/license metadata from the authoritative source for the exact edition/asset;
2. explicit rights statement on the item/edition landing page;
3. recognized open-license URI attached to the exact asset or edition;
4. source-specific rights field with documented semantics;
5. human review.

Do not determine copyright solely from publication year without jurisdiction-aware policy.

## Source adapter contract

Each adapter must define:

- canonical source name;
- documented API/feed endpoint;
- rate-limit policy;
- identification/User-Agent requirements;
- supported query fields;
- stable source record identifier;
- canonical landing URL;
- rights/access fields and their semantics;
- whether direct asset URLs are stable and allowed;
- attribution requirements;
- retry/backoff rules.

## Initial sources

### Open Library

Use documented APIs rather than scraping HTML. Cache aggressively and identify Bukmatika according to Open Library guidance. Open Library describes `ebook_access` as online readability for the work. Values such as `public` and `borrowable` are useful discovery/access signals, but Bukmatika does **not** convert `public` or `public_scan_b` into a copyright/public-domain determination. Exact-asset rights must be established later by stronger evidence.

### Internet Archive

Use documented metadata/search endpoints and item metadata. Acquisition must honor the access state and rights evidence of the exact item/file. Borrowing controls are not download authorization.

### Project Gutenberg

Do not crawl ordinary human-facing pages. Use Project Gutenberg’s permitted robot/catalog mechanisms for automated retrieval and keep canonical landing-page links for provenance.

### Library of Congress

Use the loc.gov JSON API for digitized collections. Rights statements can vary by item/collection and must be retained as evidence rather than flattened into a universal assumption.

### Standard Ebooks

Treat supported feeds as a provider integration. Feed access requirements can differ by project/account status; the adapter must follow the current supported access model rather than screen-scraping the site.

### General web discovery

A web-search adapter may discover candidate landing pages and files using topic terms plus format hints such as PDF/EPUB. It may also use domain filters for trusted institutions. It cannot mark a candidate downloadable solely because the search result exposes a file URL.

## Provider health

Every adapter exposes health telemetry:

- last successful request;
- latency;
- throttling state;
- recent error rate;
- schema/parse failures;
- last rights-semantics review date.

A provider whose response shape changes should fail visibly rather than silently emit malformed catalog records.
