# 本轮差异与验收说明

本轮 basecrawler 全程无浏览器。实际结果、运行秒数和参考状态见 [summary.md](summary.md)，逐 URL 判断见各站点 `comparison.jsonl`，HTTP 证据见 `verification.jsonl`。原始运行命令保留在每个模式的 `command.json`。

七站点已确认有效 GET 参考分别为 10、13、84、219、585、13、410；basecrawler 分别覆盖 10、13、84、219、585、13、406。覆盖率达到 95% 的数值门槛不等于正式验收通过：目前正式通过 4/7，低于固定七站点至少六站点的要求。

## 已解决的差异

- React：Pages Router 的 16 条数据请求在原有 JS/页面路径发现之外，需要根据明确的构建 ID、动态路由和已观察链接恢复。新增数据请求曾挤占访问预算，已延后调度，避免丢失 blog/community 页面。
- Angular：`/assets/tutorials/first-app/metadata.json` 和 `source-code.json` 的路径含函数参数。修复参数作用域误取外层同名常量，并用已观察教程路径为 JSON 数据模板生成候选。扩大 Katana 静态参考后，585 条有效页面/JSON 全部覆盖。
- Next.js：`/api/show-consent-banner` 作为两段路径变量传给数据加载函数，goja 的裸变量形状规则过严，未进入候选；已与 treesitter 的两段路径规则对齐，继续排除版本号前缀。当前已确认的 13 项全部覆盖。
- SvelteKit：`llms.txt` 在种子解析前登记已见，导致 HTML 再次发现 `/playground` 时不能提升已有任务优先级。修复引导入口调度后，覆盖从 317/410 提升到 406/410；前后网络条件有波动，耗时只报告实测值，不作纯速度排名。
- 重复过滤：修复 AST、来源边及输出层对方法和参数契约的保留。路径相似过滤仍启用；过滤后输出行数不是覆盖率分母。

## 尚缺的四条有效 URL

1. `https://svelte.dev/docs/kit/introduction/__data.json?x-sveltekit-invalidated=011`
2. `https://svelte.dev/docs/svelte/__data.json?x-sveltekit-invalidated=01`
3. `https://svelte.dev/playground/__data.json?x-sveltekit-invalidated=010`
4. `https://svelte.dev/playground/hello-world/__data.json?show=input&x-sveltekit-invalidated=011`

这些是浏览器导航产生的 SvelteKit 数据请求。失效位图取决于布局节点和导航状态，不能把随机构造一个位图当成恢复了实际请求。后续需结合 hydration、节点清单及 server-load 依赖恢复请求契约，再单独验证参数变体与响应节点。

## 阻止正式验收的问题

- 两个传统 HTTP 站点的 Katana 浏览器模式返回空结果；debug 日志明确为 `net::ERR_BLOCKED_BY_CLIENT`。调整页面等待、代理、scope 后仍复现；指定 obscura 可执行程序也因 Katana 注入其不支持的 Chromium 参数而失败。这是参考运行不可用，不能算 basecrawler 领先。
- `https://nextjs.org/api/get-session` 已被 basecrawler 发现，但核验响应为 HTTP 400 JSON `Forbidden`，保留待核验。它不属于当前已确认的漏爬 URL，也不能据此确认业务成功。
- 仍需固定同一遍历策略和网络环境再跑三轮稳定性测试，并补充非 GET 方法、参数和完整 API 契约验收。本轮没有证明可替代认证、运行时 DOM 交互或验证码流程。

Katana 的 `max depth reached` 记录属于未请求的边界发现，与 basecrawler 的来源发现相对应，不统计为网络请求失败。全部真实请求失败与退出码 0 的空结果均单独标记。

## Source Map 独立验证

已下载 map 在本地调试，再通过真实网页复验。配套编译 JS 明确给出 `https://t-cyapi.shdaex.com`，与前端 `t-ciyuan.shdaex.com` 不同。最新线上解析重定位 293 个不同方法/URL 候选，保留 `api_base_url`、编译脚本证据及原始前端 URL。单独访问后端 `/api/status` 得到 JSON；没有把 293 个候选全部标成业务有效。

最新线上仅导航 map 和配套 JS，关闭 testfill，报告总时长 7.089 秒；报告仍含 `analysis_failure` 和预算截断，不宣称完整扫描。相关文件为 `api-base-live-report.json`、`api-base-live-results.jsonl`。

本地下载 map 的 CLI 集成验证：286 个接口组、535 条生成用例、520 条实际发送、1 条参数名未知、14 条由既有 safety 策略跳过，总时长 4.028 秒。响应来自统一 JSON 模拟服务，只证明填充与发送链路；不能当作线上全部 API 验证成功。

## 快照口径

保留此前已完成的 Katana 快照，失败的 Angular/Next.js 静态轮次重新运行并扩大参考。历史参考使用 Katana 默认 depth-first，补跑使用脚本声明的 breadth-first；各次真实命令均保留，不能宣称全矩阵采用同一策略或用其耗时作严格速度排名。该目录用于追踪能力差集，下一正式稳定性基线需全矩阵统一策略重新运行。
