# testweb 本地爬虫测试站点

本目录包含 13 个本地站点、机器可读 URL/方法契约和本地站点服务。传统站由 Go 服务提供；Vue、React、Angular、Nuxt、Next.js、SvelteKit 是独立的真实框架应用。TodoMVC 与 Juice Shop 是使用 Vue/React/Angular 框架构建的独立测试场景，不是公网产品的复制。

## 启动

后端是独立 Go 模块，不加入仓库 `go.work`。在 PowerShell 中：

```powershell
cd testweb/backend
$env:GOWORK = "off"
go run .
```

后端默认仅监听 `127.0.0.1:8090`。如需更换端口，可传 loopback 地址：

```powershell
go run . 127.0.0.1:8091
```

服务拒绝非 loopback 监听地址。健康检查：`GET http://127.0.0.1:8090/healthz`。13 个站点入口、框架和端口由 `manifest.json` 指定。

安装前端依赖并运行全套浏览器 E2E：

```powershell
cd testweb
npm install
npm run e2e
```

E2E 会启动后端与全部前端开发服务，使用 Playwright 连接本机 Microsoft Edge（或将 `TESTWEB_BROWSER` 指向本机 Chromium/Chrome），测试 13 个入口、框架运行标记、页面导航、Todo 添加、表单 POST、交互 API GET 和传统站静态资源。每次 E2E 结果写入仓库 `artifacts/crawler-comparison/testweb-e2e-<timestamp>/` 新目录，包含逐站汇总和服务日志。不要把失败尝试与后续通过批次合并。

## 已实现目标

`contracts/traditional.json` 与 `contracts/frameworks.json` 是人工维护的基线，身份按 HTTP 方法和精确 path/query 判定。传统站包括首页、分页页、参数详情、搜索、固定测试表单、CSS、robots.txt 与 sitemap.xml；HTML5 站另含一个外部 JS 文件。框架站包括初始页面、客户端/文件路由、参数路由以及交互触发的 GET/POST API。表单只返回固定测试数据的确认响应，不保存状态。

服务器日志记录请求方法、Request URI 和 Host。除站点页面外，不提供通用代理或任意 URL 抓取接口。

## 版本与结果隔离

`manifest.json` 和 `contracts/` 属于源代码真值，修改后应审阅差异。每次爬虫对照应在 `artifacts/crawler-comparison/` 下新建独立运行目录，并记录本目录 Git 提交、manifest/契约哈希、工具版本与实际命令。不要将历史 runs 或爬虫发现自动合并为真值。

框架版本固定在各应用 `package.json` 和根 `package-lock.json`。Next.js 使用修复版 15.5.9。实施进度与剩余增强项见 [实施方案](IMPLEMENTATION_PLAN.md)。
