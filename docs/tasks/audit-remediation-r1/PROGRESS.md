# audit-remediation-r1 — PROGRESS

## 环境与位置

- Worktree：`D:\Program Files\workplace\agent-wt-audit-r1`（从 main@70f6351 创建）
- 分支：`fix/audit-remediation-r1`
- .env：已从主仓库拷贝（worktree 默认无 .env）

## 已完成

- [x] P0-1 `backend/app/api/routes/data.py`：
  - filename 守卫（拒绝分隔符/空/隐藏/NUL，不静默改名）
  - realpath 归一 + commonpath 包含校验（双层防线，模板对齐 rag_upload）
  - 流式读取 + `DATA_UPLOAD_MAX_BYTES`（50MB）上限
  - 响应与日志改用 `safe_name`，不再回显原始 filename
- [x] P1-2 `backend/tasks/error_taxonomy.py` + `backend/observability/error_taxonomy.py`：
  - `service_unavailable` 进 `TASK_ERROR_TYPES`（取值域补齐）与 `_RETRYABLE_TYPES`（口径对齐）
  - `_TASK_MAP` 增加 `service_unavailable → network_error`（与 Tool 层 UNAVAILABLE 同口径；导入期 fail-fast 通过）
  - 模块 docstring 的 Retry 口径同步
- [x] P1-3 `backend/orchestration/graph/runner.py` `_worker`：
  - 绑定段单独包层 try/except；失败走 `_runner_error_event` 同口径 error 帧 + 部分绑定 ContextVar 清理 + done sentinel 后 return
  - 正常路径（图执行主体 try/except/finally 与 sentinel 投递）零改动
- [x] 回归测试三份：
  - `backend/tests/api/test_data_upload_path_guard.py`（新增，7 例）
  - `backend/tests/rag/test_pipeline_not_ready.py`（追加 cancel/retry 口径一致性用例）
  - `backend/tests/orchestration/graph/test_stream_events_flow.py`（追加绑定失败终结流用例）

## 验证证据

- `py_compile`：4 个改动源文件 + 3 个测试文件全部通过。
- pytest 定向（worktree，非全量），全部通过：
  - `backend/tests/api/test_data_upload_path_guard.py` + `backend/tests/rag/test_pipeline_not_ready.py` → **11 passed**（2026-11-03）
  - `backend/tests/orchestration/graph/test_stream_events_flow.py` + `backend/tests/test_error_taxonomy_unify.py` + `backend/tests/test_task_phase2_step2_error_taxonomy.py` + `backend/tests/test_task_cancel.py` → **102 passed**（2026-11-03，927s）
- 未运行全量回归（遵循 testing-guide 最小充分测试；本轮无 DB 迁移/契约变更）。

## 阻塞

- 无。

## 下一步

1. ~~回填 runner/词表/任务取消套件测试结果~~（已回填：102 passed）。
2. 按 Git 并行开发规范展示 Diff + 验证结果，等用户确认后路径限定提交（`git add -A -- <paths>` + `git commit -- <paths>`），走 PR 合并 main。
3. 后续轮次立项（P1-1 / P1-4 / P1-5 / P2 批次），见 TASK.md 范围节。

## 备注

- Mimosa PreToolUse hook 对 `os.path.join(base, f"...")` 内联 f-string 拼路径会拦截；改为先提取 `stored_name` 变量再 join + realpath 包含校验后通过。后续在此仓库写路径相关代码时沿用该形态。
