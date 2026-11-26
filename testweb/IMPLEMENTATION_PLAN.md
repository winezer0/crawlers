# 本地爬虫测试站点实现方案

状态：规划中  
更新日期：2026-11-27

## 1. 目标

在仓库 `testweb/` 下建设一套可本地运行、可重复、可审计的爬虫测试网站。每个站点提供人工维护的真实目标契约，作为 basecrawler、browcrawler 和外部竞品的独立基线，避免将任一爬虫的发现结果反过来当作真值。

这套站点是 13 个测试目标的本地代表实现，不是对公网产品的复制。目标是保留可测的页面结构、框架运行方式和接口行为，同时让 URL、HTTP 方法、参数、触发条件与预期响应都由测试代码明确掌控。

## 2. 设计原则

1. **真值先于爬虫运行**：契约由站点路由和前端调用定义，另有机器可读清单；任何爬虫输出均不得自动追加为真值。
2. **前端框架真实**：Vue、React、Angular、Nuxt、Next.js、SvelteKit 目标使用对应框架和固定版本实现。仅用 Go 模拟 HTML 不算框架覆盖。
3. **后端可复用**：可由一个本地 Go 测试服务承载多个独立站点/API 前缀；各目标的契约和端口/前缀隔离。Next/Nuxt/SvelteKit 如需真实 SSR/服务端路由，则保留其框架服务器。
4. **默认安全**：仅绑定 loopback；固定假数据；不访问公网、不提交真实业务数据、不具备破坏性操作。
5. **发现层次可区分**：每个 URL 标注发现层级，例如 HTML 静态链接、JS 字面量、打包路由、动态执行、浏览器交互、API 方法线索。
6. **可复现**：固定依赖版本、启动参数、种子、预算和响应数据；健康检查和复位过程有明确定义。
7. **旧证据隔离**：本地测试站点生成的新契约、运行记录存入独立目录；不得把历史 artifacts 的结果混入新金集。

## 3. 目标站点清单（13 个）

清单沿用统一对比方案中的 13 个目标 ID。它们是 13 个测试配置，不代表 13 种互不相同的技术栈。

| ID | 本地目标类型 | 实现重点 | 是否需要专属前端 |
|---|---|---|---|
| `testasp` | 传统多页服务端站 | HTML 多页链接、表单、分页、重定向、注释/资源路径 | 否；用 Go 模板模拟传统站即可，不仿造真实 ASP 运行时 |
| `testphp` | PHP 风格多页站 | 查询参数页面、服务端分页、表单 action、相对链接 | 否；代表行为，不要求 PHP 运行时 |
| `testaspnet` | ASP.NET 风格多页站 | 大小写/扩展名路径、查询参数、传统表单 | 否；代表行为，不要求 .NET 运行时 |
| `testhtml5` | HTML5 / 原生 JS 站 | 多种 HTML 引用、内联脚本、CSS、图片/静态资源噪音 | 否 |
| `juice-shop` | Angular 风格 API SPA | 多路由、延迟加载、REST API、只读交互；不复制漏洞或真实业务 | 是，Angular |
| `framework-vue` | Vue 文档型应用 | Vue Router、嵌套路由、懒加载、动态路由 | 是，Vue |
| `framework-react` | React 文档型应用 | React Router、客户端跳转、动态组件和懒加载 | 是，React |
| `app-react-todomvc` | React Todo 风格应用 | 列表/详情、过滤路由、交互触发只读 API | 是，React |
| `app-vue-todomvc` | Vue Todo 风格应用 | 与 React 版本保持同等目标契约，比较框架表达差异 | 是，Vue |
| `framework-angular` | Angular 教程型应用 | Angular Router、lazy module、guard 的匿名可见路径 | 是，Angular |
| `framework-nuxt` | Nuxt SSR/SPA 混合站 | SSR 页面、Nuxt 路由、懒加载组件、公开 API 调用 | 是，Nuxt |
| `framework-nextjs` | Next.js App Router 站 | 服务端页面、客户端组件、RSC 请求、静态/动态路由与 chunk | 是，Next.js |
| `framework-sveltekit` | SvelteKit SSR 站 | 文件路由、load 请求、客户端导航及延迟加载 | 是，SvelteKit |

每站需包含至少一个正常页面、一个可跟进链接、一个静态资源、一个参数化 URL；SPA/框架站额外包含至少一个需 JS 或交互触发的目标。目标数量按契约统计，不按站点页面数估算。

## 4. 仓库布局建议

```text
testweb/
  README.md                 # 启动、停止、运行矩阵和安全说明
  IMPLEMENTATION_PLAN.md    # 本方案
  manifest.yaml             # 13 个站点的 ID、入口、端口、框架、启用状态
  contracts/
    testasp.yaml            # 每站唯一真值契约
    ...
  backend/                  # 共用 Go API 和传统页面服务
  apps/
    vue/
    react/
    angular/
    nuxt/
    nextjs/
    sveltekit/
  fixtures/                 # 固定 JSON、HTML、JS、CSS、source map
  scripts/                  # 启动、健康检查、复位、导出真值
  runs/                     # 每次新运行单独目录；不纳入源码版本控制
```

如某框架官方脚手架必须有各自配置文件，保留各应用独立 `package.json` / lockfile。公共契约不得依赖前端构建输出的随机 hash；构建可重复或在测试期关闭随机化。`runs/` 默认加入 `.gitignore`，但契约和 fixture 应进入版本控制。

## 5. 真值契约格式

每一项至少包括：

```yaml
site_id: framework-react
base_url: http://127.0.0.1:PORT
routes:
  - id: catalog-list
    method: GET
    path: /api/catalog?page=1
    category: api
    discoverability: js-literal
    trigger: initial-page
    expected_status: 200
    read_only: true
    expected_request_headers: []
    expected_body_keys: [items, next]
```

建议支持的 `discoverability` 值：`html-link`、`html-form`、`response-header`、`robots-sitemap`、`js-literal`、`bundle-route`、`runtime-js`、`interaction`、`api-response`、`negative-control`。

URL 精确性策略：

- 比较身份为 `(HTTP method, normalized URL)`；query 参数值是否纳入身份在站点契约中逐项声明。
- 动态路径用明确的参数规则或测试实例 ID，例如 `/api/items/item-01`，禁止用不约束的宽泛正则掩盖漏报。
- GET、POST、PUT、PATCH、DELETE 分别建契约；POST 等写方法仅接收固定测试数据，具备复位能力，并仅允许 loopback。
- OPTIONS/HEAD 只有在目标契约明确需要时才纳入。
- 区分“链接/线索被发现”和“请求被发送/服务端收到”；禁止把自动发起的破坏性操作作为通过条件。

## 6. 接口和页面场景

共用 API 最少覆盖：

- GET 列表与详情，含分页和路径参数。
- GET 查询参数组合，验证参数排序/编码规范化。
- POST 表单与 JSON 两种请求体。
- PUT、PATCH、DELETE 的方法识别；验证使用固定对象、复位状态及只允许本机访问。
- 返回 JSON 中的下一页 URL、嵌套 URL 字段、无效/外域 URL 负控。
- 页面发出的只读请求与点击后发出的只读请求，确保静态爬虫与浏览器爬虫差异可计量。
- 重定向、404、受限状态、重复 URL、资源后缀噪音、source map 有/无两种情况。

服务端记录每次请求的时间、站点、方法、完整 URL、来源页、请求头白名单摘要、响应状态和契约 ID。不得记录系统环境变量、用户 Cookie 或真实凭证。

## 7. 实施阶段

### 阶段 0：冻结验收协议

- 定义 `manifest.yaml`、契约字段、URL 规范化和计数口径。
- 确认 13 个站点 ID、入口和运行方式；发现源配置中的端口冲突和目标遗漏。
- 建立负控与状态分类：候选、命中契约、非契约 URL、请求失败、被预算截断。

### 阶段 1：基础服务与传统站

- 实现 loopback 服务管理、健康检查、固定数据、请求日志、复位和真值导出。
- 建立 `testasp`、`testphp`、`testaspnet`、`testhtml5` 四个代表站。
- 验证 HTML、表单、分页、响应头、重定向及资源链接的真实契约。

### 阶段 2：Vue/React 与 Todo 应用

- 创建 Vue、React 两套实际框架应用。
- 实现框架首页、嵌套路由、参数路由、懒加载和只读点击请求。
- TodoMVC 两个站点使用相同功能真值集合，便于比较，而不是复制全部产品体验。

### 阶段 3：Angular、Nuxt、Next.js、SvelteKit

- 使用各自固定版本和 lockfile，优先实现当前竞品差距最明显的路由与运行时特征。
- Nuxt/Next/SvelteKit 保留真实 SSR 服务端路径；Next 单独覆盖 App Router/RSC，但把 RSC 网络请求与普通业务 API 分开统计。
- 逐框架验证构建、健康检查、API 契约和静态/浏览器发现目标。

### 阶段 4：Angular 风格 Juice Shop 替代站

- 实现独立的 Angular SPA + API 测试站，不复制上游 Juice Shop 数据、漏洞或品牌内容。
- 覆盖 API 密集、菜单交互和懒加载模块，以代表该测试类别。

### 阶段 5：爬虫接入与基线冻结

- 先对每站人工审阅契约，再运行站点自身契约测试。
- 分别运行 basecrawler、browcrawler 和外部工具；静态、浏览器、离线 JS 分组出表。
- 每次运行写入全新目录，记录站点仓库提交哈希、前端 lockfile 哈希、配置哈希、爬虫版本/哈希、命令和预算。
- 冻结的契约版本只增不改；需修正时新建契约版本并记录变更原因，旧运行保持原状。

## 8. 阶段验收

每个站点合并前应具备：

1. 可重复启动、停止、健康检查和清理步骤。
2. 契约路径与服务器实际路由一致；所有契约请求有服务端断言。
3. URL 和方法的人工审查记录；契约数量可由仓库代码复算。
4. 至少一个负控，证明越权主机、伪路径或未登记方法不会被错误计为命中。
5. 前端框架站明确区分初始 HTML 可见目标、静态 JS 可见目标和交互新增目标。
6. 本机运行不会请求外部站点；默认监听 127.0.0.1，固定本地测试数据。
7. 依赖版本和命令可复现；站点故障时能在报告中区分环境失败和爬虫发现失败。

整体金集验收应对全部启用站点至少运行三轮爬虫对照；验收指标包括 `(method, URL)` 召回率、精确率、误报类别、轮间稳定性、请求预算完成情况和耗时。具体数值门槛在阶段 0 冻结，不能在看到结果后调整。

## 9. 风险和边界

- 13 个目标中存在重复框架，不应按“13 种框架”宣传；真实框架数少于目标配置数。
- 以 Go 后端统一服务可降低维护成本，但它不能替代真实框架 SSR/构建产物；两者职责要分开。
- Next.js RSC、Nuxt hydration、浏览器交互会随框架版本变化，需固定版本；框架升级应视为测试语料变更。
- 动态时间戳、随机 ID、开发服务器热重载会降低稳定性；应禁用或在契约中用明确规则处理。
- 不将测试站点暴露到公网。若后续确需远程运行，单独评审认证、隔离、速率和清理策略。
- 测试站点的 API 方法应只模拟安全测试数据，不执行系统命令、文件写入或外部网络副作用。

## 10. 方案收口

本文件是实施规划，不表示 13 个站点已实现。实施开始后按阶段维护完成状态、契约版本和验收证据。优先先完成阶段 0 和阶段 1，形成可审阅的人工真值流程，再扩展到前端框架站点。
