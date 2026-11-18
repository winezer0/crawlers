# 爬虫对比结果

运行目录：`C:\ISEC\Deving\crawlers\artifacts\crawler-comparison\phase2-llms-index-modern-20261118`

URL 数量来自各工具原始输出的 URL 抽取和统一归一化。该表不代表 URL 当前可访问性；差异 URL 需另行复核。

| 站点 | 类型 | 工具 | 模式 | 轮次 | 状态 | 唯一 URL | 站内 | 站外 | 独有站内 URL | 耗时(秒) | 版本 |
|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---|
| framework-vue | framework-vue | basecrawler | static | 1 | completed | 202 | 202 | 0 | n/a | 26.61 | source (go run) |
| framework-vue | framework-vue | basecrawler | static | 2 | completed | 202 | 202 | 0 | n/a | 23.89 | source (go run) |
| framework-vue | framework-vue | basecrawler | static | 3 | completed | 202 | 202 | 0 | n/a | 23.71 | source (go run) |
| framework-react | framework-react | basecrawler | static | 1 | completed | 233 | 233 | 0 | n/a | 23.95 | source (go run) |
| framework-react | framework-react | basecrawler | static | 2 | completed | 233 | 233 | 0 | n/a | 23.67 | source (go run) |
| framework-react | framework-react | basecrawler | static | 3 | completed | 233 | 233 | 0 | n/a | 23.74 | source (go run) |
| framework-angular | framework-angular | basecrawler | static | 1 | completed | 1658 | 1658 | 0 | n/a | 23.77 | source (go run) |
| framework-angular | framework-angular | basecrawler | static | 2 | completed | 1658 | 1658 | 0 | n/a | 23.73 | source (go run) |
| framework-angular | framework-angular | basecrawler | static | 3 | completed | 1658 | 1658 | 0 | n/a | 23.67 | source (go run) |
| framework-nextjs | framework-nextjs | basecrawler | static | 1 | completed | 1365 | 1365 | 0 | n/a | 29.10 | source (go run) |
| framework-nextjs | framework-nextjs | basecrawler | static | 2 | completed | 1365 | 1365 | 0 | n/a | 31.06 | source (go run) |
| framework-nextjs | framework-nextjs | basecrawler | static | 3 | completed | 1365 | 1365 | 0 | n/a | 29.09 | source (go run) |
| framework-sveltekit | framework-sveltekit | basecrawler | static | 1 | completed | 409 | 409 | 0 | n/a | 28.16 | source (go run) |
| framework-sveltekit | framework-sveltekit | basecrawler | static | 2 | completed | 409 | 409 | 0 | n/a | 29.28 | source (go run) |
| framework-sveltekit | framework-sveltekit | basecrawler | static | 3 | completed | 409 | 409 | 0 | n/a | 37.91 | source (go run) |

原始输出和命令记录保存在每个 `runs/<site>/<tool>/round-N/` 子目录。


## Per-tool summary

| Site | Tool | Mode | Rounds | Mean in-scope URLs | Mean unique-only URLs | Round stability (Jaccard) | Failed/interrupted |
|---|---|---|---:|---:|---:|---:|---:|
| framework-angular | basecrawler | static | 3 | 1658.0 |  | 1.0 | 0 |
| framework-nextjs | basecrawler | static | 3 | 1365.0 |  | 1.0 | 0 |
| framework-react | basecrawler | static | 3 | 233.0 |  | 1.0 | 0 |
| framework-sveltekit | basecrawler | static | 3 | 409.0 |  | 1.0 | 0 |
| framework-vue | basecrawler | static | 3 | 202.0 |  | 1.0 | 0 |

## Candidate and timing counts

Result URLs come from the tool output or standard streams. Discovery URLs come from source-edge records when the tool provides them. Unique URL totals are the deduplicated union.

| Site | Tool | Mean result URLs | Mean discovery URLs | Mean unique in-scope URLs | Mean runtime (s) |
|---|---|---:|---:|---:|---:|
| framework-angular | basecrawler | 19.0 | 1658.0 | 1658.0 | 23.72 |
| framework-nextjs | basecrawler | 17.0 | 1365.0 | 1365.0 | 29.75 |
| framework-react | basecrawler | 19.0 | 233.0 | 233.0 | 23.79 |
| framework-sveltekit | basecrawler | 20.0 | 414.0 | 409.0 | 31.78 |
| framework-vue | basecrawler | 18.0 | 203.0 | 202.0 | 24.74 |

Recovered runs have an unknown exit code; `known_exit_rounds` counts only runs whose exit code was recorded.

Raw outputs and command records are under `runs/<site>/<tool>/round-N/`.
