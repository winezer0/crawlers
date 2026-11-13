# JShunter 原理分析

> 对象：[cc1a2b/JShunter](https://github.com/cc1a2b/JShunter)（Go 实现，MIT 协议，约 545 star）
> 定位：面向 JavaScript 资产的安全分析命令行工具，主打"端点发现 + 敏感数据/密钥检测"。

---

## 一、它到底在解决什么问题

传统 Web 渗透的信息收集以页面为起点：爬虫抓 HTML，从 HTML 里抽链接，再逐个请求。现代前端把这个前提打碎了。

1. **代码集中到一个 bundle**。几百 KB 的压缩代码里，接口路径、参数名、云服务配置全在里面，但人眼不可读。
2. **路由与接口是运行时拼出来的**。`baseURL + "/api/v" + version + "/user"` 这种写法，静态爬虫看不见完整的 URL。
3. **懒加载 chunk 没有任何入口链接**。Bundler 把 chunk 清单内联在入口文件里，运行时用 `__webpack_require__.u(id)` 拼出文件名再 `jsonpScriptSrc` 注入。传统爬虫拿不到这份清单，等于放弃了 90% 的攻击面。
4. **密钥常常被误判**。一段 40 字符 base64，在字符串字面量里可能是凭证，在压缩后的标识符里可能是 chunk 哈希，横跨两个 token 时则什么都不是。纯正则无法区分。

JShunter 的设计基本就是围绕这四点展开的：**先把 JS 当成代码去理解，再在理解的基础上做匹配**。

---

## 二、整体流水线

```
输入（-u / -l / -f / stdin / --har / --robots）
        │
        ▼
   抓取层：并发拉取、限流、重试、缓存、代理、UA 轮换
        │
        ▼
   词法层（jslex.go）：单遍 ECMAScript 扫描，逐字节分类
        │  产出 token 流 + span 区间表
        ▼
   结构层（structure.go）：区间索引、字面量角色、绑定名、兄弟键集合
        │  回答"这个匹配落在什么语法位置上、绑定给了谁"
        ▼
   规则层：85+ provider 正则规则 + 各类专项分析（GraphQL / Firebase / JWT / 参数）
        │
        ▼
   校验层：provider validator（校验和、结构解码、长度/字符集证明）
        │
        ▼
   评分层：置信度打分、曝光分类、误报抑制
        │
        ▼
   输出层：终端 / JSON / NDJSON / CSV / SARIF / Burp
```

关键点在于：**规则匹配不是流水线的起点，而是发生在词法与结构分析之后的判断**。这个顺序决定了它和同类工具的本质差异。

---

## 三、核心原理分层拆解

### 3.1 采集层：把"看不见的资产"变成看得见的

- **多形态输入**：单 URL、URL 列表文件、本地 JS 文件、stdin 管道，以及 Chrome DevTools 导出的 HAR 归档（`--har`）——后者能把浏览器里真实发生过的请求一次性回放成待扫列表。
- **工程化抓取**：`-t` 线程数、`--per-host` 单主机并发上限、`-R` 请求间隔毫秒、`-y` 指数退避重试、`-T` 超时、`--max-bytes` 响应体截断（默认 32MiB）、`--cache-dir` 基于 ETag 的磁盘缓存复用。
- **反侦察配合**：`-p` 支持 HTTP/HTTPS/SOCKS5 代理（Burp、Tor、SSH 隧道）、`-U` 自定义 UA 或 UA 字典轮换、`-H` 可重复的请求头、`-c` 会话 Cookie、`-k` 跳过 TLS 校验。
- **横向扩展**：`-w` 递归爬取深度、`-D` 域名限定、`-E` 扩展名过滤、`--robots` 拉取 robots.txt。
- **HTML 侧补充**：`--inline-html` 扫描内联 `<script>` 并检查 SRI/CSP，`--csp-origins` 把 CSP 放行的源直接当作候选端点输出。

### 3.2 词法层（jslex.go）：单遍字节级分类

这是整个检测引擎的地基。工具不复用浏览器引擎，而是自己实现一个单遍 ECMAScript 扫描器，把响应体的**每一个字节**归入六类之一：

| 类别 | 含义 |
|---|---|
| `ClassString` | 字符串字面量 |
| `ClassTemplate` | 模板字面量（含插值） |
| `ClassLineComment` / `ClassBlockComment` | 注释 |
| `ClassRegex` | 正则字面量 |
| `ClassCode` | 代码区（标识符、运算符等） |
| `ClassStraddle` | 跨 token 边界，不属于任何单一字面量 |

实现上只扫一遍，同时产出 token 流和一张按起点排序的 span 区间表。之所以要自己写而不直接用正则，是因为**正则引擎不知道自己在匹配什么位置**——它只能看到字符，看不到语法。

### 3.3 结构层（structure.go）：把"位置"变成"语义"

拿到区间表之后，每个正则命中都要回答三个问题。

**① 落在哪个区域？——区间二分查找**

`Classify(start, end)` 用二分查找定位起点所在 span（O(log n)），然后：

- 起点与终点在同一 span 内 → 返回该 span 的类型；
- 起点在 span 内、终点越界 → 返回 `ClassStraddle`，判定"这不是一个完整的字面量"；
- 起点在代码区且后面还有 span 开始 → 同样 `ClassStraddle`；
- 否则 `ClassCode`。

配套有 `IsLiteral()`：只有字符串与模板字面量允许承载硬编码值。于是三类命中被**直接否掉**：

- 落在正则字面量里 → 那是模式，不是值；
- 跨 token 边界 → 不是单一字面量；
- 落在代码区 → 只是标识符碎片。

**② 绑定给了谁？——LiteralRole 与 BindingName**

工具不是扫一个固定字符窗口去找 `key` 这个词，而是恢复这个字面量在程序里实际绑定的标识符或属性名。角色枚举包括：

`RoleAssignment`（赋值）、`RoleObjectValue`（对象属性值）、`RolePropertyKey`（属性键）、`RoleCallArg`（调用实参）、`RoleArrayElem`（数组元素）、`RoleReturn`（返回值）、`RoleImportSpec`（import 说明符）、`RoleMemberIndex`（成员索引）、`RoleComparison`（比较）、`RoleConcat`（拼接）、`RoleTaggedTemplate`（标签模板）。

其中 `Assignment / ObjectValue / CallArg / Return / ImportSpec` 被标记为**绑定角色（Binding）**——只有这些角色的名字才真正携带意图证据。`const stripeSecret = "..."` 和 `{contentHash: "..."}` 的含义天差地别，而形状规则（shape-only rule）现在可以强制要求存在这种绑定关系。

**③ 它的兄弟键是什么？——SiblingKeys**

结构层会记录每个对象字面量声明的属性名（`recordObjectKey`，单对象上限 64 个键），再通过 `SiblingKeys` 反查某个值所在对象的**完整键集合**。

这一步专门用来分辨"配置对象"与"真密钥"。Firebase web config、Algolia 搜索配置、Segment 埋点配置、Supabase 浏览器客户端，都是由一组特定键名构成的对象；一个孤零零的 `apiKey` 字段和一个嵌在 `{apiKey, authDomain, projectId, storageBucket, ...}` 里的 `apiKey`，风险等级完全不同。**靠邻居判断身份，而不是靠自己的长相。**

### 3.4 规则层：85+ provider 检测器

规则注册表覆盖的范围相当宽：

- **云与密钥管理**：AWS、Azure、GCP/PKCS#8、HashiCorp Vault、Terraform、Fly.io、Tailscale
- **版本控制与 CI/CD**：GitHub、GitLab、Docker Hub、Atlassian、Sentry、CircleCI、Buildkite
- **支付**：Stripe、Square、Braintree、Plaid
- **AI/LLM**：OpenAI、Anthropic、Groq、Perplexity、Replicate、OpenRouter、Fireworks、HuggingFace
- **消息与 SaaS**：Slack、Discord、Telegram、Twilio、Intercom、SendGrid、Mailgun、Notion、Airtable、Figma、Postman、Databricks、PlanetScale、Grafana、New Relic、Dropbox、RubyGems、Supabase
- **PKI 材料**：RSA/EC/DSA/OpenSSH/PGP/PKCS#8 私钥、PuTTY `.ppk`、数据库连接串

设计取舍上有一条明确原则：**无前缀的"裸哈希"形状，只有在配备结构校验器或强制上下文门槛时才会发布**，否则不进注册表。宁可少收，不制造噪声。

除密钥外还有一组专项分析：

| 能力 | 开关 | 原理 |
|---|---|---|
| 端点提取 | `-ep` | 从字符串/模板字面量中抽取 URL 与 API 路径 |
| JWT 分析 | `-x` | base64url 解出 header 的 `alg` 与 payload JSON，验证结构合法性 |
| GraphQL | `-g` | schema 探测、query 文本抽取、端点定位 |
| Firebase | `-F` | 配置对象识别 + API key + databaseURL |
| 参数发现 | `-P` / `-PU` | 隐藏表单参数、变量、配置键；后者保留完整 URL 上下文 |
| WAF 绕过特征 | `-B` | 识别绕过/规避模式的痕迹 |
| 内部端点过滤 | `-i` | 归类 localhost / 内网 / link-local 资源 |
| 链接关系 | `-L` | 全量 URL 抽取与关系映射 |

### 3.5 校验层：validator 是误报的第一道硬防线

validator 在正则命中**之后**执行，是一段针对该 provider 的一致性检查。随机字符串即使偶然长得像，也得先过下面其中一种证明才能被报出来：

| Provider | 校验方法 |
|---|---|
| AWS | 前缀族（`AKIA/ASIA/A3T…`）+ 16 字符 base32 主体 |
| Stripe | key 族（`sk/rk/pk_live/test_`）与 `whsec_` webhook 的 base62 主体 |
| GitHub | 随机主体上的 CRC32 base62 校验和验证 |
| OpenAI | 族前缀 + 长度窗口（`sk-` / `sk-proj-` / `sk-svcacct-`） |
| Slack | 连字符分段形状（数字内段 + 字母数字尾段） |
| JWT | base64url 解码后 header 含 `alg` 且 payload 为 JSON |
| Twilio | 32 位 hex 主体 + 熵门槛 |
| Azure | `AccountKey=` 的 base64 主体必须恰好解出 64 字节；AD 令牌 `…<digit>Q~…` |
| Telegram | `<8-10 位数字 id>:AA…` 切分后 base64url 密钥 + 熵门槛 |
| Intercom | base64 解码得到 `tok:` 前缀载荷 |
| Sentry | `sntrys_` 组织令牌载荷 base64 解码为带 `url` 声明的 JSON |
| Terraform | `<14>.atlasv1.<60-70>` 三段结构 |
| Square | `sq0atp-/sq0csp-/sq0idp-` 族 + 精确主体长度 + 熵 |
| Braintree | `access_token$<env>$<16 base36>$<32 hex>` 四段结构 |
| Airtable | `pat<14>.<64 hex>` 两段切分 |
| Postman | `PMAK-<24 hex>-<34 hex>` 段长 + 熵 |
| 数据库连接串 | 密码段若为模板占位、默认值或低熵，直接拒绝 |

校验通过还会给置信度 **+0.10**。这是"形状像"与"确实是"之间的分界线。

### 3.6 评分层：从先验到可解释的置信度

每个密钥类命中都会在 `[0.0, 1.0]` 内打分，起点是该规则的先验，然后按信号加减：

| 信号 | 影响 |
|---|---|
| 来源路径像 vendor/chunk 打包文件 | −0.15 |
| 周边上下文含 fixture/示例措辞 | −0.30 |
| 通过 provider 专用校验器 | +0.10 |
| 通用规则所需的上下文关键词出现 | +0.05 |
| Shannon 熵 ≥ 4.5 | +0.05 |
| 字符类多样性 ≥ 3 | +0.05 |
| 命中 vendor 噪声拒绝名单 | 评分前即丢弃 |
| 长度/熵低于规则下限 | 评分前即丢弃 |
| 该行是 `//# sourceMappingURL=` 标记 | 评分前即丢弃 |

默认阈值 `--min-confidence 0.50`，用于砍掉形态匹配的长尾；高精度筛选用 `0.80`；`--no-fp-filter` 则是调试用的裸输出。

**曝光分类（exposure）** 是另一条独立判断：有些值是发布方**本来就公开**的（Stripe publishable key、Mapbox public token、从自身 `role` claim 读取的 Supabase anon JWT、Twilio SID），这类会被标记为"公开"而非"泄露"，默认不上报，`--include-public` 才会输出。这个设计避免把正常的公开设计误报成事故。

**文件相对 surprisal** 则是较有意思的一层：用**被扫文件自身**统计出的字符转移模型，给候选字符串打一个"它有多不像这个文件里其他内容"的分数。压缩产物里混入一段人类可读的英文，或者一段与全文编码风格格格不入的串，都会因此获得更高的可疑度。参照系来自文件本身，而不是通用语料。

### 3.7 结构层的安全阀：宁可漏报不能误抑制

所有"拒绝"动作都有一个前置条件：**响应体必须被高置信地判定为 JavaScript 或 JSON**。

遇到 `.env` 文件、散文文本、裸 HTML 这类非 JS 内容时，结构引擎只提供证据、**不做抑制**。这样即使输入被误分类，也不会因此把真正的密钥藏起来。`--no-structural` 可整体关闭该层，退回 v0.7 的纯正则行为。

`--stats` 会在结束时向 stderr 输出各阶段丢弃计数，让整条流水线的判定过程可审计——这对安全工具很重要，因为"为什么没报"和"为什么误报"同样需要答案。

### 3.8 Chunk Graph：把懒加载资产与前端路由挖出来

`-G` 是这套工具里最具差异化的一块。现代应用只有一份入口 bundle，其余几百个 chunk 的 URL 都是运行时从内联清单里拼出来的，没有任何链接指向它们，爬虫永远看不到。

`-G` 做的事情就是**还原这份清单**，并输出完整资产列表与客户端路由表：

```
[CHUNKS] https://target.example/...: next runtime, 214 chunks, 37 routes
[CHUNK]  https://target.example/_next/static/chunks/settings.11aa22bb33cc.js
[ROUTE]  https://target.example/...  /admin/users/:id
```

支持的运行时：

- **webpack 4/5**：`__webpack_require__.u`、`miniCssF`、`jsonpScriptSrc`
- **Next.js**：build manifest
- **Vite / Rollup**：`__vite__mapDeps`、`__vitePreload`
- **原生动态 `import()`**
- **路由表**：React Router、Vue Router、Angular

输出是制表符分隔并去重，因此可以直接回流成为下一轮扫描的输入列表：

```bash
jshunter -u https://target.example/main.js -G -q | awk -F'\t' '/^\[CHUNK\]/{print $3}' > chunks.txt
jshunter -l chunks.txt -s -j > findings.json
```

这就是"发现 → 递归 → 收敛"的闭环：一次扫描的产物变成下一次扫描的种子。

### 3.9 反混淆与 Source Map

- `-d` 反混淆引擎：展开压缩/混淆代码，让结构层能在还原后的语义上工作。
- `-m` Source Map 解析：抓取 `sourceMappingURL` 指向的 map，并解析 `sourcesContent[]` 里的**原始源码**。生产环境的 source map 一旦泄露，等于拿到未压缩的完整代码，是最有价值的目标之一。
- `-z` 混淆检测：识别并归类混淆手法（便于判断代码是否被刻意保护）。
- `-e` eval 分析：定位 `eval()`、`Function()` 等动态执行点与运行时生成代码。

### 3.10 验证层：从"像"到"是"的最后一步

`--verify` 会用发现的值去打 **provider 的只读端点**，确认凭证是否真的有效。工程上做了三件事：`--verify-workers` 并发 worker 池（默认 8）、`--verify-timeout` 单次探测超时（默认 10s）、并发校验走 `concurrent_verify.go` 独立调度。

这是"静态匹配"与"动态确认"的分界：前者只能说存在这个字符串，后者才能说这个凭证是真的。

---

## 四、一次判定的完整走查

假设在某个 bundle 里出现：

```js
const awsAccessKeyId = "AKIAIOSFODNN7EXAMPLE";
```

1. **抓取**：并发拉取，按 `--max-bytes` 截断，必要时走 ETag 缓存。
2. **词法**：这一行被切成 `const`(code) `awsAccessKeyId`(code) `=`(code) `"..."`(string) `;`(code)，并为字符串建立 span。
3. **区间判定**：正则命中区间完全落在 `ClassString` 内 → 通过区域门控。若命中区间跨了引号边界，直接返回 `ClassStraddle` 被拒。
4. **角色判定**：字面量处于 `RoleAssignment`，属绑定角色 → 通过。
5. **绑定名**：`BindingName()` 恢复出 `awsAccessKeyId`，命中 AWS 规则的强制上下文要求。
6. **兄弟键**：本对象无兄弟键，不构成配置对象误判。
7. **校验**：`AKIA` 前缀 + 16 字符 base32 主体，validator 通过 → +0.10。
8. **评分**：先验 + 校验 + 上下文关键词 + 熵/字符类，汇总后越过 0.50。
9. **曝光分类**：判定为 secret（非发布方公开值）。
10. **输出**：终端彩色输出；`--json` 里附带完整 `evidence`：

```json
"exposure": "secret",
"evidence": {
  "region": "string-literal",
  "role": "assignment",
  "bound_to": "awsAccessKeyId",
  "shape": "opaque-token",
  "charset": "alphanumeric",
  "signals": [
    {"name": "in-literal", "delta": 0.04, "detail": "value is a complete string-literal"},
    {"name": "credential-binding", "delta": 0.12, "detail": "bound to 'awsAccessKeyId'"}
  ]
}
```

注意最后这个 `evidence` 对象——**每条结论都携带自己的推理链**，这也是它比纯正则扫描器更适合做正式报告的原因：发现可以被复核，而不只是一个"命中了"。

---

## 五、设计取舍与局限

**做得好的地方**

- **语法感知优于模式匹配**：先分类字节，再匹配，是这类工具里正确但昂贵的选择。误报率的下降主要来自这里，而不是来自规则数量。
- **可审计**：`--stats` 分阶段丢弃计数、`evidence` 逐信号增量、`--explain` 单规则详情，整条链路可追溯。
- **规则可扩展**：`--rules-file` 加载外部 JSON 规则包，`--only-rules` / `--disable-rule` 支持 `*` 通配，`--registry` / `--self-test` 用内置 TP/FP 样本自检。`--diff` 只报相对上次 JSON 的**新增**发现，适合放进 CI 做增量回归。
- **工程化成熟**：SARIF 2.1.0 输出直接对接 GitHub code scanning；NDJSON 方便 `jq` / SIEM 流式消费；`--ignore-file` 支持永久抑制。

**需要清醒认识的边界**

- **它不执行 JS**。所有"运行时拼接"的推断都建立在语法与字符串模式的静态分析上，遇到高度动态（服务端下发配置、加密混淆、运行时解密字符串）的情况会漏。真正的动态分析要靠浏览器实际跑起来。
- **结构引擎依赖"输入确实是 JS/JSON"这一前提**。虽然它做了安全阀（非 JS 时不抑制），但这也意味着对非 JS 输入的判定质量会退化。
- **verify 会向外发请求**。对第三方 provider 端点做探测，涉及授权范围问题，必须在合法授权内使用。
- **`--allow-internal` 默认关闭**。这既是对 localhost/RFC1918/link-local 的保护，也意味着扫内网资产需要显式打开——这个默认值设计是正确的。
- **规则库存在维护负担**。85+ 规则每一种都有自己的前缀族、长度窗口和校验逻辑，provider 改格式就会失效，需要持续跟进。
- **它只覆盖客户端可见面**。JS 里没有的东西它找不到；服务端配置错误、逻辑漏洞、认证绕过，都不在它的能力范围内。

---

## 六、与同类工具的关系

历史上这类需求多由 LinkFinder、SecretFinder 一类脚本承担，本质是"正则 + 少量启发式"。JShunter 的差异可以概括为一句话：

> 它把"这个字符串长得像密钥"换成了"这个字符串在语法上处于什么位置、绑定给了谁、周围有哪些兄弟键、是否通过了该 provider 的结构证明"。

再叠加 chunk graph 的资产还原能力，它实际上把工作从"扫描一个文件"推进到了"枚举一个应用的前端攻击面"。

---

## 七、使用速览

```bash
# 安装
go install -v github.com/cc1a2b/jshunter/cmd/jshunter@latest

# 单文件基础分析
jshunter -u "https://example.com/app.js"

# 密钥 + JWT + Firebase
jshunter -u "https://target.com/app.js" -s -x -F

# 全量：反混淆 + sourcemap + eval + 安全分析
jshunter -u "https://target.com/app.js" -d -m -e -s -x -P -g -F -B -L

# chunk 与路由枚举，产物回流
jshunter -u https://target.example/main.js -G -q | awk -F'\t' '/^\[CHUNK\]/{print $3}' > chunks.txt

# 走 Burp 代理，限速，导出 JSON
jshunter -l targets.txt -p 127.0.0.1:8080 -R 2000 -s -x -g -F -j -o findings.json

# CI 增量：只报新增
jshunter -f dist/bundle.js -s -j --diff previous.json --sarif

# 单规则详情与规则表
jshunter --list-rules
jshunter --explain aws_access_key_id
```

---

## 八、合规提示

该工具的能力本质是**对外部资产做主动探测**，其中 `--verify` 还会向第三方服务端点发起请求。使用时须满足：

- 仅在**获得明确书面授权**的目标范围内使用（自有资产、授权渗透测试、漏洞赏金项目 scope 内）；
- 遵守目标方的测试条款与速率限制，善用 `-R`、`--per-host`、`-T` 做节流；
- 发现的凭证应立即通过合规渠道上报，**不得留存、复用或扩散**；
- 扫描流量可能触发 WAF/IDS 告警，需提前与资产方沟通。

---

## 参考来源

- 仓库主页：https://github.com/cc1a2b/JShunter
- README（特性、命令参考、评分模型、provider 覆盖）：https://github.com/cc1a2b/JShunter/blob/main/README.md
- 规则说明：https://github.com/cc1a2b/JShunter/blob/main/RULES.md
- 结构引擎源码：https://github.com/cc1a2b/JShunter/blob/main/internal/jshunter/structure.go
- 词法扫描源码：https://github.com/cc1a2b/JShunter/blob/main/internal/jshunter/jslex.go
- Chunk Graph 源码：https://github.com/cc1a2b/JShunter/blob/main/internal/jshunter/chunkgraph.go
