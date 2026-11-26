# crawlers 项目指引

本仓库是 Go workspace 爬虫项目。开始工作前阅读本文件及 [AGENTS.common.md](AGENTS.common.md)，并遵守目标模块目录下可能存在的进一步 AGENTS.md。

## 项目范围

- basecrawler：基础爬虫能力模块。
- Basecrawler 目标：浏览器渲染或交互发现的有效 URL 都应进入 Basecrawler 的必测目标集；Basecrawler 要通过 JS 执行、静态分析等无浏览器技术尽可能复现这些发现。
- browcrawler：浏览器爬虫模块，依赖基础模块；承接 basecrawler 无法实现的框架能力（见「项目约定」第 6 条）。
- othercrawlers：外部爬虫工具与相关配置。
- tools、docs、TODO：仓库工具、文档与规划材料。
- artifacts：运行或比较产物，除非任务涉及，不作为源码修改目标。

模块清单以根目录 go.work 为准；新增/移除模块时同步更新 workspace 并检查相关文档。

## 项目约定

1. 遵循 AGENTS.common.md 中适用于 Go workspace 与爬虫场景的工程规范。
2. 不适用其他项目专属的规则：例如 Web 漏洞扫描平台、扫描任务/API 密钥、mgcodesec 解析器、源位置索引契约及其 fixture/脚本约定。
3. 维护现有爬虫行为和配置兼容性；如需求要求改变公开 CLI 参数、配置格式或模块 API，先说明影响范围，并更新相关文档与测试。
4. 对 URL 处理、代理、并发与外部请求等边界修改，增加与风险相称的针对性测试。
5. 运行命令应以目标模块及仓库脚本为准。测试单个模块可在该模块目录执行 go test ./...；workspace 全量验收需按用户要求明确执行范围。
6. 框架覆盖策略：优先在 basecrawler（无浏览器）以 JS 执行、静态分析等方式实现框架适配；经评估确认无法用 basecrawler 实现的框架，使用 browcrawler（浏览器渲染 + 全流量录制）实现，并保留对照证据与边界结论。项目目标是覆盖当前所有公开的爬虫能力，持续对标补齐差距，达到完美的爬虫效果。
