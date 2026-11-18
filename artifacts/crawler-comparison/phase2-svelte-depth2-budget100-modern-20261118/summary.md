# 爬虫对比结果

运行目录：`C:\ISEC\Deving\crawlers\artifacts\crawler-comparison\phase2-svelte-depth2-budget100-modern-20261118`

URL 数量来自各工具原始输出的 URL 抽取和统一归一化。该表不代表 URL 当前可访问性；差异 URL 需另行复核。

| 站点 | 类型 | 工具 | 模式 | 轮次 | 状态 | 唯一 URL | 站内 | 站外 | 独有站内 URL | 耗时(秒) | 版本 |
|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---|
| framework-sveltekit | framework-sveltekit-depth2-budget100 | basecrawler | static | 1 | completed | 706 | 706 | 0 | n/a | 108.09 | source (go run) |
| framework-sveltekit | framework-sveltekit-depth2-budget100 | basecrawler | static | 2 | completed | 706 | 706 | 0 | n/a | 107.94 | source (go run) |
| framework-sveltekit | framework-sveltekit-depth2-budget100 | basecrawler | static | 3 | completed | 706 | 706 | 0 | n/a | 107.58 | source (go run) |

原始输出和命令记录保存在每个 `runs/<site>/<tool>/round-N/` 子目录。


## Per-tool summary

| Site | Tool | Mode | Rounds | Mean in-scope URLs | Mean unique-only URLs | Round stability (Jaccard) | Failed/interrupted |
|---|---|---|---:|---:|---:|---:|---:|
| framework-sveltekit | basecrawler | static | 3 | 706.0 |  | 1.0 | 0 |

## Candidate and timing counts

Result URLs come from the tool output or standard streams. Discovery URLs come from source-edge records when the tool provides them. Unique URL totals are the deduplicated union.

| Site | Tool | Mean result URLs | Mean discovery URLs | Mean unique in-scope URLs | Mean runtime (s) |
|---|---|---:|---:|---:|---:|
| framework-sveltekit | basecrawler | 91.0 | 862.0 | 706.0 | 107.87 |

Recovered runs have an unknown exit code; `known_exit_rounds` counts only runs whose exit code was recorded.

Raw outputs and command records are under `runs/<site>/<tool>/round-N/`.
