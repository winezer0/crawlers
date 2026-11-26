# 迁移证据包

本目录的 5 个 `evidence.zip.partNNN` 为同一个 ZIP 的连续二进制分片，单片不超过 40 MiB。请保留全部分片。

恢复步骤和继续任务见 [跨电脑开发交接](../../TODO/跨电脑开发交接-20261126.md)。在仓库根执行：

```text
python tools/transfer_crawler_evidence.py verify
python tools/transfer_crawler_evidence.py restore
```

推荐保留原仓库路径；其他路径可使用 `restore --relocate`，派生元数据与原始历史证据的区别详见交接文档。`manifest.json` 记录各分片及 11,000 个文件的 SHA256。包内结果早于最后一次参数枚举修复，不能作为该修复端到端通过的证据。
