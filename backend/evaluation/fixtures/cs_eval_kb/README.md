# 客服 full_path 测试语料

`manifest.json` 将 `docs/customer-service/demo-kb/` 中的合成 Markdown 文件锁定到 `cs_eval_kb`，并用固定文档 ID、内容 SHA-256、RAG 版本 ID 和精确章节维护事实来源映射。

此目录不由生产客服 seeder 或 Docker 启动流程导入。需要准备本地/CI 测试 KB 时，必须显式运行：

```powershell
D:/Python/python.exe -m backend.evaluation.ingest_cs_fixtures --fixture-set baseline --apply
```

该命令会调用当前配置的 embedding Provider，并且只清理/写入 `audience=test` 的 `cs_eval_kb`。不得在离线验证或生产部署流程中运行。导入器会拒绝 KB 受众不为 `test`、语料哈希漂移或无法通过索引状态校验的情况。

更新语料时要提升 `fixture_version` 与 `version_id`，重算文档 SHA-256，复核 `fact_sources` 的章节标题，并保留此前 suite 与报告使用的版本证据。未登记的文档、章节和业务数据不会生成事实 ID。
