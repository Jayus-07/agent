# 上下文预算管理 Context Budget Management · 基础版实施规格

> 日期：2026-09-22 ｜ 状态：实施中 ｜ 本文为需求持久化（逐字实施依据）
> 背景现状见《2026-09-22-上下文预算L1-L5-项目现状说明.md》

## 目标范围（基础版）

先实现稳定、简单、可运行的基础版，架构上为完整 L1–L5 演进预留接口：

- L1 工具结果预算（ToolResultBudgetGuard，统一拦截点）
- L2 基础历史裁剪（复用 trim_messages_to_budget，动态预算）
- L3 previous_outputs 微压缩（总预算 1024，最新优先）
- 全局 Prompt Token Preflight（prepare_llm_context）
- SSE context 事件 + 前端上下文提示
- L4 Context Collapse / L5 AutoCompact 仅预留接口，不实现

## 目标模块

```
backend/context_budget/
├── __init__.py
├── manager.py      # ContextBudgetManager
├── models.py       # ContextUsage / ToolResultRef / ArtifactStore Protocol
├── tool_guard.py   # L1 ToolResultBudgetGuard
├── micro_compactor.py  # L3 compact_previous_outputs
└── metrics.py      # 指标挂钩
```

## 核心设计原则

1. **原始聊天记录不能删除**：裁剪/压缩只影响发送给模型的 active context；不 DELETE chat_messages、不覆盖原始 user/assistant 内容；前端历史仍完整展示。
2. **工具输出不属于聊天历史**：L1 针对工具执行结果（step_results 入口前），L3 针对 step_results/previous_outputs；不把工具压缩逻辑放进聊天历史裁剪。
3. **所有阈值以 token 为准**：统一用 count_tokens；字符数仅作极端 fallback。禁止 50K 字符/2KB 类字符阈值。
4. **不影响 Router Prefilter 顺序**：CS/Travel/Selection 三个 prefilter 顺序不变，守护测试 test_router_prefilter_order.py 必须继续通过。

## 配置（backend/config/memory.py，env 可覆盖）

```python
CONTEXT_BUDGET_ENABLED = True
CONTEXT_OUTPUT_RESERVE_TOKENS = 768
CONTEXT_SAFETY_RESERVE_TOKENS = 256
TOOL_INLINE_MAX_TOKENS = 768
TOOL_PREVIEW_MAX_TOKENS = 256
PREVIOUS_OUTPUTS_MAX_TOKENS = 1024
CONTEXT_L4_TRIGGER_RATIO = 0.80
CONTEXT_L5_TRIGGER_RATIO = 0.90
```

LLM_CONTEXT_LENGTH=4096 复用 config/llm.py，不复制第二份。业务代码禁止直接 os.getenv。

## ContextBudgetManager

```python
get_input_budget()  # = LLM_CONTEXT_LENGTH - OUTPUT_RESERVE - SAFETY_RESERVE = 3072
calculate_usage(messages=None, extra_texts=None) -> ContextUsage
# ContextUsage: used_tokens / input_budget / remaining_tokens / usage_ratio
```

## L1：工具结果预算（本轮最重要）

- 拦截位置：**Skill Executor / Tool Executor 统一拿到工具执行结果的位置**（工具执行完成 → 写入 step_results 之间）；不能只改 tools/map/_base.py::ok()（18 个老 Tool 返 Markdown 不经过它）。
- 行为：count_tokens(result) <= TOOL_INLINE_MAX_TOKENS 保持原样；超过则转 preview 结构：
  `{"context_compacted": True, "type": "tool_result_preview", "original_tokens": N, "preview": "..."}`，preview 按 token 截取 ≤ TOOL_PREVIEW_MAX_TOKENS（可新增 truncate_text_to_tokens）。
- 预留 ToolResultRef（original_tokens/inline_tokens/preview/truncated/artifact_id=None）与 ArtifactStore Protocol（save/get），本次不实现复杂存储。

## L2：基础历史裁剪

- 复用 trim_messages_to_budget；SystemMessage 保留、当前 user message 保留、最近消息优先、超预算丢最旧 active context。
- HISTORY_TOKEN_BUDGET=2048 改造为**最大值**，实际预算 = min(2048, max(0, input_budget - system - current_query - memory - rag - previous_output_tokens))。
- 统一计算入口收在 ContextBudgetManager，不再各模块独立写死 token budget。

## L3：previous_outputs MicroCompact

- 修改 orchestration/supervisor/scheduler.py 的 previous_outputs 构造，加 compact_previous_outputs。
- 规则1：全部 previous_outputs ≤ PREVIOUS_OUTPUTS_MAX_TOKENS=1024。
- 规则2：超预算优先保留最新 step result，旧结果降级为 {step_id, tool, status, preview, compacted: True}，不无声丢失。
- 规则3：L1 已 truncated=True 的结果不允许重新获取或拼回完整结果。

## 统一 Prompt Preflight

```python
prepared = context_budget.prepare_llm_context(
    messages=..., previous_outputs=..., rag_context=...)
# -> PreparedContext(messages, previous_outputs, usage)
```

流程：L2 history trim → L3 previous_outputs compact → 重新 count_tokens → 确认 ≤ input_budget。仍超 hard budget 则 deterministic trim（优先级：旧 history → 旧 previous_outputs → RAG 尾部证据），保留 System Prompt / 当前用户问题 / 最新必要业务状态。最后仍无法满足：记录 warning + metric +1 + 安全降级，禁止 except Exception: pass，不静默调用模型。

## L4/L5 预留

- should_context_collapse(usage) → usage_ratio >= CONTEXT_L4_TRIGGER_RATIO；async context_collapse(...) 预留（NotImplementedError 或暂不调用）。
- should_auto_compact(usage) → usage_ratio >= CONTEXT_L5_TRIGGER_RATIO；async auto_compact(...) 预留。不大规模改造 SessionMemory.summarize()。

## SSE context 事件

```json
{"type": "context", "level": "L1", "action": "tool_compact",
 "before_tokens": 5200, "after_tokens": 250, "saved_tokens": 4950}
```

不在 SSE 里发完整工具结果。done 里优先复用 context 事件携带最终 context_usage（或独立 context_usage 事件）。

## 前端

- frontend/src/api/client.ts 加 case "context" + ContextEvent 类型（level: "L1"|"L2"|"L3"|"L4"|"L5"）。
- 聊天区灰色系统提示条「已优化上下文 · 节省 xxx tokens」；仅 UI runtime event，不写库、不污染聊天历史。
- Header/输入框附近显示「上下文 xx%」，数据来自后端（meta/done/context 事件更新）。

## Metrics（observability/metrics.py，低基数）

- context_compactions_total{level, action}
- context_tokens_saved_total{level}
- context_budget_overflow_total{stage}
- 禁止 session_id/user_id/turn_id 进 Prometheus label（Trace 可记 ID）。

## 日志与 Trace

compact 时结构化日志 logger.info("context_compacted", level=..., action=..., before_tokens=..., after_tokens=..., saved_tokens=...)；已有 Langfuse/OTel span 则加 context_budget.prepare / tool_guard / micro_compact span，不另起 tracing 系统。

## 测试要求（全部 --no-cov）

- L1：小结果不压缩；大结果生成 preview；preview ≤ TOOL_PREVIEW_MAX_TOKENS；original_tokens 正确；老 Markdown Tool 与新 JSON Tool 都走统一 Guard。
- L2：不足预算不裁剪；超预算丢最旧；SystemMessage 保留；当前 user message 保留。
- L3：≤1024 不处理；>1024 compact、最新优先、总 token ≤ budget。
- Preflight：最终 prepared context ≤ input_budget。
- Router：pytest tests/orchestration/graph/test_router_prefilter_order.py --no-cov 必须通过。

## 实施顺序

P0 git status + 梳理统一出口 → P1 模块+配置 → P2 L1 → P3 L3 → P4 L2 → P5 Preflight → P6 metrics/logs → P7 SSE → P8 前端 → P9 测试 → P10 终检 git status。

## 多会话并行红线

修改前 git status/diff；不覆盖无关改动、不 reset、不 checkout 覆盖、不清理未提交文件；完成后再次 git status/diff，只总结本次修改。

## 禁止事项

❌ 大规模重构 LangGraph ｜ ❌ 改 CS/Travel/Selection 路由 ｜ ❌ 删除 chat_messages ｜ ❌ 一次实现完整 L4/L5 ｜ ❌ 新建第二套会话状态系统 ｜ ❌ 本地临时文件做跨容器 Artifact Store ｜ ❌ 业务代码直接 os.getenv ｜ ❌ except Exception: pass ｜ ❌ 每个 Agent 各写一套 token budget ｜ ❌ 字符数代替 token 作核心预算

## 完成标准

1. 所有工具结果进 step_results 前都有 token budget；2. 单个大工具结果不直接塞进 previous_outputs；3. previous_outputs 有统一总预算；4. 历史消息预算由 ContextBudgetManager 统一管理；5. LLM 调用前有统一 Preflight；6. 原始 chat_messages 完整保留；7. 前端能收 context SSE；8. 用户可见「已优化上下文 · 节省 xxx tokens」；9. Prometheus 可统计 L1/L2/L3 次数与节省 token；10. L4/L5 已预留；11. 原有 CS/Travel/Selection/RAG/SQL/Report 不受影响。
