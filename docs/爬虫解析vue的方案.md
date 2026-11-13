# 爬虫解析 Vue 路由的方案

> 适用范围：Go 为主栈、需要批量做 SPA 资产与路由测绘（非授权站点禁止使用）、目标站点使用 Vue 2 / Vue 3 构建。
> 本文同时覆盖两条技术路线：**普通爬虫（无浏览器、纯 JS 源码静态解析）** 与 **浏览器爬虫（无头浏览器运行时提取）**，并给出二者组合的推荐落地方式。

---

## 1. 问题定义

"爬虫解析 Vue" 要解决的其实不是一个问题，而是三个层次的问题：

| 层次 | 具体目标 | 典型产出 |
|---|---|---|
| L1 路由发现 | 找出这个站点有哪些前端路由 path | 路由清单 `/login`、`/admin/user/:id`、嵌套子路由 |
| L2 接口发现 | 路由页面背后的后端接口路径 | `/api/user/list`、`/api/order/detail` |
| L3 权限绕过 | 直接访问需要鉴权的路由（跳过 `beforeEach` 守卫、`meta.requiresAuth`） | 页面直接渲染受保护内容 |

**关键结论（先讲清楚，避免后面走弯路）：**

- **L1 / L2 可以用普通爬虫（无浏览器）拿到一部分**，取决于路由是"写死在代码里"还是"运行时才生成"。
- **L3 只有浏览器爬虫能做**，因为守卫函数 `beforeEach` 存在于运行时 router 实例上，静态源码里没法"执行"它。
- Vue 的特殊性在于：**`vue-router` 的 `router` 实例几乎总会被挂到应用上下文**（`app.config.globalProperties.$router`、组件 `this.$router`、devtools 钩子），所以**浏览器侧提取成功率远高于 React**，这是 Vue 相对"好解"的根本原因。

---

## 2. Vue 路由的几种存在形态（决定方案选择）

先判断目标属于哪一类，方案才不跑偏：

| 形态 | 代码特征 | 无浏览器能否拿到 | 浏览器能否拿到 |
|---|---|---|---|
| A. 静态路由表 | `const routes = [{ path: '/a' }]` 直接写死 | ✅ 可以 | ✅ 可以 |
| B. 路由表被 webpack/vite 分包 | 路由定义在某个 chunk 模块里，需 `__webpack_require__` 加载 | ⚠️ 需要模块还原 | ✅ 可以 |
| C. 路由在闭包内不导出 | 函数内 `createRouter({routes})`，不 return、不挂全局 | ❌ 基本拿不到 | ⚠️ 需从挂载点反推 |
| D. 路由由后端接口返回 | `const routes = await api.getRouters()` | ❌ 拿不到 | ✅ 可以（有登录态） |
| E. 懒加载路由 | `component: () => import('./views/X.vue')` | ✅ path 仍可静态提取 | ✅ 可以 |
| F. 混淆打包 | obfuscator 字符串编码、控制流扁平化 | ⚠️ 需反混淆，成功率下降 | ✅ 可以（跑起来就行） |

> 一句话记忆：**"写死在源码里的" → 普通爬虫；"运行时才出现的" → 浏览器爬虫。**

---

## 3. 方案矩阵总览

| 编号 | 方案 | 浏览器 | 技术栈 | L1 | L2 | L3 | 评价 |
|---|---|---|---|---|---|---|---|
| V-S1 | JSHunter 静态扫描 | ❌ | Go 原生，可内嵌 | ✅ | ✅ | ❌ | **推荐主力（无浏览器）** |
| V-S2 | webcrack / wakaru 反混淆 + AST | ❌ | Node（SEA 打包为单二进制） | ✅ | ⚠️ | ❌ | 混淆站点降级方案 |
| V-S3 | goja/parser（Go AST）+ 自定义 visitor | ❌ | Go 原生 | ⚠️ | ⚠️ | ❌ | 轻量自研，可控但上限低 |
| V-S4 | goja 局部求值 | ❌ | Go | ⚠️ | ❌ | ❌ | 只做"候选片段求值"，别当主方案 |
| V-B1 | 无头 Chrome 注入脚本提取 `$router` | ✅ | Rod / Playwright-go | ✅ | ✅ | ✅ | **推荐主力（浏览器）** |
| V-B2 | 加载 VueCrack 扩展 | ✅ | Chrome `--load-extension` | ✅ | ✅ | ✅ | 开箱即用，免维护脚本 |
| V-B3 | BFS 点击遍历 + 跳转监控 | ✅ | Rod / Playwright-go | ✅ | ✅ | ⚠️ | 不依赖框架特征，通用兜底 |
| V-B4 | CDP Network 原生抓流 | ✅ | Rod | — | ✅ | — | 接口测绘主力，不依赖代理 |

---

## 4. 普通爬虫方案（无浏览器）

### 4.1 前置：先把 JS 资产拿全

无浏览器的前提是**你已经拿到完整的 JS 文件**。采集入口：

1. 对目标站点首页/入口页发普通 HTTP 请求，解析 HTML 里所有 `<script src>`、`<link rel=preload>`；
2. 解析入口 JS 里的相对路径引用（webpack chunk 映射表、`__webpack_require__.u`、vite 的 `import("./xxx.js")`）；
3. 递归下载 chunk，直到依赖闭包收敛；
4. 同时把 `*.js`、`*.mjs`、SFC 相关资源一并落盘，供后续 AST 扫描。

> 注意：这一步只解决"拿到代码"，**不解决"看懂代码"**，更没有解决"运行时实例"。

### 4.2 方案 V-S1：JSHunter（首选，纯 Go）

- 仓库：`https://github.com/.../JSHunter`（Go 实现，无需 Node、无需浏览器）
- 能力：webpack 4/5、vite chunk 解析，识别 VueRouter / React Router 的路由定义；`-G` 参数开启路由表恢复
- 用法（子进程形态）：

```bash
jshunter -u https://target.example.com/assets/index-abc123.js -G
```

- 典型输出（结构化路由）：

```text
[ROUTE] /login
[ROUTE] /dashboard
[ROUTE] /admin/user/:id
```

- Go 集成建议：
  - 以**子进程**方式调用，stdout 解析成行文本，统一转换成内部 `Route` 结构；
  - 或用 `go:embed` 把二进制打包进主程序，运行时释放到临时目录执行，做到单文件分发；
  - 加超时控制（`exec.CommandContext`）与并发信号量（同时最多 N 个解析进程）。

**优点**：零外部依赖、可直接进 Go 项目、单二进制分发。
**短板**：反混淆能力弱于 webcrack；重度混淆或闭包隐藏路由的站点识别率下降。

### 4.3 方案 V-S2：webcrack 反混淆（混淆站点专用）

- webcrack 能把 webpack bundle 拆回原始模块、还原可读变量名，之后在拆出的源码里定位 `routes` 数组。
- 集成难点是 Node 依赖，三种处理方式（按推荐排序）：
  1. **SEA 打包成单二进制**（Node ≥ 20 官方能力）：`webcrack → esbuild 打包为单 js → node --experimental-sea-config → 生成 webcrack-bin`。目标机器无需 Node，Go 侧只做子进程调用。
  2. **内置微型 HTTP 服务**：一次性启动 Node 服务，Go 用 POST 传 JS 文本、回 JSON，进程复用，适合高并发批处理；代价是部署仍需 Node（可与方式 1 组合）。
  3. **Wasm 移植**：不推荐。webcrack 重度依赖 Node 的 fs/path/模块系统，完整移植到 Wasm 成本极高、成功率低。

### 4.4 方案 V-S3：Go 原生 AST + 自定义 visitor（goja/parser）

这是"想纯 Go、又不想只靠正则"的中间路线。

⚠️ 先纠正一个常见误解：**esbuild 虽然是 Go 写的，但它的 Go API 只提供 Build / Transform，不暴露 AST**（语法树在 `internal/` 内部包里）。
所以 AST visitor 的正确底座是 **`github.com/dop251/goja` 的 `parser` + `ast` 子包**（公开包，可 `ast.Walk`）。

- 解析：`parser.ParseFile(nil, "x.js", src, 0)` 得到 AST；复杂新语法先用 esbuild `Transform` 降到 ES2018 再解析；
- 自定义 visitor 匹配 Vue 路由特征：

```go
// 伪代码：ast.Walk 遍历，命中路由特征
// 1) CallExpression: callee 为 createRouter / new VueRouter
// 2) ObjectProperty key == "path"，value 为 StringLiteral
// 3) 嵌套识别 children 数组
// 4) 识别 component: () => import("...") 的懒加载映射
```

- 产出：`path` 字面量 + `name` + `meta` + `component` chunk 名。
- 局限：**没有变量追踪与跨模块求值**，`const P = '/admin'; routes=[{path:P}]` 这类间接引用会漏；`webpack_require__` 分模块定义会漏。
- 分工建议：**esbuild 只做格式化/降级转译，goja/parser 做 AST，goja 解释器做求值**，三者别混用。

### 4.5 方案 V-S4：goja 局部求值（只做补强，不做主力）

goja 是 ES 解释器，不是 AST 分析器，也不是 webpack 加载器。正确用法：

- ❌ 错误用法：把整个 bundle 丢进 goja 期望自动跑出路由 —— 没有 DOM、没有 Vue runtime、没有 `__webpack_require__`，跑不起来或跑出空值。
- ✅ 正确用法：先用 AST（V-S1 / V-S3）定位到"某个模块里的 `routes` 定义片段"，把这个片段（或整段 `router.js`）喂给 goja 求值，拿到数组/对象字面量的真实值。

```go
vm := goja.New()
vm.Set("__routes_holder", func(r []interface{}) { /* 收集 */ })
// 注入极简 require 桩，让片段能自我注册
vm.RunString(`__routes_holder([{path:"/login"},{path:"/admin"}])`)
```

> 定位：**goja 是"求值器"，不是"发现器"**。发现靠 AST 或浏览器。

### 4.6 普通爬虫的能力边界（必须写进验收口径）

无浏览器方案**确定拿不到**：

1. 由接口动态下发的路由（`await api.getRouters()`）；
2. 全程闭包内、不对外暴露的 router 与 routes；
3. `beforeEach` 守卫函数的实际内容与绕过（L3 做不到）；
4. 混淆到无法静态还原的路由字面量；
5. 运行时才拼装的 path（`/user/${id}/detail` 模板）。

---

## 5. 浏览器爬虫方案

### 5.1 浏览器后端选型（与既有爬虫架构一致）

| 后端 | 无头加载扩展 | 原生 stealth | 单文件 | 说明 |
|---|---|---|---|---|
| Chrome for Testing | ✅（必须 `--headless=new`） | ❌ | ❌ 目录包 | 兼容性与 CDP 网络事件最稳，推荐主力 |
| CloakBrowser | ✅ | ✅ 源码级 | ❌ 目录包 | 强反爬站点兜底 |
| Camoufox（Firefox） | ✅（Firefox 扩展） | ✅ | ❌ 目录包 | 走 BiDi，需 Playwright，指纹池独立 |
| Obscura | ❌ | ✅ | ✅ 单二进制 | 轻量、低内存；扩展缺失 |
| Moli | ❌ | ❌ | ✅ 单二进制 | 最轻，MITM CA 支持弱 |

> 解析 Vue 路由这一步，**只需要浏览器能跑 Vue**：Moli / Obscura 也能跑（它们没有扩展能力，但注入脚本方式不依赖扩展）。**只有走 VueCrack 扩展路线时才必须 Chrome 系。**

启动参数模板（Chrome for Testing）：

```bash
chrome --headless=new \
  --user-data-dir=./profile_sess01 \
  --no-first-run --no-default-browser-check \
  --disable-background-timer-throttling \
  --disable-gpu
```

### 5.2 方案 V-B1：注入脚本从运行时提取 `$router`（推荐主力）

思路：页面加载完成、Vue 完成挂载后，在页面上下文执行脚本，**从内存里拿 router 实例**，再读 `router.options.routes`。

#### 提取 router 实例的常见挂载点（按命中率排序）

1. `window.__VUE_DEVTOOLS_APP_RECORDS`（开启 devtools hook 的站点）
   → `app._instance.exposed.$router`
2. 根节点的 app 句柄：`document.querySelector('#app').__vue_app__`
   → `app.config.globalProperties.$router`（Vue3 部署版没删这层）
3. Vue2：`document.querySelector('#app').__vue__.$router` 或 `window.$vm.$router`
4. 组件内部主动挂全局（少数项目会 `window.router = router`）

#### 注入脚本（Vue3 优先，含 Vue2 兜底）

```js
(() => {
  function findRouter() {
    // 1) devtools records
    const recs = window.__VUE_DEVTOOLS_APP_RECORDS;
    if (recs) {
      for (const r of recs) {
        const app = r.app || (r.instance && r.instance.appContext && null);
        const ins = app && app._instance;
        const er = ins && ins.exposed && ins.exposed.$router;
        if (er) return er;
        const gp = app && app.config && app.config.globalProperties;
        if (gp && gp.$router) return gp.$router;
      }
    }
    // 2) root app handle (Vue3)
    const roots = [document.querySelector('#app'), document.querySelector('[data-v-app]')];
    for (const root of roots) {
      if (!root) continue;
      const app = root.__vue_app__;
      if (app) {
        const gp = app.config && app.config.globalProperties;
        if (gp && gp.$router) return gp.$router;
        const er = app._instance && app._instance.exposed && app._instance.exposed.$router;
        if (er) return er;
      }
      // 3) Vue2 fallback
      if (root.__vue__ && root.__vue__.$router) return root.__vue__.$router;
    }
    if (window.$vm && window.$vm.$router) return window.$vm.$router;
    if (window.router && window.router.options) return window.router;
    return null;
  }

  const router = findRouter();
  if (!router) return { ok: false, reason: 'router-not-found' };

  function joinPath(parent, cur) {
    if (!cur) return parent || '/';
    if (cur.startsWith('/')) return cur;
    if (!parent || parent === '/') return '/' + cur;
    return parent.replace(/\/$/, '') + '/' + cur;
  }

  function walk(rs, parentPath, acc) {
    for (const r of rs || []) {
      const full = joinPath(parentPath, r.path);
      acc.push({
        path: full,
        name: r.name ?? null,
        meta: r.meta ?? null,
        hasChildren: !!(r.children && r.children.length),
      });
      if (r.children) walk(r.children, full, acc);
    }
    return acc;
  }

  const list = walk(router.options && router.options.routes, '', []);
  return {
    ok: true,
    count: list.length,
    routes: list,
    mode: router.options && router.options.history && router.options.history.base ? 'hash/history' : 'unknown',
  };
})()
```

#### Go（Rod）落地流程

```go
// 1. 启动浏览器（Chrome for Testing, --headless=new）
// 2. page.Navigate(url); page.WaitLoad()
// 3. 轮询 evaluate，直到 ok 或超时（Vue 常异步初始化）
deadline := time.Now().Add(15 * time.Second)
for time.Now().Before(deadline) {
    res, err := page.Eval(`...上面那段 IIFE 字符串...`)
    if err == nil && res.Value.Get("ok").Bool() {
        // 解析 routes 数组 -> []Route
        break
    }
    time.Sleep(500 * time.Millisecond)
}
```

> **时序是最大的坑**：`load` 事件 ≠ Vue 已挂载。必须**轮询探测**，不要 `WaitLoad` 后立刻取一次。

#### 动态路由（接口下发）的处理

如果目标站点路由来自后端（形态 D）：

1. 先完成登录流程，确保 cookie / token 就绪（`page` 里跑一次登录，或注入已有 cookie）；
2. 再访问首页，让前端真正请求 `/api/.../routes`；
3. 等 router 就绪后再提取 —— 此时 `router.options.routes` 已包含动态路由；
4. 可选：同时抓该次路由请求的响应体，作为独立路由清单对照。

> 这正是**静态方案永远拿不到、而浏览器方案天然拿到**的核心场景 —— 也是"动态路由站点必须上浏览器"的原因。

### 5.3 方案 V-B2：加载 VueCrack 扩展

- 原理：MV3 扩展在页面注入脚本，直接读取运行时 `$router`、导出路由树，并**覆盖 `beforeEach` / `meta` 鉴权标记**绕过前端守卫。
- 启动：`--headless=new --load-extension=./vuecrack`，页面加载后从扩展写入的全局变量读取结果。
- 优点：零脚本维护、开箱即用、推进到 L3（绕过守卫）。
- 坑：MV3 的 service worker 有隔离，通信比 MV2 麻烦；部分站点 CSP 会阻止扩展注入；扩展自身也会因为 router 挂载点变化而失效，需版本跟进。

### 5.4 方案 V-B3：BFS 点击遍历 + 跳转监控（通用兜底）

不依赖任何框架特征，任何 Vue 项目都能跑：

1. 用 CDP 在页面早期注入 `history.pushState` / `replaceState` / `hashchange` / `popstate` 补丁，把每次跳转的 `location.pathname` 记进 `window.__seen_paths`；
2. 解析页面可点击元素（菜单、tab、链接），逐个点击；
3. 每次点击后等待一跳（`WaitStable` 或固定短延时），收集新出现的 path；
4. 对新 path 递归（BFS），直到收敛或深度上限；
5. 产出：实际可达的路由集合（**没有入口的路由抓不到**，这是天然盲区）。

> 这条路的产出「路由」天然是**可达集合**，而不是「定义集合」，两者互补：静态提取给"全集"，BFS 给"真实可达"，交叉验证能显著降低误报。

### 5.5 方案 V-B4：CDP Network 原生抓流（接口测绘）

- 在无头 Chrome 里挂 CDP，监听 `Network.requestWillBeSent` / `responseReceived` / `loadingFinished`，**只读不篡改**；
- 用途：路由页面逐个打开时，把该页触发的接口全部抓下，形成「路由 → 接口」映射（L2）；
- **不需要前置 goproxy、不需要 MITM 证书**，架构最简；
- 局限：WebSocket / SSE 只拿握手元信息；Service Worker 内请求偶发丢事件（Chrome 相对最稳）。

> 若目标站点对 CDP 抓流敏感，或需要 100% 完整流量，退回「goproxy 前置 MITM」方案，代价是多一个进程与证书信任。

---

## 6. 推荐：混合方案（普通 + 浏览器）

这是工程上投入产出最好的组合，也是建议落地的默认架构。

### 6.1 三层流程

```text
第一层（廉价、并行、无浏览器）
  HTTP 拉入口页 → 递归拉 chunk → JSHunter / goja-parser 静态扫描
        ↓ 得到：候选路由全集（字面量）+ 接口路径候选

第二层（精准校验，按需触发）
  仅对「第一层结果为空 / 疑似动态站点 / 命中混淆特征」的目标，
  启动无头 Chrome → 注入脚本提取 $router → 比对静态结果
        ↓ 得到：真实路由（含接口下发的动态路由）

第三层（补全与验证）
  BFS 点击遍历 + CDP Network 抓流
        ↓ 得到：可达路由集合 + 路由↔接口映射
```

### 6.2 触发策略（省钱的关键）

| 条件 | 动作 |
|---|---|
| 静态已拿到 ≥ 5 条路由且页面正常 | 跳过浏览器，节省资源 |
| 静态 0 条路由 | 直接升级浏览器 |
| JS 混淆特征（`_0x` 长变量、`eval(` 密集） | 先 webcrack，再浏览器 |
| 页面含登录跳转 | 必须浏览器（需登录态才能拿动态路由） |
| 静态与 BFS 可达集合差集大 | 浏览器复采，人工复核 |

### 6.3 数据模型（两层共用）

```go
type Route struct {
    Path       string            `json:"path"`        // 归一化后的完整路径
    Name       string            `json:"name,omitempty"`
    Meta       map[string]any    `json:"meta,omitempty"`
    Source     string            `json:"source"`      // static | browser | bfs
    SessionID  string            `json:"session_id"`
    Confidence float64           `json:"confidence"`  // 交叉验证得分
    Chunk      string            `json:"chunk,omitempty"` // 静态提取时的归属 chunk
}
```

> `Source` + `Confidence` 两个字段决定了这条路走得通：静态给全集、浏览器给真相、BFS 给可达，三源交叉合并去重。

### 6.4 归一化与去重规则

1. path 统一补前导 `/`，去重复斜杠、去尾部斜杠（根路径除外）；
2. `:id` / `:id(\d+)` / `*` 这类参数段保留原样，**不要展开**，作为同一路由的同一 key；
3. hash 模式路由（`/#/user`）统一拆成 `path` 字段，标记 `mode=hash`；
4. 只保留同 host 的 path，跨域绝对 URL 单独归入外部链接池。

---

## 7. 代码骨架（Go，可粘合进现有爬虫）

```go
// ===== 普通爬虫：静态扫描层 =====
type StaticScanner interface {
    CrawlJS(ctx context.Context, entryURL string) ([]string, error) // 返回全部 js 文件内容或路径
    ExtractRoutes(ctx context.Context, jsBlobs []JSBlob) ([]Route, error)
}

// JSHunterScanner 子进程实现
type JSHunterScanner struct{ BinPath string; Concurrency int }

// ===== 浏览器爬虫：运行时提取层 =====
type RuntimeExtractor interface {
    Extract(ctx context.Context, targetURL string, cookie string) ([]Route, error)
    Close() error
}

// RodRuntimeExtractor（Chrome for Testing + headless=new + 注入脚本轮询）
type RodRuntimeExtractor struct {
    BrowserBin  string
    UserDataDir string
    Timeout     time.Duration
    InjectScript string // 见 5.2 注入脚本
}
```

**关键实现要点**

1. 每个浏览器会话独立 `--user-data-dir`，一对一绑定代理/任务，用完销毁；
2. 注入脚本用 `page.Eval` 轮询，间隔 500ms，总超时 10–15s；
3. 脚本执行失败要区分「router 未就绪」与「router 不存在」，前者重试，后者直接判静态；
4. 会话结束务必 `browser.Close()` 并清理 profile 目录，防止僵尸进程与磁盘膨胀。

---

## 8. 常见坑清单

| 坑 | 现象 | 处理 |
|---|---|---|
| 时序 | `load` 后立即取路由，返回 0 | 轮询探测，带超时；必要时额外等 3s |
| 懒加载 | 深层路由组件未加载，但 path 在表里 | 只取 `options.routes`，不要试图展开组件 |
| 只取当前路由 | 只拿到当前页 path | 必须读 `options.routes` 整表，不要读 `currentRoute` |
| vue-router 4 惰性 | `router.options.routes` 为空 | 检查 `router.getRoutes()`（已扁平化，含全部） |
| devtools 被关闭 | `__VUE_DEVTOOLS_APP_RECORDS` 不存在 | 换 `__vue_app__` / `__vue__` 挂载点 |
| production 构建 | `config.globalProperties` 被裁剪 | 优先 devtools 钩子，其次组件实例遍历 |
| 无头指纹 | 页面直接 403 / 挑战页 | 换 CloakBrowser，或补 stealth 参数 |
| MV3 扩展 | 加载成功但取不到结果 | 改用自注入脚本（V-B1），扩展只做补强 |
| CSP 阻止注入 | `eval` 类注入被拦截 | 用 `Page.addScriptToEvaluateOnNewDocument` 前置注入 |

> `router.getRoutes()` 补充说明：vue-router 4 里 `options.routes` 可能只保留顶层，`getRoutes()` 返回扁平化全量（含子路由），命中为空时优先用它。

---

## 9. 选型结论

| 场景 | 推荐方案 |
|---|---|
| 批量、轻量、只看静态路由 | **V-S1 JSHunter（无浏览器）** |
| 目标 JS 重度混淆 | V-S2 webcrack（SEA 单二进制）→ 再上浏览器 |
| 需要接口测绘 | V-B4 CDP Network 抓流（无 goproxy）/ 或 goproxy MITM |
| 动态路由、需登录态、需绕过守卫 | **V-B1 注入脚本（Chrome/Cloak）**，L3 用 V-B2 VueCrack |
| 不知道框架特征、想通用兜底 | V-B3 BFS 点击遍历 |
| 成本最优的默认架构 | **第 6 章混合方案：静态 → 浏览器 → BFS** |

---

## 10. 合规与授权

本文所有"提取路由""绕过 `beforeEach` 守卫""加载渗透类扩展"的操作：

- **仅限自有站点、或已获得书面授权的测试目标**；
- 未授权站点上的路由探取与鉴权绕过，属于非法行为；
- VueCrack 等红队工具请只在授权范围与隔离环境内使用，不要在生产环境常驻加载。
