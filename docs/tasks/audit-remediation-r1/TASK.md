# audit-remediation-r1 — 全仓审计修复第一轮（P0-1 / P1-2 / P1-3）

> 源起：2026-11-03 全仓审计（架构分析 + Mimosa 深度扫描 + 双线代码审查）。
> 审计报告见当次会话输出；Mimosa scanId
> `scan-2026-11-02T22-27-15.492Z-7e692965b766`（seal
> `sha256:d31b064aaf99d7f66772fc776ae27dbe1504fbee78e5cb29d5d7ca9f379c8e81`）。

## 目标

把审计确认的最高优先级缺陷修复到位并带回归测试锁死：

| 项 | 缺陷 | 修复文件 |
| --- | --- | --- |
| P0-1 | `/data/upload` 路径穿越（客户端可控 filename 直拼路径 → 容器内任意 .csv/.json 写入）+ 上传无大小上限 | `backend/app/api/routes/data.py` |
| P1-2 | `service_unavailable` 重试口径分裂：可调度重投但 `is_waiting_retry` 视为终态 → 已取消任务复活执行 | `backend/tasks/error_taxonomy.py`、`backend/observability/error_taxonomy.py` |
| P1-3 | `GraphRunner._worker` 绑定段（bind/import/挂 sink）在 try 之外：异常时 done sentinel 永不入队，SSE 流永久挂死并逐个占死线程池线程 | `backend/orchestration/graph/runner.py` |

## 范围

- 只改上述缺陷的最小充分集；不顺手重构（checkpointer 连接池、competitor 鉴权等留给后续轮次）。
- 每项带特征化回归测试；不扩大 mock、不 skip。
- 后续轮次（未在本任务内）：P1-1 三处 checkpointer 换 ConnectionPool、P1-4 网关直通面收口、P1-5 competitor 域鉴权、P2 批次（任务流 SSE 终态补帧、Redis 锁释放、demo 角色闸、验证码 secrets、XML 实体防护等）。

## 验收标准

1. P0-1：含 `/`、`\`、空名、`.` 开头、NUL 的文件名一律拒绝；合法 CSV/JSON 仍可上传且 realpath 包含校验落在 UPLOAD_DIR 内；流式读取超上限（50MB）即拒。全部有测试。
2. P1-2：`retryable_of("service_unavailable") is True` 且 `is_waiting_retry` 对 FAILED(service_unavailable) 返回 True；`TASK_ERROR_TYPES` 取值域与 `observability` 统一映射同步（fail-fast 自检通过）。
3. P1-3：绑定段异常 → 统一 error 帧出帧 + done sentinel 恰好一次，流可终结（测试以 `list(stream_events(...))` 自然返回为断言）；正常路径行为不变。
4. 定向测试全绿：`test_data_upload_path_guard.py`、`test_pipeline_not_ready.py`、`test_stream_events_flow.py`、`test_error_taxonomy_unify.py`、`test_task_phase2_step2_error_taxonomy.py`、`test_task_cancel.py`。

## 风险与连带更新

- `_RETRYABLE_TYPES` 新增取值会使 DB 中历史 FAILED(service_unavailable) 记录从「终态」变回「等待重试」——这是修复本意（可被取消/继续重试语义），需在 PR 描述中说明。
- `observability/_TASK_MAP` 由导入期 fail-fast 守护，漏配会在启动即暴露（已同步）。
- 不涉及 DB 迁移、API 契约变更、前端改道；无需增改 rag_smoke 定向清单（无新链路环节）。
