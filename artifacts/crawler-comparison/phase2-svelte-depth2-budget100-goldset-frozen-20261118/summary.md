# Verified crawler URL gold set

Run directory: `artifacts\crawler-comparison\phase2-svelte-depth2-budget100-modern-20261118`
Started: `2026-11-18T02:42:51.333489+00:00`

The authoritative set combines same-host URLs from `robots.txt`/sitemap documents, the official `llms.txt` documentation index when available, and links on the configured seed page. Every unique source URL and every unique in-scope basecrawler candidate is checked with HEAD, falling back to a one-byte ranged GET when HEAD is rejected.

A URL is counted as a verified gold page/API record only when its response is 2xx/3xx and its content type is HTML, Markdown, XHTML, or plain text on a route without a file extension. A JSON response is counted as an API endpoint only when its path is under `/api/` or `/graphql`. API reference documentation pages are kept as documentation pages, not mislabeled as live API endpoints.

`gold_precision` is source-backed precision: verified source URLs that also appear in basecrawler candidates divided by route candidates after excluding static assets and structured-data files. `gold_recall` is the share of all verified source-backed gold pages/API records present in the candidate set. Sources can omit valid but unlisted URLs, so these metrics describe this explicit source-defined gold set, not every possible route on a host.

Verification timeout: 12s; maximum start rate: 2 requests/second per site; workers per site: 4.

Official navigation crawl depth: 2; page cap per sitemap-less host: 250; reused prior HTTP checks: 702 from `artifacts\crawler-comparison\phase2-llms-index-verified-goldset-20261118`.

Authoritative source snapshot reused: `artifacts\crawler-comparison\phase2-llms-index-verified-goldset-20261118`.

Unique URL status checks: 874 total, 172 new this pass, 702 reused.

| Site | Sitemap | Index | Seed links | Expanded nav URLs | Nav pages read | Cap reached | Gold pages | API docs | Live APIs | Route candidates | TP | HTTP yield | Precision | Recall |
|---|---:|---:|---:|---:|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|
| framework-sveltekit | 0 | 193 | 90 | 627 | 91 | no | 532 | 0 | 0 | 518 | 444 | 92.7% | 85.7% | 83.5% |

Source collection errors are listed in `metadata.json`. Full URL-level provenance and HTTP results are in `goldset.csv`; candidate checks are in `candidate-verification.csv`.
