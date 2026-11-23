# Local framework crawl fixtures

These small Nuxt, React, Vue SPA, Next.js App Router, Angular, and SvelteKit sites reproduce crawl-relevant framework behavior without repeatedly requesting public sites. They include route links, API forms, nested click-only menus, scroll-triggered endpoints, pagination, and source-map-enabled build output for browserless and browser-driven comparison. A separate dependency-free interaction fixture checks open shadow roots, same-origin iframe controls, and actions revealed by nested scrolling.

The Nuxt fixture also replays the 835 frozen candidate path strings removed by the 2026-11-19 call-evidence filter as arguments to a local label-reading helper. The regression report checks that these strings remain clues or are ignored and do not produce HTTP requests. The original Nuxt bundle bodies were not present in the frozen artifacts, so this validates the candidate values against a controlled helper-call shape rather than claiming source-line identity with the public bundle.

Run the browserless regression from the repository root:

```powershell
python tools/run_local_site_regression.py
```

The runner installs locked npm dependencies when needed, builds each selected site, starts it on loopback-only ports, builds Basecrawler from the current checkout, crawls each site, records timing and responses under `artifacts/crawler-comparison/`, then stops the local servers. Angular and SvelteKit use the shared local static/API fixture server; the interaction fixture serves its source files directly and needs no package install or build. The unified matrix runner also compares frozen Katana baselines and Browcrawler browser interactions.

The accepted route set is kept in the runner. The negative strings in the apps are expected to remain unscheduled. Updating a fixture requires updating that route set and the acceptance checks together.

Framework versions are pinned in package manifests and lockfiles. No crawl traffic is sent to public reference sites.

The Next.js fixture explicitly enables production browser source maps so source-map parsing is covered in the local matrix; this setting is limited to this loopback test fixture.

Run the independent Next.js scenario matrix (App/Pages SSR, dynamic route, client effect,
lazy import, response chain and SVG identity):

```powershell
python tools/run_next_scenario_matrix.py
```

This runner uses the existing locked dependencies, builds the real production fixture and
Basecrawler, and scans independent seeds without invoking Katana. It freezes expected
method/URL/query contracts before scanning and records per-case duration, successful
requests, discovery provenance, negative controls, extra API requests and duplicate
response counts in JSON. Use a new output directory for every run. Exit 1 preserves
failed scenarios; it does not mean the runner failed to execute.

An endpoint found by AST or JSON extraction does not prove that the client effect,
dynamic import, response continuation or SVG guard executed. Shared-component branches
deliberately expose this distinction. Extra APIs are unclassified for that seed, not
automatically invalid across the entire site. The fixtures are an application contract,
not yet a browser-recorded oracle or a new frozen Katana comparison.
