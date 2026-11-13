# 爬虫解析 React 路由的方案

> 适用范围：Go 为主栈、需要批量做 SPA 资产与路由测绘（非授权站点禁止使用）、目标站点使用 React 生态构建（React Router / Next.js / Remix / TanStack Router / 自研路由）。
> 本文同时覆盖 **普通爬虫（无浏览器、纯 JS 源码静态解析）** 与 **浏览器爬虫（无头浏览器运行时提取）**，并给出组合落地方案。

---

## 1. 问题定义与 React 的特殊困难

要解决三层目标，与 Vue 文档一致：

| 层次 | 目标 | 产出 |
|---|---|---|
| L1 路由发现 | 站点有哪些前端路由 path | 路由清单 |
| L2 接口发现 | 每个页面背后的接口 | 路由 ↔ 接口映射 |
| L3 权限绕过 | 直接访问受保护路由 | 受保护页面内容 |

### 1.1 为什么 React 比 Vue 难

**Vue 有天然的"全局单例"出口，React 没有。** 这是两个框架解析难度的分水岭：

| 维度 | Vue | React |
|---|---|---|
| router 实例位置 | 挂在 `app.config.globalProperties.$router`，几乎必可得 | 没有统一全局出口；`<BrowserRouter>` 的 router 藏在组件树闭包里 |
| 路由表形态 | `router.options.routes` 一张完整表 | 多数项目是**分散的 `<Route>` 组件**，没有集中表 |
| 路由历史 | `router.options.history` 可控 | 依赖 `history.pushState` 与框架内部匹配，无统一 API |
| 动态路由 | 常见（`api.getRouters()` → `addRoute`） | 相对少见，但 Next.js 有文件系统路由（反而很好解） |
| 官方调试钩子 | `__VUE_DEVTOOLS_APP_RECORDS` | 有 React DevTools 的 Fiber，但**结构随版本变动，极不稳定** |

> **核心结论**：React **没有"读一张表就全拿到"的路径**。浏览器侧最稳的不是"读 router 对象"，而是**监控跳转（history 补丁）+ BFS 点击遍历**；静态侧最稳的是**扫描源码里的 `path` 字面量 + 识别 Next.js 的构建清单**。

---

## 2. React 路由的几种存在形态（决定方案）

| 形态 | 代码特征 | 无浏览器 | 浏览器 |
|---|---|---|---|
| A. React Router v5/v6 集中配置 | `const routes=[{path:'/a'}]` + `<Routes>` 渲染 | ✅ 字面量可扫 | ✅ BFS 可走 |
| B. `<Route path="/x">` 分散声明 | JSX 里一个个 `<Route>` | ✅ 可扫（JSX 属性） | ✅ 可走 |
| C. `createBrowserRouter([...])` v6.4+ | 对象数组配置 | ✅ 可扫 | ✅ 可走 |
| D. 懒加载路由 | `lazy: () => import('./p')` | ✅ path 仍在 | ✅ 可走 |
| E. Next.js Pages Router | `pages/` 目录 → 构建产物 `*-manifest.json` | ✅ **manifest 直读，最准** | ✅ 可走 |
| F. Next.js App Router | `app/` 目录 → RSC + route manifest | ⚠️ 需读 `.next` 产物 | ✅ 可走（含 `_rsc` 请求） |
| G. 动态构造路由 | `routes = build(apiData)`、路由表来自接口 | ❌ | ⚠️ 只能靠 BFS |
| H. 自研路由（无框架） | 自己维护 path 常量表 | ✅ 常量可扫 | ✅ BFS |
| I. 混淆打包 | obfuscator 编码 path 字符串 | ⚠️ 需反混淆 | ✅ 可走（跑起来就跳转） |

---

## 3. 方案矩阵总览

| 编号 | 方案 | 浏览器 | 技术栈 | L1 | L2 | L3 | 评价 |
|---|---|---|---|---|---|---|---|
| R-S1 | JSHunter 静态扫描 | ❌ | Go 原生 | ✅ | ✅ | ❌ | **推荐主力（无浏览器）** |
| R-S2 | Go 原生 AST visitor（goja/parser + esbuild 预处理） | ❌ | Go 原生 | ✅ | ✅ | ❌ | 可控、可定制，JSX + 对象双匹配 |
| R-S3 | webcrack 反混淆 + AST | ❌ | Node（SEA 打包） | ✅ | ⚠️ | ❌ | 混淆站点降级 |
| R-S4 | Next.js 构建清单直读 | ❌ | Go（HTTP + JSON） | ✅ | ✅ | ❌ | **Next.js 场景最优解** |
| R-S5 | `__NEXT_DATA__` 解析 | ❌ | Go（HTML 解析） | ⚠️ 当前页 | ✅ | ❌ | Pages Router 补充 |
| R-B1 | history 补丁 + 跳转监控 | ✅ | Rod / Playwright-go | ✅ | ✅ | ⚠️ | **推荐主力（浏览器）** |
| R-B2 | BFS 点击遍历 | ✅ | Rod / Playwright-go | ✅ | ✅ | ⚠️ | 通用兜底，不依赖框架 |
| R-B3 | Fiber 树遍历找 router | ✅ | Rod | ⚠️ | ⚠️ | ❌ | 不稳定，不建议当主方案 |
| R-B4 | Next.js `_rsc` 请求解析 | ✅ | Rod + CDP | ✅ | ✅ | — | App Router 专项 |
| R-B5 | CDP Network 原生抓流 | ✅ | Rod | — | ✅ | — | 接口测绘主力 |

---

## 4. 普通爬虫方案（无浏览器）

### 4.1 前置：JS 资产采集（同 Vue）

1. 拉入口页 HTML → 解析 `<script src>` / `<link rel=preload>`；
2. 递归解析 webpack chunk 映射（`__webpack_require__.u`、`webpackChunk` 数组）与 vite 的 `import()`；
3. 落盘全部 js，进入静态扫描；
4. Next.js 站点额外抓：`/_next/static/...` 下所有 js 与 manifest。

### 4.2 方案 R-S1：JSHunter（首选，纯 Go）

- 纯 Go、无 Node、无浏览器；内置 webpack/vite chunk 解析，支持 React Router 路由识别；
- 命令形态：

```bash
jshunter -u https://target.example.com/static/js/main.abc123.js -G
```

- 输出需自己做后处理（React 的路由识别假阳性比 Vue 更高，常见噪音是普通字符串 `'/api/xxx'`）：

**降噪规则（必须做）**

1. 只保留以 `/` 开头、不含 `://`、不含 `.js/.css/.png` 后缀的字符串；
2. 过滤明显接口前缀：`/api/`、`/rest/`、`/graphql`（除非同时出现在 `path:` 上下文）；
3. 过滤静态资源目录：`/static/`、`/assets/`、`/_next/`；
4. 优先采信上下文命中：`path:`、`pathname:`、`to=`、`url:`、`<Route`；
5. 统计频次：同一 path 在多个 chunk 出现 → 置信度提升。

### 4.3 方案 R-S2：Go 原生 AST visitor（goja/parser）+ esbuild 预处理（工程上最实用）

React 路由特征比 Vue 更分散，**用 AST 精确匹配上下文比正则靠谱得多**。

⚠️ 前置纠正：**esbuild 的 Go API 只提供 Build / Transform，不暴露 AST**（语法树在 `internal/` 包内）。AST 底座请用 `github.com/dop251/goja` 的 `parser` / `ast`；esbuild 只用 `Transform` 把 TS/JSX/新语法降级成 goja 能解析的形态。

```go
// 1) esbuild.Transform：TS/JSX → ES2018 纯 JS（不带 AST）
// 2) goja/parser.ParseFile → ast，再用 ast.Walk 写 visitor
// 命中点 1：对象字面量属性 key == "path"，value 为 string
//   { path: '/admin/user', element: <User/> }
// 命中点 2：JSX 属性 path="..." 或 to="..."
//   <Route path="/login" ...>  /  <Link to="/dashboard">
//   注意：JSX 已被 esbuild 转成 React.createElement(...)，属性在 CallExpression 的实参对象里
// 命中点 3：调用表达式 createBrowserRouter / createHashRouter / createMemoryRouter
//   实参第一个元素是数组 → 递归取 path
// 命中点 4：字符串常量池
//   const ROUTE_USER = '/user/detail'
//   → 先收集常量表，再做「变量→字面量」替换（一层即可，多层级联可迭代）
```

**实现要点**

1. 每个 bundle 解析一遍，输出「候选 path + 文件 + 行号 + 命中类型」；
2. 常量表替换只做 1–2 层，避免无限递归；
3. `createBrowserRouter` 的参数常被拆到单独文件（`routes.tsx`）→ 需要跨 chunk 汇总；
4. Next.js 的 `app/` 路由**不会有 `path` 字面量**，走 R-S4。

### 4.4 方案 R-S3：webcrack 反混淆（混淆站点）

- webcrack 拆 webpack bundle、还原变量名，再在还原后的源码里扫 `path`；
- 集成同 Vue 文档：**SEA 打包单二进制**为最优，其次内置 HTTP 服务，Wasm 移植不推荐；
- 判断何时启用：源码出现 `_0x` 长变量、`atob`/自定义解码、`switch-case` 控制流扁平化、大量 `eval`。

### 4.5 方案 R-S4：Next.js 构建清单直读（Next.js 场景最优解）

Next.js 是 **React 生态里最好解的一类**，因为它的路由是**文件系统路由 + 构建期生成清单**。

#### Pages Router（`pages/`）产物

| 文件 | 位置 | 内容 |
|---|---|---|
| `build-manifest.json` | `/_next/static/<buildId>/_buildManifest.js` 或站点根 | 页面 → chunk 映射 |
| `pages-manifest.json` | 构建产物（生产环境常不可直读） | 页面 path 列表 |
| `_buildManifest.js` | 可 HTTP 直取 | 内含全部页面 path 的 JS 对象 |

`_buildManifest.js` 形如：

```js
self.__BUILD_MANIFEST = {
  "/": ["static/chunks/pages/index.js"],
  "/about": ["static/chunks/pages/about.js"],
  "/blog/[slug]": ["static/chunks/pages/blog/[slug].js"],
  "/404": ["static/chunks/pages/404.js"],
  __rewrites: { beforeFiles: [], afterFiles: [], fallback: [] }
};
self.__BUILD_MANIFEST_CB && self.__BUILD_MANIFEST_CB()
```

> **直接 GET 这个 js，用轻量 JS 对象解析（goja 求值最合适）或正则取 key**，即可拿到接近 100% 的页面路由全集。这是全流程里性价比最高的一步。

```go
// goja 求值 _buildManifest.js 拿 key 集合（比正则稳）
vm := goja.New()
vm.Set("self", map[string]any{})
vm.Set("__rewrites", map[string]any{})
_, err := vm.RunString(manifestJS)
obj := vm.Get("self").ToObject(vm).Get("__BUILD_MANIFEST").ToObject(vm)
for _, k := range obj.Keys() { /* 过滤 __rewrites / sortedPages 等非路由 key */ }
```

#### App Router（`app/`）产物

| 文件 | 用途 |
|---|---|
| `app-build-manifest.json` | app 路由 → chunk 映射 |
| `routes-manifest.json` | 静态路由、动态路由、重定向、rewrite 全表 |
| `prerender-manifest.json` | 已预渲染的路径 |

> 生产环境这些文件通常在服务端，不一定能 HTTP 直取；能直取则**优先直读 `app-build-manifest.json` + `routes-manifest.json`**，比任何扫描都准。

### 4.6 方案 R-S5：`__NEXT_DATA__` 解析

Pages Router 的页面会带：

```html
<script id="__NEXT_DATA__" type="application/json">{"props":{...},"page":"/blog/[slug]","query":{...},"buildId":"..."}</script>
```

- 只能告诉你**当前页**的路由与 buildId，不能给全集；
- 但有额外价值：`page` 字段给出当前路由模式，`query` 给出参数，可用于路由↔参数建模；
- `buildId` 是后续拼出 `_buildManifest.js` URL 的关键（`/_next/static/<buildId>/_buildManifest.js`）。

### 4.7 普通爬虫的能力边界

无浏览器**确定拿不到**：

1. 路由由接口动态生成（形态 G）；
2. `createBrowserRouter` 参数在运行时才构造（如 `routes = buildRoutes(api)`）；
3. 守卫逻辑内容与绕过（L3）；
4. 未被 `<Route>` 声明、靠 `history.pushState` 直接跳转的路径（除非源码里有字面量）；
5. Next.js 生产环境的构建清单（若服务端不暴露）。

---

## 5. 浏览器爬虫方案

### 5.1 后端选型（同 Vue 文档第 5.1 节）

Chrome for Testing（`--headless=new`）为主力；强反爬换 CloakBrowser；需要 BiDi/独立指纹池换 Camoufox（Playwright-go）。Obscura / Moli 可用（不依赖扩展），但轻量后端不建议跑复杂 React SPA（内存与 CDP 交互频度高）。

### 5.2 方案 R-B1：history 补丁 + 跳转监控（推荐主力）

**这是 React 场景最可靠的路由发现手段**，因为它不依赖框架内部结构。

#### 前置注入（必须在页面脚本执行前）

用 CDP 的 `Page.addScriptToEvaluateOnNewDocument`（Rod 的 `page.EvalOnNewDocument`）注入：

```js
(() => {
  window.__seen = window.__seen || [];
  const push = (p) => {
    try {
      if (!p) return;
      const u = new URL(p, location.origin);
      if (!window.__seen.includes(u.pathname)) window.__seen.push(u.pathname);
    } catch (e) {}
  };
  const _push = history.pushState;
  const _replace = history.replaceState;
  history.pushState = function (s, t, url) { const r = _push.apply(this, arguments); push(url); return r; };
  history.replaceState = function (s, t, url) { const r = _replace.apply(this, arguments); push(url); return r; };
  window.addEventListener('popstate', () => push(location.pathname));
  window.addEventListener('hashchange', () => push(location.hash.replace('#', '') || '/'));
  // 首次记录
  push(location.pathname);
})()
```

#### 读取（Go 侧）

```go
seen, _ := page.Eval(`window.__seen`)
// seen.Value -> []string，转 []Route{Source:"browser-history"}
```

**优点**：不碰 Fiber、不碰内部 API、框架无关（React/Vue/自研都能用）。
**局限**：只能拿到"实际跳过的"路径，**没有入口的路由抓不到** → 必须配合 BFS。

### 5.3 方案 R-B2：BFS 点击遍历（通用兜底，与 B1 组合）

```text
1. 注入 history 补丁（5.2）
2. 拉取当前页所有可交互候选（a[href]、button、[role=menuitem]、tab 类元素）
3. 逐个点击 → 等待一跳（WaitStable，800ms~2s）
4. 收集 __seen 新增 path，对每个新 path 递归（BFS，深度上限 3~5）
5. 收敛后输出：可达路由集合 + 每次跳转触发的接口（配 CDP Network）
```

**关键实现细节**

1. 元素定位用「稳定的 CSS 选择器 + 文本」双要素，避免点击到动态 id；
2. 每次点击前 `page.WaitStable`，点击后要处理可能被 Modal 遮挡（按 ESC 或点遮罩关闭）；
3. 同一 path 只展开一次，避免环；
4. 点击失败不能中断整条链路，记录失败并继续（React 大量元素点击无跳转，如按钮只弹窗）；
5. **必须做 referer/back 回退**：点击后如果不跳转，需要恢复原页面再点下一个。

### 5.4 方案 R-B3：Fiber 树遍历找 router（不推荐当主方案）

React DevTools 的 Fiber 存在于 DOM 节点的 `__reactFiber$xxx` / `__reactProps$xxx` 属性上，可以尝试：

```js
// 找到根 fiber，向下遍历，识别 Router 相关的 context / state
// 命中特征：type.name 含 'Router'/'Routes'/'Route'，或 props 里含 routes/path
```

**为何不推荐**：
1. 属性名带随机后缀（`__reactFiber$abc123`），每次构建都变，要靠前缀扫描；
2. Fiber 结构（`child`/`sibling`/`return`）随 React 版本变化；
3. `createBrowserRouter` 模式下路由表在 Router 实例里，不一定进 Fiber 可见 props；
4. 生产构建 minify 后 `type.name` 被压缩，特征匹配失效。

> 结论：**只在"必须拿到集中路由表、且 BFS 也不够"时作为补强**，且必须做版本探测与降级。

### 5.5 方案 R-B4：Next.js App Router 的 `_rsc` 请求解析

App Router 页面导航时，浏览器会带 `?_rsc=xxx` 的请求头 `RSC: 1` 去拉 RSC payload，返回包含当前路由段信息的流式数据。

- 用 CDP 监听请求：过滤 URL 含 `_rsc=` 的；
- 从响应体里正则提取路由段（`/app/...`、`"pathname":"..."`）；
- 能拿到 App Router 的**实际可达路由**与页面数据形态，对 L2（接口）也有帮助。
- 补充：`/_next/static/chunks/app/**` 的 chunk 路径本身就编码了路由目录结构（`app/(marketing)/about/page.js`），对 chunk 路径做规则化还原即可得到路由树 —— 这是 App Router 无浏览器侧最有效的替代。

### 5.6 方案 R-B5：CDP Network 抓流（接口测绘）

同 Vue 文档 5.5：只读监听 `Network.*` 事件，不需要 goproxy、不需要 MITM 证书；WebSocket/SSE 仅元信息；如需 100% 完整流量再退回 goproxy MITM。

---

## 6. 推荐：混合方案（普通 + 浏览器）

### 6.1 三层流程

```text
第一层（无浏览器，首选按"框架类型"分流）
  ├─ Next.js Pages Router → 直读 __NEXT_DATA__ 的 buildId → 取 _buildManifest.js → goja 求值拿全集
  ├─ Next.js App Router   → 尝试 routes-manifest.json / app-build-manifest.json；
  │                          失败则按 /_next/static/chunks/app/** 的目录结构还原
  └─ 其它 React 项目      → JSHunter + goja/parser AST visitor（对象/JSX 双匹配 + 常量替换）
        ↓：候选路由全集 + 降噪结果

第二层（浏览器补真）
  仅对「静态 0 条 / 疑似动态路由 / 需要登录态 / 需 L3」的目标启动无头 Chrome：
      history 补丁 + BFS 点击遍历 → 真实可达集合
        ↓：真实路由（含未在源码里出现字面量的路径）

第三层（接口与校验）
  CDP Network 抓流 → 路由 ↔ 接口映射
  与第一层全集做差集 → 人工/规则复核
```

### 6.2 触发策略

| 条件 | 动作 |
|---|---|
| 识别到 Next.js Pages Router | 直接走 manifest，**跳过 JS 扫描**（更快更准） |
| 识别到 Next.js App Router | 先试 manifest，再试 chunk 路径还原 |
| 静态命中 ≥ 5 条且带 `path:` 上下文 | 跳过浏览器，直接进 BFS 轻校验 |
| 静态 0 条 | 直接上浏览器 |
| 存在混淆特征 | webcrack → 浏览器 |
| 存在登录页跳转 | 必须浏览器，携带登录态 |
| 静态全集 vs BFS 可达差集过大 | 浏览器复采 + 人工复核 |

### 6.3 数据模型（三源共用）

```go
type Route struct {
    Path       string         `json:"path"`
    Kind       string         `json:"kind"`       // static-route | jsx-route | manifest | dynamic-segment
    Params     []string       `json:"params,omitempty"` // :id / [slug] / [...all]
    Source     string         `json:"source"`     // js-scan | next-manifest | browser-history | bfs
    SessionID  string         `json:"session_id"`
    Confidence float64        `json:"confidence"`
    Chunk      string         `json:"chunk,omitempty"`
    Method     string         `json:"method,omitempty"` // 可选：跳转方式 push/replace/hash
}
```

### 6.4 归一化与去重

1. 三种参数语法必须统一：`:id`（React Router）、`[slug]`（Next 动态段）、`[...all]`（catch-all）、`*`（通配）；
2. `[...all]` 与 `*` 归一为 `/*`，但保留原始写法到 `Params`；
3. 去掉 Next.js 内部路径：`/_next/`、`/404`、`/500`、`/_error`（可选保留、默认过滤）；
4. 去掉 query 与 hash，单独字段保存；
5. 同一 path 多源命中 → `Confidence` 加权重（manifest 1.0、静态 0.8、BFS 0.9），用于排序与人工复核优先级。

---

## 7. 代码骨架（Go）

```go
// ===== 静态层 =====
type StaticScanner interface {
    CrawlJS(ctx context.Context, entry string) ([]JSBlob, error)
    ExtractRoutes(ctx context.Context, blobs []JSBlob) ([]Route, error)
}

// NextManifestScanner 直读 Next.js 产物
type NextManifestScanner struct{ HTTPClient *http.Client }

// EsbuildASTScanner 自研 AST 扫描（对象字面量 + JSX 属性 + 常量替换）
type EsbuildASTScanner struct{ MaxConstDepth int }

// ===== 运行时层 =====
type RuntimeExtractor interface {
    // 注入 history 补丁 → BFS 点击 → 返回可达路由 + 抓到的接口
    Extract(ctx context.Context, target string, cookie string, maxDepth int) ([]Route, []Endpoint, error)
    Close() error
}

type RodBFSExtractor struct {
    BrowserBin  string
    UserDataDir string
    WaitStable  time.Duration
    MaxDepth    int
}
```

**实现要点**

1. `page.EvalOnNewDocument` 注入 history 补丁，**必须在 Navigate 之前**，否则漏掉首次跳转；
2. BFS 必须有「访问集合 + 深度 + 总预算（URL 数上限）」三重上限，防爆；
3. 每一次点击失败都记录元素特征与失败原因，便于后续规则调优；
4. 每个会话独立 `--user-data-dir`，结束销毁，防僵尸进程；
5. 并发上限由 SessionPool 控制（参考 go-browser 的健康巡检思路）。

---

## 8. 常见坑清单

| 坑 | 现象 | 处理 |
|---|---|---|
| 假阳性 | 扫出一堆 `/api/xxx`、`.js` 路径 | 降噪规则（4.2 节五条） |
| JSX 路径无 `path` 字面量 | 只有 `<Route path={P}>` | 做常量表替换 |
| 路由被拆到独立文件 | `routes.tsx` 在另一个 chunk | 跨 chunk 汇总后再匹配 |
| 只有当前页 | 只拿到一个 path | 迭代读 `__seen`，不能只读一次 |
| 注入时机晚 | BFS 前几次跳转没记录 | `EvalOnNewDocument` 前置注入 |
| SPA 点击无反应 | 点击按钮只弹窗 | 判断 path 是否变化，不变则回退重试 |
| Next 生产隐藏 manifest | 直读 404 | 退回 JS 扫描 + chunk 路径还原 |
| `__NEXT_DATA__` 缺失 | 已迁移到 App Router | 转 R-B4 的 `_rsc` 方案 |
| 无头指纹被拦 | 403 / 挑战页 | CloakBrowser 或调 stealth 参数 |
| Fiber 特征失效 | `__reactFiber$` 后缀变化 / type.name 被压缩 | 不做主方案，改 BFS |
| 无限 BFS | Query 参数导致 path 无限膨胀 | URL 归一化（去 query）+ 数量上限 |

> 归一化特别提醒：React 应用中大量 `?sort=asc`、`?page=2` 变体会被当作新路径，**必须在归一化阶段去掉 query**，否则 BFS 会爆炸。

---

## 9. 选型结论

| 场景 | 推荐方案 |
|---|---|
| 目标是 Next.js | **R-S4 构建清单直读**（Pages Router 用 `_buildManifest.js`，App Router 用 manifest / chunk 路径） |
| 一般 React 项目、批量轻量 | **R-S1 JSHunter + R-S2 goja/parser AST 降噪** |
| JS 重度混淆 | R-S3 webcrack（SEA 单二进制）→ 浏览器 |
| 需要真实可达路由 | **R-B1 history 补丁 + R-B2 BFS**（主推） |
| 需要接口测绘 | R-B5 CDP Network / 或 goproxy MITM |
| 需要绕过守卫（L3） | ⚠️ React 无统一 router，静态工具做不到；只能 BFS 携带登录态访问，或直接复用已登录会话 |
| 成本最优默认架构 | **第 6 章混合方案：按框架分流静态 → 浏览器补真 → 接口抓流** |

---

## 10. Vue 与 React 方案的关键差异对照（给实施者）

| 维度 | Vue | React |
|---|---|---|
| 静态首选 | JSHunter（一次命中率高） | 分流：Next 走 manifest，其余走 AST 双匹配 |
| 浏览器首选 | **注入脚本读 `$router`**（命中率高） | **history 补丁 + BFS**（无统一 router 可读） |
| 最好解的框架 | Vue 全系 | **Next.js**（文件系统路由 + 清单） |
| 最难解的情况 | 闭包隐藏路由 | 自研路由 + 动态构造 + 混淆 |
| L3 绕过 | VueCrack 可做（覆盖 `beforeEach`） | 无等价工具，需已登录会话 + BFS |
| 降噪压力 | 低（`createRouter` 上下文明确） | **高**（JSX 与字符串噪音多） |

---

## 11. 合规与授权

- 全部路由探取、BFS 遍历、接口抓流，**仅限自有站点或已获得书面授权的测试目标**；
- 绕过登录态、访问受保护页面属于红队测试行为，必须在授权书范围内；
- 注意请求频率与并发限制，避免对目标造成压力（生产环境尤其）；
- 若采集到个人信息，需按当地法规与授权范围处理与留存。
