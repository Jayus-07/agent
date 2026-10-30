# TASK: obs-collection-handoff — 可观测性采集链路修复 + 管理端重构遗留收尾

- worktree: `D:/Program Files/workplace/agent-wt-obs-handoff`
- branch: `feat/obs-collection-handoff`（自 main@2517e98）
- 状态: IN_PROGRESS

## 目标

1. P1 采集链路统一化：主图 LLM 调用在 `backend/infra/llm` 出口补 llm_call span（脱敏 + 不双计）；
   RAG 链/定时任务路径补 prompt 版本记录或明确"未版本化"口径。
2. P2 遗留三项：`_trace_dto` 透出 workflow_kind；`/traces/stats` 增 domain 维度；
   dev-svc.bat 注释与 rebuild 服务列表修正。
3. P3 原型保真度：总览运行中卡可点击；接 llm_model_health 模型健康卡；差异清单逐项核对。
4. P4 验证交付：vitest + tsc + 定向 pytest + rebuild 实测 + PR 合并 main。

## 根因证据（2026-10-30 实查，已在任务派单中固化）

- llm_call span 既有埋点仅 rag/chain.py:604（llm_generate）与 tool_selector.py:399；
  主图 general_chat 等节点直连 `backend/infra/llm` 共享客户端，该层无埋点 →
  近 24h workflow_name=agent 的 28 条 trace llm_call span = 0。
- llm_usage 计费正常（Token 明细有数据），tracer.finish 有 `_backfill_usage_from_store`
  时间最近邻配对逻辑——说明 llm_usage 与 trace 两条链只差"span 本体不存在"。
- prompt 版本：`record_prompt_version` 由 PromptService.render/render_sync 自动触发；
  `get_template_sync` 不触发（配套 `record_template_usage_sync` 无人调用）。
  - rag/chain.py 已有 `_record_runtime_prompt_usage()`（rag.qa/rag.document/rag.contextualize）；
  - daily_report 的 InventoryAnalyzer 用 `get_template_sync("capability.inventory_analyzer")` 缺记录；
  - inventory_alert 为纯规则链路（SQL+状态机+邮件，零 LLM），prompt_versions=0 属如实空值。

## 验收红线

- 不降低授权与脱敏标准；不伪造未采集数据（空值如实显示）；
- 不删除既有测试除非契约确实变更并有替代覆盖；llm 计量不双计。

## 阶段

- [ ] P1 后端采集链路统一化（span + prompt_versions + 定向测试）
- [ ] P2 遗留三项（DTO / stats domain / dev-svc.bat）
- [ ] P3 原型保真度收尾（运行中卡 / 模型健康卡 / 差异清单）
- [ ] P4 验证与交付（vitest / tsc / pytest / rebuild 实测 / PR）
