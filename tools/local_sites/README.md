# Local framework crawl fixtures

These small Nuxt, React, Vue SPA, and Next.js App Router sites reproduce crawl-relevant framework behavior without repeatedly requesting public sites. They include route links, API forms, click-only runtime endpoints, and build output suitable for browserless and browser-driven comparison.

The Nuxt fixture also replays the 835 frozen candidate path strings removed by the 2026-11-19 call-evidence filter as arguments to a local label-reading helper. The regression report checks that these strings remain clues or are ignored and do not produce HTTP requests. The original Nuxt bundle bodies were not present in the frozen artifacts, so this validates the candidate values against a controlled helper-call shape rather than claiming source-line identity with the public bundle.

Run the browserless regression from the repository root:

```powershell
python tools/run_local_site_regression.py
```

The runner installs locked npm dependencies when needed, builds each selected site, starts it on loopback-only ports, builds Basecrawler from the current checkout, crawls each site, records timing and responses under `artifacts/crawler-comparison/`, then stops the local servers. The unified matrix runner also compares frozen Katana baselines and Browcrawler browser interactions.

The accepted route set is kept in the runner. The negative strings in the apps are expected to remain unscheduled. Updating a fixture requires updating that route set and the acceptance checks together.

Framework versions are pinned in package manifests and lockfiles. No crawl traffic is sent to public reference sites.

The Next.js fixture explicitly enables production browser source maps so source-map parsing is covered in the local matrix; this setting is limited to this loopback test fixture.
