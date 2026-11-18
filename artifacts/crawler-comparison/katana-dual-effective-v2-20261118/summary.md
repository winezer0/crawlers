# Katana 双模式有效结果对比

| 站点 | base秒 | Katana静态秒 | Katana浏览器秒 | 已核验有效参考 | 覆盖 | 待核验 | 静态/浏览器参考状态 | 达标 |
|---|---:|---:|---:|---:|---:|---:|---|---|
| testasp | 38.37 | 22.94 | 7.37 | 10 | 100.00% | 0 | completed/empty_unverified | False |
| testaspnet | 10.12 | 23.19 | 7.13 | 13 | 100.00% | 0 | completed/empty_unverified | False |
| framework-vue | 60.46 | 60.57 | 61.04 | 84 | 100.00% | 0 | completed/completed | True |
| framework-react | 41.04 | 51.14 | 60.69 | 219 | 100.00% | 0 | completed/completed | True |
| framework-angular | 48.94 | 60.08 | 62.65 | 585 | 100.00% | 0 | completed/completed | True |
| framework-nextjs | 43.70 | 60.47 | 61.20 | 13 | 100.00% | 1 | completed/completed | False |
| framework-sveltekit | 60.21 | 60.53 | 62.70 | 410 | 99.02% | 0 | completed/completed | True |

达标站点：4/7；总目标达成：False。

只核验 GET 页面/JSON；方法、参数、模板及业务有效性另行验收。未核验、网络错误和浏览器失败不会被当成达标。
启用 basecrawler --fsu；发现记录保留过滤前证据。参数值和数字 ID 不做武断归并，HTML 别名需响应证据。
