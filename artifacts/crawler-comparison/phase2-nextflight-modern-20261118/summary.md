# 爬虫对比结果

运行目录：`C:\ISEC\Deving\crawlers\artifacts\crawler-comparison\phase2-nextflight-modern-20261118`

URL 数量来自各工具原始输出的 URL 抽取和统一归一化。该表不代表 URL 当前可访问性；差异 URL 需另行复核。

| 站点 | 类型 | 工具 | 模式 | 轮次 | 状态 | 唯一 URL | 站内 | 站外 | 独有站内 URL | 耗时(秒) | 版本 |
|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---|
| framework-vue | framework-vue | basecrawler | static | 1 | completed | 81 | 81 | 0 | n/a | 22.91 | source (go run) |
| framework-vue | framework-vue | basecrawler | static | 2 | completed | 81 | 81 | 0 | n/a | 23.64 | source (go run) |
| framework-vue | framework-vue | basecrawler | static | 3 | completed | 81 | 81 | 0 | n/a | 22.53 | source (go run) |
| framework-react | framework-react | basecrawler | static | 1 | completed | 92 | 92 | 0 | n/a | 22.63 | source (go run) |
| framework-react | framework-react | basecrawler | static | 2 | completed | 92 | 92 | 0 | n/a | 22.62 | source (go run) |
| framework-react | framework-react | basecrawler | static | 3 | completed | 92 | 92 | 0 | n/a | 23.25 | source (go run) |
| framework-angular | framework-angular | basecrawler | static | 1 | completed | 30 | 30 | 0 | n/a | 22.58 | source (go run) |
| framework-angular | framework-angular | basecrawler | static | 2 | completed | 30 | 30 | 0 | n/a | 22.57 | source (go run) |
| framework-angular | framework-angular | basecrawler | static | 3 | completed | 30 | 30 | 0 | n/a | 22.56 | source (go run) |
| framework-nextjs | framework-nextjs | basecrawler | static | 1 | completed | 356 | 356 | 0 | n/a | 22.60 | source (go run) |
| framework-nextjs | framework-nextjs | basecrawler | static | 2 | completed | 356 | 356 | 0 | n/a | 22.62 | source (go run) |
| framework-nextjs | framework-nextjs | basecrawler | static | 3 | completed | 356 | 356 | 0 | n/a | 22.60 | source (go run) |
| framework-sveltekit | framework-sveltekit | basecrawler | static | 1 | completed | 386 | 386 | 0 | n/a | 22.64 | source (go run) |
| framework-sveltekit | framework-sveltekit | basecrawler | static | 2 | completed | 386 | 386 | 0 | n/a | 23.61 | source (go run) |
| framework-sveltekit | framework-sveltekit | basecrawler | static | 3 | completed | 386 | 386 | 0 | n/a | 22.65 | source (go run) |

原始输出和命令记录保存在每个 `runs/<site>/<tool>/round-N/` 子目录。


## Per-tool summary

| Site | Tool | Mode | Rounds | Mean in-scope URLs | Mean unique-only URLs | Round stability (Jaccard) | Failed/interrupted |
|---|---|---|---:|---:|---:|---:|---:|
| framework-angular | basecrawler | static | 3 | 30.0 |  | 1.0 | 0 |
| framework-nextjs | basecrawler | static | 3 | 356.0 |  | 1.0 | 0 |
| framework-react | basecrawler | static | 3 | 92.0 |  | 1.0 | 0 |
| framework-sveltekit | basecrawler | static | 3 | 386.0 |  | 1.0 | 0 |
| framework-vue | basecrawler | static | 3 | 81.0 |  | 1.0 | 0 |

## Candidate and timing counts

Result URLs come from the tool output or standard streams. Discovery URLs come from source-edge records when the tool provides them. Unique URL totals are the deduplicated union.

| Site | Tool | Mean result URLs | Mean discovery URLs | Mean unique in-scope URLs | Mean runtime (s) |
|---|---|---:|---:|---:|---:|
| framework-angular | basecrawler | 19.0 | 28.0 | 30.0 | 22.57 |
| framework-nextjs | basecrawler | 17.0 | 356.0 | 356.0 | 22.61 |
| framework-react | basecrawler | 19.0 | 92.0 | 92.0 | 22.83 |
| framework-sveltekit | basecrawler | 20.0 | 391.0 | 386.0 | 22.97 |
| framework-vue | basecrawler | 18.0 | 81.0 | 81.0 | 23.03 |

Recovered runs have an unknown exit code; `known_exit_rounds` counts only runs whose exit code was recorded.

Raw outputs and command records are under `runs/<site>/<tool>/round-N/`.
