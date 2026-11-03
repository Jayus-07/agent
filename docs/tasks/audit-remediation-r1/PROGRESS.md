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

- `py_compile`：全部改动源文件 + 测试文件通过。
- pytest 定向（worktree，非全量），全部通过：
  - P0-1/P1-2 初版：`test_data_upload_path_guard.py`（7 例）+ `test_pipeline_not_ready.py`（含新口径用例）→ 11 passed
  - P1-3/词表：`test_stream_events_flow.py` + `test_error_taxonomy_unify.py` + `test_task_phase2_step2_error_taxonomy.py` + `test_task_cancel.py` → 102 passed
- **与 main 合并（eb405b3）后复验**：main 侧 0f74940（S3/S4，另一会话）已修复 data.py 路径穿越并加 admin 鉴权，且 runner.py 增加 copy_context worker 包裹。据此：
  - data.py 冲突以 main 版为基，仅叠加仍缺失的流式 50MB 大小上限（S4 未覆盖的内存缺口）；
  - `test_data_upload_path_guard.py` 重写为锁定合并后语义（净化不逃逸 + admin 桩 + 超限拒绝）→ 6 passed；
  - `test_stream_events_flow.py`（copy_context × 绑定段包层组合语义）→ 30 passed。
- PR CI（#283）全绿：RAG Smoke preflight、Evaluation V2 free contract / admin build、secret-scan、Detect RAG changes。

## 提交与合并记录

- 1cb8d04 fix(data)：P0-1（初版，后与 main S4 合流）
- 9d410b3 fix(tasks)：P1-2
- 8a5945f fix(runtime)：P1-3
- 6b80db6 docs(tasks)：立项档案
- eb405b3 merge origin/main + data.py 冲突消解（main S3/S4 为基 + 大小上限）+ 测试语义重写
- PR：#283（CI 全绿后合并 main）

## 未完成 / 并行协调备注

- 另一会话的审计线（0f74940 S3/S4、`docs/tasks` 回填「W4/router 收官与 W5 实施」）与本项目后续轮次可能重叠：**启动 P1-5（competitor 域鉴权）、P2 批次前必须先核对 main 最新任务档案与其 S/W 编号清单，避免同题两套实现**（本次 data.py 即发生一次重叠，靠合并消解）。
- P1-1（checkpointer 连接池）、P1-4（网关直通面）、P2 批次待下一轮立项。

## 阻塞

- 无。

## 下一步

1. ~~回填 runner/词表/任务取消套件测试结果~~（已回填：102 passed）。
2. 按 Git 并行开发规范展示 Diff + 验证结果，等用户确认后路径限定提交（`git add -A -- <paths>` + `git commit -- <paths>`），走 PR 合并 main。
3. 后续轮次立项（P1-1 / P1-4 / P1-5 / P2 批次），见 TASK.md 范围节。

## 备注

- Mimosa PreToolUse hook 对 `os.path.join(base, f"...")` 内联 f-string 拼路径会拦截；改为先提取 `stored_name` 变量再 join + realpath 包含校验后通过。后续在此仓库写路径相关代码时沿用该形态。
