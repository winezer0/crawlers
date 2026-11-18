# 爬虫对比结果

运行目录：`C:\ISEC\Deving\crawlers\artifacts\crawler-comparison\phase2-sitemap-discovery-20261118`

URL 数量来自各工具原始输出的 URL 抽取和统一归一化。该表不代表 URL 当前可访问性；差异 URL 需另行复核。

| 站点 | 类型 | 工具 | 模式 | 轮次 | 状态 | 唯一 URL | 站内 | 站外 | 独有站内 URL | 耗时(秒) | 版本 |
|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---|
| framework-vue | framework-vue | basecrawler | static | 1 | completed | 196 | 196 | 0 | n/a | 24.48 | source (go run) |
| framework-vue | framework-vue | basecrawler | static | 2 | completed | 196 | 196 | 0 | n/a | 22.59 | source (go run) |
| framework-vue | framework-vue | basecrawler | static | 3 | completed | 196 | 196 | 0 | n/a | 23.74 | source (go run) |
| framework-react | framework-react | basecrawler | static | 1 | completed | 106 | 106 | 0 | n/a | 22.69 | source (go run) |
| framework-react | framework-react | basecrawler | static | 2 | completed | 106 | 106 | 0 | n/a | 22.61 | source (go run) |
| framework-react | framework-react | basecrawler | static | 3 | completed | 106 | 106 | 0 | n/a | 23.31 | source (go run) |
| framework-angular | framework-angular | basecrawler | static | 1 | completed | 1658 | 1658 | 0 | n/a | 22.64 | source (go run) |
| framework-angular | framework-angular | basecrawler | static | 2 | completed | 1658 | 1658 | 0 | n/a | 24.55 | source (go run) |
| framework-angular | framework-angular | basecrawler | static | 3 | completed | 1658 | 1658 | 0 | n/a | 22.40 | source (go run) |
| framework-nextjs | framework-nextjs | basecrawler | static | 1 | completed | 1195 | 1195 | 0 | n/a | 22.78 | source (go run) |
| framework-nextjs | framework-nextjs | basecrawler | static | 2 | completed | 1195 | 1195 | 0 | n/a | 22.49 | source (go run) |
| framework-nextjs | framework-nextjs | basecrawler | static | 3 | completed | 1195 | 1195 | 0 | n/a | 23.03 | source (go run) |
| framework-sveltekit | framework-sveltekit | basecrawler | static | 1 | completed | 399 | 399 | 0 | n/a | 22.47 | source (go run) |
| framework-sveltekit | framework-sveltekit | basecrawler | static | 2 | completed | 399 | 399 | 0 | n/a | 34.70 | source (go run) |
| framework-sveltekit | framework-sveltekit | basecrawler | static | 3 | completed | 399 | 399 | 0 | n/a | 22.63 | source (go run) |

原始输出和命令记录保存在每个 `runs/<site>/<tool>/round-N/` 子目录。


## Per-tool summary

| Site | Tool | Mode | Rounds | Mean in-scope URLs | Mean unique-only URLs | Round stability (Jaccard) | Failed/interrupted |
|---|---|---|---:|---:|---:|---:|---:|
| framework-angular | basecrawler | static | 3 | 1658.0 |  | 1.0 | 0 |
| framework-nextjs | basecrawler | static | 3 | 1195.0 |  | 1.0 | 0 |
| framework-react | basecrawler | static | 3 | 106.0 |  | 1.0 | 0 |
| framework-sveltekit | basecrawler | static | 3 | 399.0 |  | 1.0 | 0 |
| framework-vue | basecrawler | static | 3 | 196.0 |  | 1.0 | 0 |

## Candidate and timing counts

Result URLs come from the tool output or standard streams. Discovery URLs come from source-edge records when the tool provides them. Unique URL totals are the deduplicated union.

| Site | Tool | Mean result URLs | Mean discovery URLs | Mean unique in-scope URLs | Mean runtime (s) |
|---|---|---:|---:|---:|---:|
| framework-angular | basecrawler | 19.0 | 1658.0 | 1658.0 | 23.2 |
| framework-nextjs | basecrawler | 17.0 | 1195.0 | 1195.0 | 22.77 |
| framework-react | basecrawler | 19.0 | 106.0 | 106.0 | 22.87 |
| framework-sveltekit | basecrawler | 20.0 | 404.0 | 399.0 | 26.6 |
| framework-vue | basecrawler | 18.0 | 197.0 | 196.0 | 23.6 |

Recovered runs have an unknown exit code; `known_exit_rounds` counts only runs whose exit code was recorded.

Raw outputs and command records are under `runs/<site>/<tool>/round-N/`.
