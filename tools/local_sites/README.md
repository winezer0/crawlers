# Local Nuxt and React crawl fixtures

These two small sites reproduce crawl-relevant framework behavior without repeatedly requesting the public Nuxt and React sites. They intentionally include server-rendered navigation, API references, lazy chunks, source maps, and non-URL strings that resemble request candidates.

The Nuxt fixture also replays the 835 frozen candidate path strings removed by the 2026-11-19 call-evidence filter as arguments to a local label-reading helper. The regression report checks that these strings remain clues or are ignored and do not produce HTTP requests. The original Nuxt bundle bodies were not present in the frozen artifacts, so this validates the candidate values against a controlled helper-call shape rather than claiming source-line identity with the public bundle.

Run the browserless regression from the repository root:

```powershell
python tools/run_local_site_regression.py
```

The runner installs locked npm dependencies when needed, builds both sites, starts them on loopback-only ephemeral ports, builds Basecrawler from the current checkout, crawls each site, records per-site timing and responses under `artifacts/crawler-comparison/`, then stops the local servers.

The accepted route set is kept in the runner. The negative strings in the apps are expected to remain unscheduled. Updating a fixture requires updating that route set and the acceptance checks together.

Nuxt and Vite/React versions are pinned in their package manifests and lockfiles. No crawl traffic is sent to the public reference sites.
