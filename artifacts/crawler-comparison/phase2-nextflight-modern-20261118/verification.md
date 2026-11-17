# Candidate URL verification

Checks a deterministic SHA-256 sample of all in-scope candidates from `basecrawler`, including URLs shared with other tools. Uses HEAD with redirect following and a one-byte ranged GET fallback when HEAD is unsupported.

Run directory: `C:\ISEC\Deving\crawlers\artifacts\crawler-comparison\phase2-nextflight-modern-20261118`

| Classification | Count |
|---|---:|
| accessible | 100 |
| limit_not_checked | 845 |
