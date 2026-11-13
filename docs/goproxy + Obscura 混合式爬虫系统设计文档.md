# 混合式爬虫系统设计文档
> 方案定位：只读流量采集爬虫，基于 Go + Rod，**一对一绑定 goproxy MITM代理 + Obscura 无头浏览器**；复用成熟开源组件能力，不重复造轮子，薄粘合、低维护成本。
> 核心来源：
> 1. `naiba/proxyinabox`：Obscura 进程拉起、CDP WS就绪探测、进程优雅回收、僵尸进程处理
> 2. `anatolykoptev/go-browser`：Session 池、会话健康巡检、会话生命周期管理、故障隔离
> 3. `projectdiscovery/katana`：BFS广度优先调度、链接提取、URL去重、Scope域名范围过滤、jsluice静态JS链接解析
> 4. 新增自研薄粘合层：一对一代理绑定、goproxy MITM流量录制、流量数据标准化存储

## 1 需求说明
### 1.1 功能需求
- 网页渲染、JS执行、懒加载接口触发（依靠 Obscura）
- **仅采集请求/响应，不篡改、不拦截流量**（只读采集）
- 采集内容：URL、Method、Header、Status、Body、时序、Content-Type
- 爬虫能力：BFS递归爬取、链接提取、域名scope控制、URL去重、JS静态解析提取接口
- 会话隔离：**一个代理实例 ↔ 一个Obscura实例 ↔ 一个爬虫任务会话**，互不干扰
- 资源管控：进程健康检查、超时、优雅关闭、防止僵尸进程、端口冲突
- 可降级：Obscura异常时，可切换为 Chrome for Testing 便携版作为备选渲染后端

### 1.2 非功能需求
- 轻量化：不内置冗余UI、Lua脚本引擎、数据库、代理轮换池（proxyinabox多余组件全部剔除）
- 可移植：Obscura为单静态二进制，goproxy原生Go，Chrome for Testing为便携免安装包
- 可扩展：上层爬虫调度、底层浏览器后端解耦；后续可替换 Moli / Chrome / Ungoogled Chromium
- 故障安全：单个会话崩溃不影响其他会话；支持全局信号优雅退出（SIGINT/SIGTERM）

### 1.3 约束
- 不使用 CDP Fetch 劫持（Obscura/Moli Fetch域不完善），流量采集全部交给前置 goproxy MITM
- WebSocket、SSE长连接不做body解析，仅记录元信息
- 流量body做大小限制，避免内存溢出（例如单响应上限 2MB）
- 许可证约束：复用代码均为 MIT 协议，保留原版权声明

## 2 整体架构
### 2.1 拓扑图
```
Crawler Scheduler（Katana BFS调度）
        ↓ 分配任务
Session Pool（来自go-browser）
    ├─ CrawlSession（1:1绑定）
    │    ├─ Goproxy Runner（MITM代理，单独端口）
    │    └─ Obscura Runner（来自proxyinabox，独立CDP端口、独立profile）
    │           ↓ CDP(WebSocket)
    │         Rod Client
    │           ↓ 页面交互（navigate/click/eval/html）
    └─ 流量流向：Obscura → 本会话专属goproxy → 目标站点
                        ↓
                  流量录制模块（结构化存储/输出HAR）
```

> 核心原则：**会话隔离**
> 每个 CrawlSession 拥有独立：代理端口、CDP端口、Obscura进程、临时CA证书（MITM）、浏览器profile；任务结束整套销毁。

### 2.2 模块划分
| 模块 | 来源 | 职责 | 裁剪说明 |
|------|------|------|----------|
| Obscura Runner | proxyinabox 片段 | 启动Obscura、传参（proxy、stealth、ca证书）、轮询 `/json/version` 等待CDP就绪、SIGTERM优雅关闭、超时SIGKILL、cmd.Wait回收僵尸 | 删除Lua、代理池、SQLite、任务调度；只保留进程启停+探活 |
| Goproxy Runner | 自研封装（基于elazarl/goproxy） | 随机空闲端口启动、MITM证书加载、流量只读录制、body限流、Shutdown优雅关闭、健康ping | 一对一绑定会话，不全局共享代理 |
| Session Pool & Health | go-browser 思想 | 会话创建、空闲/繁忙状态、最大并发限制、后台巡检、失效会话标记、自动回收、会话复用策略（可选） | 剥离原生Chrome硬编码，抽象Browser接口，支持Obscura/Chrome后端切换 |
| Crawler Core | katana 片段 | BFS队列、URL指纹去重、scope正则过滤、DOM链接提取、form/iframe提取、jsluice静态解析JS链接、深度控制 | 剔除katana原生Fetch劫持、内置http client模式；爬虫不再处理网络抓包 |
| Glue Layer（粘合层） | 自研薄层 | 组装ProxyRunner + BrowserRunner → CrawlSession；参数传递、日志绑定sessionID、降级逻辑、统一回调 | 最薄，只做组装、参数透传、错误传递，不写复杂业务 |
| Storage/Exporter | 自研 | 流量日志写入、HAR导出、JSON行输出、过滤静态资源（图片/字体可选跳过body） | 独立输出，和调度解耦 |

## 3 核心接口抽象（粘合层契约）
```go
// ProxyRunner 代理抽象，一对一绑定会话
type ProxyRunner interface {
	Start(ctx context.Context) error
	Shutdown(ctx context.Context) error
	ProxyAddr() string // [http://127.0.0.1](http://127.0.0.1):port
	Healthy() bool
}

// BrowserRunner 浏览器抽象（兼容Obscura / Chrome for Testing）
type BrowserRunner interface {
	Start(ctx context.Context, proxyAddr string) error
	Shutdown(ctx context.Context) error
	WSAddr() string // CDP ws地址
	Healthy() bool
	RodBrowser() *rod.Browser
}

// CrawlSession 会话单元
type CrawlSession struct {
	SessionID string
	proxy     ProxyRunner
	browser   BrowserRunner
	ctx       context.Context
	cancel    context.CancelFunc
}

// SessionPool 会话池（参考go-browser）
type SessionPool interface {
	Take(ctx context.Context) (*CrawlSession, error)
	Put(ctx context.Context, s *CrawlSession)
	CloseAll(ctx context.Context) error
}
```

## 4 生命周期流程（单会话）
### 4.1 启动顺序（严格不能颠倒）
1. 池分配空闲端口，初始化 ProxyRunner
2. `ProxyRunner.Start`：启动goproxy，加载CA，监听端口，循环ping探活，就绪后返回 proxyAddr
3. 初始化 BrowserRunner，传入上一步 proxyAddr
4. `BrowserRunner.Start`：启动 Obscura 进程，参数包含 `--proxy`、`--stealth`、`--ca`、独立profile目录
5. 循环探测 `ws://127.0.0.1:cdpPort/json/version`，直到CDP服务就绪
6. Rod建立连接，创建Page，标记会话状态 Ready
7. 交给Katana BFS调度器执行页面爬取、链接挖掘

### 4.2 运行阶段
1. 调度器提交页面任务，Rod执行导航、点击、等待渲染、提取DOM
2. 所有网络流量经过本会话goproxy，代理层记录请求响应，**浏览器CDP不订阅Network/Fetch事件**
3. 后台巡检协程：周期性检查 proxy.Healthy() && browser.Healthy()
   - 任意组件不健康 → 标记会话失效，触发回收
4. 流量录制：判断content-type、body大小，超过阈值只存元信息，丢弃body

### 4.3 关闭顺序（资源回收，防止僵尸）
1. 关闭Rod Page，断开CDP连接
2. 调用 BrowserRunner.Shutdown：发送SIGTERM，设置超时；超时未退出则SIGKILL；执行cmd.Wait等待进程退出
3. 调用 ProxyRunner.Shutdown：http.Server Shutdown，等待正在处理请求收尾
4. 清理临时资源：临时CA证书文件、浏览器profile目录
5. 归还/销毁会话，释放端口

### 4.4 全局信号兜底
程序捕获 SIGINT / SIGTERM：
- 带超时ctx调用 `pool.CloseAll`，遍历所有会话依次关闭
- 等待全部资源回收后退出

## 5 降级策略（重要）
> Obscura渲染失败、页面检测异常时，自动切换后端为 Chrome for Testing（便携版）
1. BrowserRunner 为接口，实现两个实例：`ObscuraRunner`、`ChromeRunner`
2. ChromeRunner 使用 Chrome for Testing 便携二进制，参数：`--user-data-dir`、`--proxy`、`--no-first-run`、`--no-default-browser-check`
3. 切换时：代理逻辑完全不变，粘合层、调度层、流量录制逻辑零改动

## 6 流量采集规则（goproxy侧）
- 只读模式：`OnRequest`、`OnResponse` 只记录，不修改request/response
- 限流：单个response body > 2MB，跳过body保存，仅记录status、header、url
- 过滤规则（可配置）：可跳过图片、字体、视频等静态资源的body存储
- 输出格式：JSONL / HAR 文件，每条记录绑定 sessionID、taskID
- MITM说明：
  - 需要CA证书；Obscura支持传入自定义CA；Chrome也支持导入根证书
  - 不需要解密body的场景：关闭MITM，仅记录握手元信息，无法拿到https明文body

## 7 复用代码的裁剪与版权说明
1. Obscura进程管理片段：取自 `naiba/proxyinabox`，MIT License；保留头部版权注释，不引入gorm、sqlite、lua、代理池模块
2. Session池、健康巡检思想：取自 `anatolykoptev/go-browser`，MIT License；剥离Chrome硬编码，抽象Browser接口
3. BFS、链接提取、去重、scope：取自 `projectdiscovery/katana`，MIT License；移除原生Fetch劫持、内置http client抓取逻辑
4. goproxy 基于 `elazarl/goproxy`，MIT License
5. 粘合层、流量录制、会话一对一绑定为自研新增代码

## 8 风险清单 & 规避方案
| 风险 | 规避 |
|------|------|
| Obscura新版本启动参数变更、CDP行为变化 | 锁定Obscura固定版本，版本号写在配置 |
| 端口分配冲突 | 使用net.Listen自动获取空闲端口，先占用再启动服务 |
| 僵尸进程 | 每次启动cmd必须goroutine执行cmd.Wait；关闭时SIGTERM+超时SIGKILL双保险 |
| MITM证书信任问题 | 单独CA管理，每个会话可复用全局CA（简化）或者临时CA（隔离） |
| 高并发内存打爆 | body大小限制、静态资源过滤、会话池最大并发限制 |
| WebSocket无法解析body | 识别Upgrade头，只记录元信息，不尝试读取流 |
| 会话泄漏 | 任务无论成功失败，都必须归还/关闭session；ctx超时自动销毁会话 |

## 9 迭代路线（分阶段落地）
### 阶段一：最简验证版（优先）
- 固定CA，不做会话独立证书
- 无会话池，单次任务单会话，用完销毁
- 只实现：ProxyRunner + ObscuraRunner + Katana BFS调用
- 目标：验证链路通不通，流量录制是否稳定，Obscura渲染是否满足需求

### 阶段二：稳定生产版（推荐长期使用）
- 实现 SessionPool，限制最大并发
- 后台健康巡检、失效会话自动回收
- 全局信号优雅退出、日志按sessionID隔离
- 支持Obscura/Chrome后端配置切换

### 阶段三：增强版（可选，大规模无人值守）
- 自动生成临时CA证书，会话级证书自动清理
- 会话复用策略（N次任务后强制重建，防止缓存污染）
- 指标上报：会话数量、失败率、流量统计
- 分布式调度扩展（多节点）

## 10 对比备选方案（为什么选这套）
1. 对比「Obscura CDP Network + JS Hook」：
   - 优点：流量信息完整，Worker请求也能抓到（除ws），不受页面JS覆盖hook影响
   - 缺点：多一层代理、证书管理、生命周期复杂度上升
2. 对比「Katana原生Fetch劫持」：
   - 优点：可以使用Obscura/Moli这类轻量Rust浏览器，二进制体积小、内存更低、自带stealth；不需要依赖完整Chrome
   - 缺点：需要额外维护代理生命周期
3. 对比「直接全套Chrome for Testing」：
   - 优点：CDP Fetch劫持原生稳定，方案简单
   - 缺点：Chrome二进制体积大、内存高，stealth反指纹不如Obscura原生

## 11 配置示例（简化yaml）
```yaml
# 浏览器后端: obscura / chrome
browser_backend: obscura
# 最大并发会话数
max_session: 5
# 单响应body上限(字节)
max_body_size: 2097152
# 爬虫配置，复用katana参数
crawl:
  depth: 3
  scope: "example.com"
  jsluice: true
# 代理MITM配置
mitm:
  ca_crt: "./ca.crt"
  ca_key: "./ca.key"
```

如果你需要，我可以继续输出：
1. 配套目录结构 + 初始go.mod
2. 阶段一最简版完整可运行代码（ProxyRunner + ObscuraRunner + 调用katana crawler）