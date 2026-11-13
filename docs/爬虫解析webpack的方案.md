# 爬虫解析 webpack 的方案

> 适用范围：Go 为主栈、需要对 webpack 打包产物做资产测绘（路由、接口、模块结构、懒加载清单）；目标站点使用 webpack 4/5 构建。
> 本文覆盖 **普通爬虫（无浏览器、纯源码静态解析）** 与 **浏览器爬虫（页面运行时枚举模块）** 两条路线，并与前两份《Vue / React 路由解析》文档形成上下游关系：**webpack 解析是"看清代码"，框架解析是"读懂路由"**。

---

## 1. 为什么单独为 webpack 出一份方案

Vue/React 文档里的"静态扫描"其实都隐含了一个前置假设：**你已经能把 webpack 打包的产物拆成可分析的模块**。这一步才是真正吃力的地方：

| 障碍 | 说明 |
|---|---|
| 代码不在文件里，在模块表里 | 所有业务代码被压进 `{moduleId: function(module, exports, __webpack_require__){...}}`，不是一个文件一个函数 |
| 需要模块加载器才能跑 | 路由定义常依赖 `__webpack_require__` 互相引用，直接正则切不出来 |
| chunk 拆散 | 路由页面对应的代码在懒加载 chunk 里，入口 JS 里根本没有 |
| 混淆 | module id 变成数字、变量名 `_0x`、字符串进 array + decoder |
| 不同版本 runtime 不一样 | webpack 4 用 `webpackJsonp`，webpack 5 用 `webpackChunk*`，实现细节差异大 |

**目标产出（按价值排序）**

1. **模块清单**：moduleId → 源码/字符串，看清"有哪些页面模块"
2. **懒加载 chunk 清单**：不点页面也能知道全部 chunk 文件名
3. **路由 path / 接口 path**：交给框架层解析
4. **原始源码**（若 sourcemap 可达）→ 降维打击，前面所有工作都可以跳过

---

## 2. 先做一件事：判断它到底是不是 webpack

很多项目看着像 webpack，其实是 Vite/Rollup，方案完全不同。

| 特征 | 判断 |
|---|---|
| `webpackChunk<name>` / `webpackJsonp` / `__webpack_require__` / `__webpack_modules__` | webpack |
| `/assets/index-<hash>.js` + `/assets/*-<hash>.js`，源码里有 `import("./x.js")` 和 `__vite__mapDeps` | Vite/Rollup |
| `/_next/static/chunks/` + `__NEXT_DATA__` | Next.js（内部是 webpack，但优先走 manifest，见 React 文档） |
| `manifest.json` / `.vite/manifest.json` 可直取 | Vite，**直接读 manifest 拿全量入口与 chunk，比任何扫描都准** |
| `//# sourceMappingURL=` 且 `.map` 可 200 | 任意构建器，**优先吃 sourcemap** |

> 判断优先级建议：**sourcemap → manifest → webpack 运行时特征 → 暴力正则**。前两种命中时，后面的工作基本可以不做。

---

## 3. webpack 产物结构解剖（解析的前提）

### 3.1 入口 JS（entry chunk）结构

```js
/******/ (() => { // webpackBootstrap
/******/ 	var __webpack_modules__ = ({
/******/ 		"./src/index.js": ((module, exports, __webpack_require__) => {
/******/ 			/* 业务代码 */
/******/ 		}),
/******/ 		"./src/router/index.js": ((module, exports, __webpack_require__) => { ... }),
/******/ 	});
/******/ 	var __webpack_module_cache__ = {};
/******/ 	function __webpack_require__(moduleId) {
/******/ 		var cachedModule = __webpack_module_cache__[moduleId];
/******/ 		if (cachedModule !== undefined) return cachedModule.exports;
/******/ 		var module = __webpack_module_cache__[moduleId] = { exports: {} };
/******/ 		__webpack_modules__[moduleId](module, module.exports, __webpack_require__);
/******/ 		return module.exports;
/******/ 	}
/******/ 	(() => { var chunk = ...; __webpack_require__(...) })();
/******/ })();
```

关键点：**module id 在两代里含义不同**

| 构建模式 | module id 形态 | 价值 |
|---|---|---|
| development / namedModules / namedChunksChunk | `"./src/views/admin/UserList.vue"` | ⭐ **直接就是路径**，路由树可从目录结构反推 |
| production（默认） | `123`、`"a1b2"` 等数字/短哈希 | 只能靠内容判断 |

> 这条差别极其关键：**开发构建或开启了 namedModules 的产物，模块清单本身就是路由地图**，几乎不需要再做 AST 语义分析。

### 3.2 异步 chunk（懒加载）结构

webpack 5：

```js
(self.webpackChunk_app = self.webpackChunk_app || []).push([
  ["src_views_admin_UserList_vue"],
  {
    "./src/views/admin/UserList.vue": ((module, exports, __webpack_require__) => { ... }),
  },
  (__webpack_require__) => { /* 该 chunk 的 runtime：挂到 __webpack_require__.m */ }
]);
```

webpack 4：

```js
(window.webpackJsonp = window.webpackJsonp || []).push([
  [3],
  { "./src/views/Order.vue": function (module, exports, __webpack_require__) { ... } }
], [["chunk-vendors"]]); // 可选：依赖的 chunk
```

结构恒定为 **三元组**：

```
[  [chunkId...],  { moduleId: factory },  runtime(可选)  ]
```

这就是静态解析的唯一入口锚点。

### 3.3 chunk 文件名如何拼出来

```js
__webpack_require__.u = (chunkId) =>
  "static/js/" + chunkId + "." + {
    "src_views_admin_UserList_vue": "a1b2c3",
    123: "d4e5f6",
  }[chunkId] + ".chunk.js";
__webpack_require__.p = "https://cdn.example.com/"; // publicPath
```

- `u(chunkId)` 给文件名；`p` 给前缀；
- `webpackChunkName: "admin"` 魔法注释会变成可读 chunk 名；
- **拿到 `u` 里的映射对象 key 集合 = 全部 chunk id 集合**，不必真的去点页面。

---

## 4. 普通爬虫方案（无浏览器）

### 4.1 全流程

```text
① 拉入口页 HTML → 取所有 script src（entry chunk + runtime chunk + vendors）
② 下载全部入口 JS
③ 尝试吃 sourcemap（.map 可 200 → 直接拿 sourcesContent，流程结束）
④ 从 runtime 里提取 __webpack_require__.u / .p → 生成全量 chunk 文件名
⑤ 按文件名下载全部 chunk（不点页面也能拿懒加载代码）
⑥ 解析每个 chunk 的三元组 → 得到 moduleId → factory 源码
⑦ 输出模块清单（moduleId、文件来源、字符串、疑似 path/路由/接口）
⑧ 交给框架层（Vue/React）做路由语义提取
```

### 4.2 步骤 ④：提取 chunk 清单的三种做法（按可靠性排序）

**做法 A：goja 求值 `u`（最稳）**

把 runtime chunk 单独喂给 goja，构造最小全局环境，调用 `u(id)`。难点是需要 chunkId 列表，可以：

1. 从 `__webpack_require__.u` 的映射对象 key 直接读（AST 里就是 ObjectExpression 的 keys）；
2. 从 `__webpack_require__.O`（onChunkLoaded 的分组表）里提取；
3. 野蛮枚举 `0..500`，对 `p + u(id)` 发 HEAD 请求，200 即命中。

**做法 B：正则提取映射表（快、够用）**

```go
// 匹配：{ "src_views_admin_UserList_vue": "a1b2c3", 123: "d4e5f6" }
re := regexp.MustCompile(`\{([^{}]*?)\}\s*\[chunkId\]`)
// 内部再匹配 `"name":"hash"` 与 `123:"hash"`
```

**做法 C：build 清单**

生产环境若暴露 `asset-manifest.json`（CRA）或 `manifest.json`（Vite），直接读。

**做法 D：CDP 抓流（要有浏览器）**

打开首页，把所有 `.chunk.js` 请求记录下来 —— 但只能拿到**实际加载过**的 chunk，不完整。

> 结论：**无浏览器时优先做法 A/B；能上浏览器时 D 只作交叉校验。**

### 4.3 步骤 ⑥：模块表解析（AST 为主）

⚠️ 前置：**esbuild 的 Go API 不暴露 AST**（只给 Build / Transform），语法树在 `internal/` 包内。所以 AST 解析用 **`github.com/dop251/goja` 的 `parser` + `ast`**；esbuild 只负责把新语法降级。

用 goja/parser 解析每个 chunk：

```go
// parser.ParseFile 得到 ast.File → ast.Walk 遍历
// 目标结构识别
// ExpressionStatement
//   CallExpression
//     callee: MemberExpression  (self.webpackChunk_app).push  或  window.webpackJsonp.push
//     args[0]: ArrayExpression
//         [0] ArrayExpression | StringLiteral    -> chunkIds
//         [1] ObjectExpression                   -> 模块表 ★
//         [2] FunctionExpression/ArrowFunction   -> runtime（可忽略）
```

遍历模块表的 `ObjectExpression`：

```go
for _, prop := range obj.Properties {
    modID := prop.Key  // StringLiteral 或 NumericLiteral
    // value 必须是函数节点（FunctionExpression / ArrowFunction）
    // 也兼容 `factory: function(){}` 包装、`module.exports = ...` 形态
}
```

**必须兼容的几种变体**

| 变体 | 识别方式 |
|---|---|
| webpack 5 三元组 | `.push([[ids], {modules}, runtime])` |
| webpack 4 二元组 | `.push([[ids], {modules}])` |
| runtime 已被内联进入口 | 模块表在入口的 `__webpack_modules__ = ({...})` 里，不在 push 里 |
| `__webpack_modules__` 以变量形式声明 | 找 `var __webpack_modules__ = {...}` 的赋值右侧 |
| CommonJS 包装 | `function(module, exports, __webpack_require__)` 三参 |
| ESM 包装 | `(__webpack_require__, __webpack_exports__, __webpack_require__) => {}` 三参（`__webpack_require__.r/d` 标记） |
| 单 chunk 合并包 | 全部模块在一个大对象里，chunkId 数组为 `[]` |

### 4.4 工具选型（不要从零造轮子）

| 工具 | 语言 | 能力 | 适配建议 |
|---|---|---|---|
| **JSHunter** | Go | 拆 webpack 模块表 + 路由识别 | **首选**，可直接内嵌/子进程，零 Node |
| **webcrack** | Node | 拆 bundle、还原模块、变量重命名 | 重度混淆降级用，SEA 打包为单二进制 |
| **unbundle / wakaru** | Node | 静态拆 chunk、还原目录结构 | 需要"按模块还原成文件树"时用 |
| **goja/parser（Go）** | Go | 公开 AST（parser + ast 包），可 `ast.Walk` | **visitor 底座**，与 goja 解释器同仓库 |
| **esbuild（Go）** | Go | Build / Transform、格式化、降级转译（**无公开 AST**） | 预处理与格式化，不做 visitor |
| **restringer / deobfuscator** | Node | 反混淆（字符串数组还原） | 混淆站点专项 |

> Go 栈的推荐组合：**JSHunter 打主力 + goja/parser 写 visitor 定向提取 + esbuild 做降级格式化 + webcrack(SEA) 兜混淆**。

### 4.5 自研 visitor：从模块体里抽有价值信息

模块体是一坨压缩代码，但字符串字面量保留完好（压缩不改字符串内容）。有效提取：

```go
// 遍历 factory 函数体内的 StringLiteral / TemplateLiteral
// 分类规则：
//  1. 以 / 开头且不含 ://  → 候选路由或接口
//  2. 含 "/api/" "/v1/" "/rest/" → 接口
//  3. 含 ".chunk.js" / "static/" → 静态资源
//  4. 上下文命中：前一个节点是 ObjectProperty.key == "path"/"name"/"component"
//     → 高置信度路由
//  5. import("...") 的实参 → 懒加载目标
```

**上下文判定的实现技巧**（很关键，纯字符串匹配会爆炸）：

1. 不要只看字符串，看它的**父节点**：`Property{key: "path", value: StringLiteral}` 才采信；
2. `Property{key:"component", value: ArrowFunction{body: CallExpression import("./X")}}` → 路由↔chunk 映射；
3. `meta:{title:"用户管理", requiresAuth:true}` → 直接产出路由元信息，这在 Vue 项目里非常常见。

### 4.6 方案：goja 模拟 `__webpack_require__` 真跑模块

当 AST 提取不够（例如路由表是 `buildRoutes()` 运行时拼装），可以用 goja 把**模块加载器跑起来**。

#### 原理

你不需要完整浏览器，只需要复刻 webpack runtime 的最小环境：

```go
vm := goja.New()
g := vm.GlobalObject()
// 1. 基本全局桩
vm.Set("window", g)
vm.Set("self", g)
vm.Set("globalThis", g)
vm.Set("console", map[string]any{"log": func(...any){}, "warn": func(...any){}, "error": func(...any){}})
vm.Set("document", map[string]any{ /* 最小桩，见下 */ })
vm.Set("navigator", map[string]any{"userAgent": "node"})
vm.Set("location", map[string]any{"href": "https://example.com/", "pathname": "/", "origin": "https://example.com"})
```

```go
// 2. 自己实现模块表与 require
modules := map[string]goja.Value{}          // moduleId -> factory
cache := map[string]*goja.Object{}          // moduleId -> module 对象
var requireFn func(goja.FunctionCall) goja.Value

requireFn = func(call goja.FunctionCall) goja.Value {
    id := call.Argument(0).String()
    if m, ok := cache[id]; ok { return m.Get("exports") }
    mod := vm.NewObject()
    exports := vm.NewObject()
    _ = mod.Set("exports", exports)
    cache[id] = mod
    if f, ok := modules[id]; ok {
        // 调用 factory(module, module.exports, require)
        if fn, ok := goja.AssertFunction(f); ok {
            _, err := fn(goja.Undefined(), mod, exports, vm.ToValue(requireFn))
            if err != nil { /* 记录但继续，很多 factory 依赖 DOM 会抛错 */ }
        }
    }
    return mod.Get("exports")
}
```

```go
// 3. 把每个模块的 factory 源码注册进 goja
for id, src := range factorySources {
    v, err := vm.RunString("(" + src + ")") // 包成表达式得到函数
    if err == nil { modules[id] = v }
}
// 4. 指定入口 moduleId，跑一次
res := requireFn(goja.FunctionCall{ Arguments: []goja.Value{ vm.ToValue("./src/router/index.js") } })
```

#### 必踩的坑与对策

| 坑 | 对策 |
|---|---|
| factory 里引用 `document.querySelector` 报错 | 提供最小 DOM 桩（`querySelector` 返回带 `appendChild`/`__vue__` 的对象） |
| `import()` 动态加载不存在 | 把 `__webpack_require__.e`/`u` 实现为返回 resolved Promise 的空桩 |
| `process.env.NODE_ENV` 未定义 | 注入 `process={env:{NODE_ENV:"production"}}` |
| factory 抛异常中断整个求值 | **每个 require 独立 recover**，失败只跳过该模块 |
| 依赖 `window.__NUXT__` / 全局注入 | 按目标站点手工补桩，或用浏览器路线替代 |
| 求值副作用（改全局、发请求） | goja 里没有真实网络，天然安全；但要防死循环（设 `vm.Interrupt`） |

> goja 求值成功率：**纯数据/工具模块（路由表、常量表、API 定义）通常 80%+ 可跑通；含 UI 副作用的路由模块常需要浏览器。**

### 4.7 混淆与反混淆（obfuscator.io 类）

典型特征：

1. 全局一个字符串数组 `const _0x4a2b = ['\x2f\x61\x64\x6d\x69\x6e', ...]`
2. 一个解码函数 `function _0x1234(i, k) { ... }`
3. 所有字符串访问变成 `_0x1234(0x1f)`
4. 控制流扁平化（`while(true){ switch(_0x..) }`）

**离线还原策略（按成本递增）**

| 策略 | 做法 | 适用 |
|---|---|---|
| 直接求值解码 | 用 goja 只跑「字符串数组 + 解码函数」这段，把 `_x(i)` 全部求出来 | ⭐ 最有效也最便宜 |
| 常量折叠 | goja/ast 上把 `CallExpression{callee: decoder, args: literal}` 求值替换 | 中等混淆 |
| 专用工具 | `webcrack` / `restringer` / `deobfuscator` | 重混淆 |
| 浏览器兜底 | 页面已跑通，直接从模块体 `toString()` + `require.c` 拿真实值 | 最省事 |

**goja 求值解码函数的最小写法**

```go
// 1. 从产物里截取字符串数组 + 解码函数（AST 定位）
// 2. 拼成一段可执行的 JS：
//    var _0x4a2b = [...];  function _0x1234(i,k){...}
//    __out([]);   // 由 Go 注入
vm.Set("__out", func(vals []string) { decoded = vals })
vm.RunString(snippet)
// 3. 用映射表替换全文中的一个字面量
```

> 注意：obfuscator 的字符串数组常被**打乱 + 轮转**（`_0x4a2b` 在运行时 `push(shift())`），必须**原样跑一遍初始化代码**再取值，不能直接取数组字面量。

### 4.8 sourcemap 优先原则（务必写进流程第一判断）

```js
//# sourceMappingURL=main.abc123.js.map
```

- `.map` 可 200 且含 `sourcesContent` → **直接拿到原始 TS/Vue/JS 源码**，所有 AST/反混淆工作全部免除；
- `sources` 有路径但无 `sourcesContent` → 只有路径信息，价值仍很高（能还原目录结构 → 推导路由）；
- 只有 `mappings` → 用 `source-map` 库反查行列，成本高，一般不用。

> 实战统计：**公开发布的 SPA 里，sourcemap 泄露比例不低**；能吃到就是最大的性价比。

---

## 5. 浏览器爬虫方案（运行时枚举）

### 5.1 为什么浏览器侧更好用

页面跑起来后，webpack 已经把**完整的模块表、缓存、chunk 映射**都建好了，你可以直接读内存对象：

| 目标 | 浏览器侧取法 |
|---|---|
| 全部模块 id | `Object.keys(__webpack_require__.m)` 或 `Object.keys(cache)` |
| 已执行模块的导出 | `__webpack_require__.c[id].exports` |
| 模块源码 | `__webpack_require__.m[id].toString()` |
| chunk 文件名映射 | `__webpack_require__.u.toString()` → 提取映射表 |
| publicPath | `__webpack_require__.p` |
| 已加载 chunk | `__webpack_require__.O` 内部 / 或 CDP 抓流 |

难点只有一个：**生产环境里 `__webpack_require__` 不在全局**（被 IIFE 包住）。需要"拿到 require"的技巧。

### 5.2 拿 `__webpack_require__` 的三种办法

#### 办法 A：劫持 chunk 数组的 push（webpack 5，最通用）

webpack 5 的 chunk 全局数组叫 `webpackChunk<name>`，它在 `window` 上是可写属性，且**每个新 chunk 加载时都会调用它的 `push`，第三个参数就是该 chunk 的 runtime 回调（形参即 `__webpack_require__`）**。

因此可以**主动 push 一个空 chunk**，让 runtime 回调把自己暴露出来：

```js
(() => {
  const key = Object.keys(window).find(k => k.startsWith('webpackChunk'));
  if (!key) return { ok: false, reason: 'no-webpackChunk' };
  const ck = window[key];
  const prevPush = ck.push.bind(ck);
  ck.push = function () {
    // 转调原实现，保证 webpack 正常加载
    const r = prevPush.apply(this, arguments);
    return r;
  };
  // 关键：push 一个空模块表 + runtime 回调，回调参数就是 __webpack_require__
  const probeId = '__crawler_' + Date.now();
  ck.push([[probeId], {}, (req) => { window.__wpReq = req; }]);
  return { ok: !!window.__wpReq, chunkKey: key };
})()
```

> 注意写法细节：chunkId 建议用不会与业务冲突的字符串；push 必须在 `EvalOnNewDocument` 阶段就装好劫持，否则首个 chunk 已经 push 完毕。

#### 办法 B：webpack 4 的 `webpackJsonp`

```js
(() => {
  const keys = Object.keys(window).filter(k => k.startsWith('webpackJsonp'));
  if (!keys.length) return { ok: false };
  const arr = window[keys[0]];
  arr.push([[`__c_crawler`], {}, (req) => { window.__wpReq = req; }]);
  return { ok: !!window.__wpReq };
})()
```

#### 办法 C：从已有 chunk 数组里回溯 runtime

`window.webpackChunk<x>` 数组在每次 push 后，会**保留最后一次 push 的 runtime 参数**吗？不会。但可以从 `__webpack_require__.m` 已挂载的模块表反推：`ck` 数组里的对象元素就是模块表，这已经够用了（不需要 require 也能拿到源码）。

> 结论：**要源码，模块表就够（办法 C）；要执行能力，必须拿到 require（办法 A/B）。**

### 5.3 枚举与提取脚本（推荐成品）

```js
(() => {
  const req = window.__wpReq || window.__webpack_require__;
  if (!req) return { ok: false, reason: 'no-require' };

  const out = { ok: true, modules: [], chunkMap: {}, publicPath: req.p || '' };

  // 1) 模块清单 + 源码
  const m = req.m || {};
  for (const id of Object.keys(m)) {
    let src = '';
    try { src = Function.prototype.toString.call(m[id]); } catch (e) { src = ''; }
    // 提取字符串字面量（压缩后字符串内容仍然完整）
    const strs = (src.match(/(["'`])(?:\\.|(?!\1).)*\1/g) || []).slice(0, 200);
    out.modules.push({ id, size: src.length, strings: strs });
  }

  // 2) chunk 文件名映射（从 u 函数源码里正则取）
  try {
    const uSrc = Function.prototype.toString.call(req.u || function () {});
    const pairs = [...uSrc.matchAll(/["']?([\w\-\/\.]+)["']?\s*:\s*["']([0-9a-f]{6,})["']/g)];
    for (const p of pairs) out.chunkMap[p[1]] = p[2];
  } catch (e) {}

  // 3) 已缓存模块的导出（能直接拿到 router / routes 等运行时对象）
  out.cached = Object.keys(req.c || {});

  return out;
})()
```

**产出与用途**

| 产出 | 用途 |
|---|---|
| `modules[].id` | 若是 `./src/views/...` 形态 → **直接还原路由树** |
| `modules[].strings` | 喂给降噪规则 → 路由/接口候选 |
| `chunkMap` | 全量懒加载 chunk 清单（不点页面也能下） |
| `cached` | 已执行模块列表，决定"哪些能直接取 exports" |

### 5.4 直接取运行时对象（省掉所有字符串分析）

如果目标模块已经执行过，可以直接拿导出：

```js
const exp = req("./src/router/index.js")   // 或 req(数字id)
// Vue: exp.default?.routes / exp.router?.options.routes
// React: exp.default?.routes（createBrowserRouter 的数组）
```

> 这一步等价于 Vue 文档里的"注入脚本读 `$router`"，但更底层：**不依赖框架挂载点，直接按 moduleId 取模块导出**。Vue 项目命中率极高，React 项目次之。

### 5.5 配合 CDP 抓流（补 chunk 与接口清单）

在无头 Chrome 里挂 `Network.*` 事件，过滤：

| 过滤条件 | 得到 |
|---|---|
| URL 含 `.chunk.js` / `.js?` | 实际加载的 chunk 清单（与 `chunkMap` 交叉校验） |
| URL 含 `/api/`、`/graphql`、`/v1/` | 接口清单 |
| URL 含 `_next/static/chunks/` | Next.js 场景（走 React 文档） |

> 不需要前置 goproxy、不需要 MITM 证书；要 100% 完整流量再退 MITM 方案。

### 5.6 浏览器侧的时序要求

1. `EvalOnNewDocument` 阶段**先装好 chunk push 劫持**，否则首个 chunk 已 push 完毕、拿不到 runtime；
2. 主页面 `load` 后等待 1–3s 让首屏 chunk 全部加载；
3. 再执行 5.3 的枚举脚本；
4. 想拿"全部懒加载 chunk 的文件名"，`chunkMap` 直接从 `u` 函数读，**不需要真的触发懒加载**；
5. 触发懒加载想验证时，BFS 点击（见 Vue/React 文档）。

---

## 6. 混合方案（推荐落地架构）

### 6.1 分层

```text
L0  决定路线
    ├─ sourcemap 可 200 → 直接吃源码，结束
    ├─ Vite/Next 有 manifest → 直读 manifest，结束
    └─ 否则进入 webpack 解析
L1  无浏览器（廉价、可并发）
    入口 JS → AST 拆模块表 → 提 chunk 映射 → 递归下载全部 chunk
    → 模块清单 + 字符串降噪 → 候选路由/接口
L2  浏览器（按需）
    劫持 chunk push 拿 require → 枚举 __webpack_require__.m
    → 全量模块 id + 源码 + chunkMap
    → 对已执行模块直接读 exports（拿真实 routes）
L3  校验与补全
    BFS 点击 + CDP 抓流 → 可达路由 / 接口 ↔ 页面映射
```

### 6.2 触发策略

| 条件 | 动作 |
|---|---|
| `.map` 可访问 | 跳过 L1/L2 |
| module id 是路径形态（`./src/...`） | L1 即可完成路由还原，跳过 L2 |
| L1 产出 0 条路由 | 进 L2 |
| 存在混淆特征（`_0x`、字符串数组 + 解码函数） | 先 goja 还原字符串，再进 L2 |
| 需要 L3（绕过守卫/带登录态） | 必须 L2 + BFS |
| chunkMap 有但下载 404 | 说明 chunk 命名有 CDN 前缀/版本参数，需读 `req.p` 拼全 |

### 6.3 数据模型

```go
type Module struct {
    ID      string   `json:"id"`        // "./src/views/admin/UserList.vue" 或 "1234"
    Chunk   string   `json:"chunk"`     // 所属 chunk 文件名
    Size    int      `json:"size"`
    Strings []string `json:"strings,omitempty"`
    Exports bool     `json:"exports,omitempty"` // 浏览器侧是否已执行
}

type ChunkInfo struct {
    ID       string `json:"id"`
    Hash     string `json:"hash"`
    Filename string `json:"filename"`
    Public   string `json:"public_path"`
    Downloaded bool `json:"downloaded"`
}

type Finding struct {
    Kind  string `json:"kind"`  // route | api | asset | lazy-import
    Value string `json:"value"`
    ModuleID string `json:"module_id"`
    Source string `json:"source"` // ast-ctx | string-noise | runtime-export | cdp
    Confidence float64 `json:"confidence"`
}
```

### 6.4 归一化

1. moduleId 统一转字符串（数字 id 与字符串 id 混排时排序会乱）；
2. chunk 文件名 = `publicPath + u(chunkId)`，`publicPath` 可能是相对（`./`）或 CDN 绝对，需归一为绝对 URL；
3. 字符串降噪：去 `.js/.css/.png/.svg` 结尾、去 `://`、去纯数字、长度 < 3 丢弃；
4. 同一 Finding 多源命中 → 提升 `Confidence`（runtime-export 1.0 > ast-ctx 0.85 > string-noise 0.4）。

---

## 7. Go 代码骨架

```go
// ===== L1 静态 =====
type BundleScanner interface {
    // 下载入口 JS + 递归全部 chunk（含 sourcemap / manifest 探测）
    Fetch(ctx context.Context, entryPage string) (*BundleSet, error)
    // 拆模块表 + 提取 chunk 映射
    ParseModules(ctx context.Context, bs *BundleSet) ([]Module, []ChunkInfo, error)
    // 从模块体提取候选路由/接口（goja/parser visitor + 上下文规则）
    ExtractFindings(ctx context.Context, mods []Module) ([]Finding, error)
}

type WebpackASTScanner struct {
    MaxChunks   int
    Concurrency int
    PublicPath  string
}

// ===== L2 运行时 =====
type RuntimeWebpackInspector interface {
    // EvalOnNewDocument 装劫持 + 枚举
    Inspect(ctx context.Context, target string, cookie string) ([]Module, map[string]string, error)
    Close() error
}

type RodWebpackInspector struct {
    BrowserBin  string
    UserDataDir string
    WaitAfter   time.Duration
}
```

**实现要点**

1. **chunk 递归要有上限**（`MaxChunks`），且按 content-length 跳过超大 vendors（> 5MB 只记录不解析）；
2. AST 解析失败不致命：单个 chunk 失败记录后继续（webpack 产物常有奇怪的边界语法）；
3. `chunkMap` 里的 hash 段可能是 `[contenthash]` 之类占位，实际文件已换成真实 hash，需以真实下载结果为准；
4. 浏览器侧必须 `EvalOnNewDocument` 前置劫持，漏了首个 chunk 就只能退办法 C（只有模块表、没有 require）；
5. 每个会话独立 `--user-data-dir`，结束销毁；
6. 全部请求带 `Referer` 与基础 UA，CDN 常校验 Referer。

---

## 8. 坑清单

| 坑 | 现象 | 处理 |
|---|---|---|
| 误判为 webpack | 实际是 Vite | 先做第 2 节判断 |
| 只下入口 chunk | 业务模块全在异步 chunk | 必须解析 `u` 拿全量 chunk 清单 |
| `__webpack_require__` 拿不到 | 被 IIFE 包住、生产无全局 | 劫持 `webpackChunk*.push`（办法 A） |
| 劫持装晚了 | 首 chunk 已 push 完 | `EvalOnNewDocument` 前置 |
| module id 是数字 | 生产构建默认数字 id | 只能靠字符串内容判定，不能靠 id 推路径 |
| 反混淆取数组字面量 | obfuscator 运行时旋转数组 | 必须**执行一遍**初始化再取值 |
| goja 求值全崩 | factory 依赖 DOM/AJAX | 补桩 + 单模块 recover + 必要时切浏览器 |
| `.map` 404 | 服务端关闭 | 漏掉最优路径，退回 AST |
| chunk 下载 404 | `publicPath` 拼错/带版本参数 | 读 `req.p` 与真实请求 URL 对齐 |
| 巨型 vendors | 单个 chunk 几十 MB | 只解析不下载、或按需范围下载 |
| 循环引用 | `require` 递归死循环 | goja 侧设 `vm.Interrupt`（超时中断） |
| 字符串爆炸 | 提取出数千条候选 | 上下文规则 + 置信度排序，只保留高分 |
| chunk 名重复 | 多 entry 共用 runtime | 以 `chunkId + hash` 为主键，不以文件名 |

---

## 9. 选型结论

| 场景 | 推荐 |
|---|---|
| sourcemap 可访问 | **直接吃源码，不做解析** |
| 常规 webpack SPA、批量轻量 | **JSHunter + goja/parser 自研 visitor（L1）** |
| 需要全量 chunk 清单 | 解析 `u` 映射 / `chunkMap` 提取（L1） |
| 路由表是运行时拼装 | **goja 模拟 require（4.6）** |
| 重度混淆 | goja 求值解码函数 → 仍不够则 webcrack(SEA) |
| 生产站、需要真实 exports | **浏览器侧劫持 push 拿 require（L2）** |
| 需要接口↔页面映射 | L3 BFS + CDP 抓流 |
| 默认架构 | **第 6 章混合：sourcemap/manifest → L1 → L2 → L3** |

---

## 10. 与其他文档的衔接

```text
本文（webpack 解析）
   ↓ 产出：模块清单 / chunk 清单 / 候选 path / 运行时 exports
《爬虫解析vue的方案.md》
   ↓ 产出：Vue 路由全集 / 动态路由 / 守卫绕过
《爬虫解析React的方案.md》
   ↓ 产出：React / Next 路由全集 / 可达集合
```

- 判断目标框架后，直接跳到对应文档选方案；
- 若目标同时是 Next.js，**优先走 React 文档的 manifest 路线**，本文章的 webpack 深层解析只在 manifest 不可达时启用；
- L2 的 require 劫持代码可以直接作为 Vue/React 文档"注入脚本"的前置步骤：**先拿 require，再按 moduleId 取 router**，命中率高于依赖全局挂载点。

---

## 11. 合规与授权

- 本文全部解析、下载、运行时枚举行为，**仅限自有站点或已获得书面授权的目标**；
- 批量抓取 chunk 与遍历模块对目标有带宽与性能影响，需限速、限并发；
- 还原出的源码可能含内部接口、业务逻辑，属于敏感信息，按授权范围存储与使用；
- 涉及个人信息时，遵循当地法规与最小必要原则。
