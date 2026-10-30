# TASK: obs-legacy-fixups — 可观测性遗留修复批次

- worktree: `D:/Program Files/workplace/agent-wt-obs-legacy`
- branch: `feat/obs-legacy-fixups`（自 main@12bdef8）
- 派单: `D:/tmp/obs-legacy-fixups-PLAN.md`（obs-collection-handoff 收尾会话产出）
- 状态: COMPLETED（PR #142 待合并）

## 目标

1. 阶段 1：详情页 Prompt 版本展示兼容 map/list 两形态；`rag/chain.py` `_start()` 显式传
   `workflow_name="rag_agent", workflow_kind="rag_query"`；清理测试账号 `obs_handoff_viewer`。
2. 阶段 2：dev-svc.bat 项目名防护（COMPOSE_PROJECT_NAME 未设且目录名 ≠ agent 时强制设为 agent）；
   trace_children 测试修复需先与 CI 基线会话协调认领。
3. 阶段 3：compare 对比页按 workbench 视觉重构；Span HTTP status 收敛方案
   （`_record_failed_attempt` finish_reason 拼 status + `to_span_dto` llm_call 增 `http_status`）。
4. 阶段 4：vitest 全量 + tsc + 定向 pytest + rebuild 实测 + PR 合并 main。

## 已验证事实（摘自派单第三节，不再重复推断）

- 详情页 `frontend-admin/src/app/observability/traces/[id]/page.tsx:314` 只认 list 形态，
  runner 写入的是 `{key: version}` map 快照 → 有值显示「未采集」。
- `record_prompt_version` 已在 86885cf 兼容 map/list；两形态并存是既有事实，本任务只修展示。
- `rag/chain.py:851` 附近 `trace_collector.start(...)` 未传 workflow_name/kind → kind 落 OTHER。
- dev-svc.bat 无 COMPOSE_PROJECT_NAME 处理；Git Bash 下 `/y` 被吞，用 `-y`。
- `test_observability_trace_children` 2 个既有失败（get_trace 缺 request 参数），main@12bdef8 已存在，
  修复前必须与 agent-wt-cibase / agent-wt-ci-debt 协调归属。
- compare 页 169 行旧样式 + page.test.tsx；SpanGantt.tsx:179 http_breakdown 前端分支已就绪、后端无写入点。
- llm_call span 统一出口契约不得破坏（backend/observability/llm_call_span.py）。
- 测试账号 `obs_handoff_viewer`（auth.users，role=admin，phone=19900000001）待删除。

## 验收红线

- 不伪造未采集数据（空值如实显示）；不降低授权与脱敏标准；llm 计量不双计。
- 不删除既有测试除非契约确实变更并有替代覆盖；不以扩大 mock/skip/xfail 掩盖失败。
- 与 CI 基线会话冲突项先协调认领；提交双重路径限定；一律 PR 合并 main；合并后才清理 worktree。

## 明确不做

- prompt_versions 双格式统一（三处契约，缓做）。
- HTTP 全链路拆分采集（DNS/连接/TTFB/下载）。
- TraceStore 留存扩容 / 统计下推 PG 聚合。
- Run 级待审批状态、降级观测覆盖率。

## 阶段

- [x] 阶段 0：开工（worktree / 分支 / 任务文件 / .env / 并发冻结确认）
- [x] 阶段 1：详情页 Prompt 版本兼容 + RAG trace kind 对齐 + 账号清理
- [x] 阶段 2：dev-svc.bat 项目名防护；trace_children 测试修复经归属协调确认
      已被 ci-debt 会话 PR #139 覆盖（main 实跑 2 passed），按派单「从计划删除本项」关闭
- [x] 阶段 3：compare 对比页重构 + Span HTTP status 下发
- [x] 阶段 4：验证与交付（vitest 全量 / tsc / 定向 pytest / rebuild / PR #142 合并 0b81777）
