# RAG 验收剩余项状态

本任务继续使用[验收清单](ACCEPTANCE_CHECKLIST.md)的门禁与阈值。历史执行流水已从清单移除；实现以代码、配置、数据库状态和当前复验为准。在本任务的剩余门禁闭合前，不声明 `RAG_PRODUCTION_READY=true`。

## 待闭合门禁

- **O11 上传/索引指标**：最近一次记录中，worker 已有带标签的 `rag_index_duration_seconds` 样本；`rag_upload_total` 只有指标定义，未观察到带标签的成功/失败样本。需要沿真实上传出口核实埋点进程与指标查询面，并在目标环境取证。
- **延迟观察**：最后一次浏览器复测的成功 Trace 为 `70daa4b88da7`，耗时 14.414 秒、root `uncovered_ms=126`；同一已检查窗口仍包含 SLA 超时，因此不能凭单次成功关闭延迟门。按清单要求取得新的干净观察窗口，并复核 Trace 与 SLA 汇总。

## 已有验证入口

- 指标：管理端 Prometheus/Trace 页面，或目标环境的 `/metrics` 与 Trace API。
- 上传、索引与检索契约：`backend/tests/rag/`、`backend/tests/api/` 中相关回归测试。
- 最新运行态证据必须从目标环境重新采集；开发机的历史 Trace 和临时探针文件不作为当前生产结论。

## 下一步

1. 定位并验证 `rag_upload_total` 的成功/失败计数出口，确认 worker 与 app 指标查询面一致。
2. 部署后观察完整验收窗口，分别检查上传指标、索引耗时、检索延迟和 no-evidence 指标。
3. 用新 Trace 与 SLA 汇总更新本状态和清单，再按门禁公式复核是否可声明就绪。
