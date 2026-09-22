# Context Budget 生产加固 · STOP A 源码审计报告

> 2026-09-23。逐文件审计，非文件名推断。行号以当日工作区为准。
> 范围：`backend/context_budget/` 全部、`backend/memory/token_budget.py`、`backend/memory/service.py`、
> `backend/infra/llm/proxy.py`、`backend/orchestration/supervisor/scheduler.py`、
> `backend/orchestration/graph/direct_executor.py`、`backend/sql/migrations/040`、
> `backend/tests/context_budget/`、`backend/evaluation/context_budget/`。

---

## 1. 当前完整调用链

```
POST /chat/stream
  → GraphRunner.run (orchestration/graph/runner.py:380)
    → MemoryService.start_session (memory/service.py:65)
        ① load_messages(limit=SHORT_TERM_MAX_MESSAGES*2=40) → 连续去重 → ShortTermBuffer
        ② L2 摘要注入：SystemMessage("以下是本会话早期对话的摘要…")   (service.py:96-100)
        ③ L3 长期记忆注入：SystemMessage(LongTermMemory.format_for_prompt) (service.py:115-119)
        ④ 动态 history_budget + trim_messages_to_budget 裁剪           (service.py:150-185)
    → make_initial_state(messages=l1.messages) → 图执行
        → 各节点 LLM 调用一律 llm.invoke / llm.ainvoke / llm.stream / llm.astream
          → _LLMProxy.__getattr__ 包装（限流/韧性链/token 记录）      (proxy.py:1383-1521)
          → 【无 preflight，见 §2】
        → Skill 边界：guard_tool_result（L1）→ step_results           (tool_guard.py:84)
        → supervisor Send 注入前：compact_previous_outputs（L3）       (scheduler.py:253)
        → direct 路径：compact_previous_outputs（L3）                  (direct_executor.py:241)
  → end_turn → MemoryManager 后台摘要（另一套会话摘要，与本预算系统并行）
```

关键结论：**消息层只有 HumanMessage/AIMessage(+注入的 SystemMessage)**（memory/session.py:27-28、
memory/short_term.py:26-27）；全仓 0 处 ToolMessage、bind_tools 仅 tool_selector.py:335 一处
（响应直接解析，不进消息史）。OpenAI tool-call 消息对今天不存在于链路，属防御性缺口。

## 2. L1-L5 各接线点

| 层 | 接线点 | 状态 |
|---|---|---|
| L1 Tool Preview | BaseSkill 两条执行路径 + SQLSkill/BusinessAnalysis 重写 + direct_executor → `guard_tool_result`（tool_guard.py:84） | ✅ 已接线 |
| L2 History Trim | 仅 memory/service.py:150-185（start_session） | ✅ 已接线（但只此一处） |
| L3 PO Compact | scheduler.py:253-254、direct_executor.py:241-242 | ✅ 已接线 |
| L4 Collapse | 仅 manager.prepare_llm_context 内部（manager.py:181-215） | ❌ **生产死码** |
| L5 AutoCompact | 仅 manager.prepare_llm_context 内部（manager.py:254-257） | ❌ **生产死码** |

**最严重发现（本报告 #1）**：`_preflight_context`（proxy.py:1329-1374）全仓唯一应用点是
`_LLMProxy.__call__`（proxy.py:1531）。而：

- 业务层 44 处调用全部走 `llm.invoke/ainvoke/stream/astream`（`__getattr__` 包装路径，proxy.py:1393-1521，**不含 preflight**）；
- `llm(...)` 直调形态全仓 **0 处**（`grep -rnE "(^|[^.\w])llm\("` 业务目录空）；
- `_BoundLLMProxy.invoke/ainvoke`（bind_tools 路径，proxy.py:1266-1288）同样**无 preflight**。

⇒ 生产聊天链路上 L4/L5（含 prepare_llm_context 统一链）**永不触发**。Phase 5「生产验收」
（docs/2026-09-22-context-budget-phase5-生产验收报告.md:94）是经
evaluation/run_context_budget_eval.py:398 **直调 prepare_llm_context** 驱动的——组件被验证了，接线没有。

## 3. TokenCounter 当前实现 — 【未实现模型感知】(P0-1)

- 唯一计数器：tiktoken `o200k_base`（memory/token_budget.py:36），懒加载、失败永久降级
  「2 字符/token」粗估（token_budget.py:20,48）。
- `count_tokens(text)` / `count_message_tokens(msg)` **无 model/provider 参数**（token_budget.py:42,52）。
- 多模态：图片 part 计 **0 token**（token_budget.py:57-61）——规格 §22-L 明确违反。
- 业务层直接 import tiktoken：tool_guard.py:67（token 级截取）、config/guard.py、
  rag/preprocessing/token_counter.py、security/input_guard/format_checker.py、rag/embedding_singleton.py。
- input_budget = `LLM_CONTEXT_LENGTH(.env=8192) − 768 − 256 = 7168`，全局常量
  （manager.py:37-48、config/memory.py:43-44、config/chat_input.py:10），与目标模型无关。
- 模型注册表：DB `llm_models`（031/038/039 迁移）有 name/provider/driver/billing/model_kind/ocr_kind/
  upstream_name，**无 context_window / max_output_tokens / tokenizer_type 字段**
  （infra/llm/models.py `get_available_models()` 无此类键）。
- tools schema / response_format / provider wrapper：**完全不入预算**。preflight 只数 messages
  （proxy.py:1355-1356），且 bind_tools 路径根本不进 preflight。

结论：「Manager 判未超限、实际请求超 provider 窗口」今天完全可能（小窗口 ollama 模型、
大 tool schema、多模态输入）。

## 4. SystemMessage 动态内容风险 — 【实现有缺陷】(P0-2)

| 位置 | 内容 | 风险 |
|---|---|---|
| collapse.py:132,71-79 `SystemMessage_from_text(build_projection_text)` | 纯元数据（条数/范围/英文提示），**不含用户内容** | 低（但仍是动态 SystemMessage） |
| auto_compact.py:591-593 `fold_rebuild` → `SystemMessage(f"{L2_SUMMARY_MARKER}…\n{summary_text}")` | **LLM 生成的用户历史摘要** | **高：role escalation**——用户说「忽略之前所有指令」，经摘要包装后以 system 权重回到 prompt |
| memory/service.py:97-99 L2 摘要注入 | 同上模式（DB 会话摘要） | 高（同一根因） |
| auto_compact.py:157-172 `validate_and_patch` 追加 `[关键实体]` | 实体值（来自用户消息） | 中（随摘要一起进 SystemMessage） |

无 `<historical_context>` 结构边界、无 untrusted-data 声明。static system prefix 稳定性：
现有节点 prompt 本就含动态内容，prefix cache 收益暂不存在，改造无回退损失。

## 5. L5 并发模型 — 【单进程单飞，跨进程裸奔】(P1-1)

- `manager.py:265-266`：`_l5_inflight: set[str]` + `threading.Lock`——**仅进程内**。
- 多 FastAPI worker / 多容器副本下，两个 worker 可同时对同一 session 摘要。
- 无任何分布式锁。仓库已有 Redis 客户端可复用：infra/redis/client.py。
- 触发检查 `get_current_session_id()`（manager.py:291-301）取自 ContextVar
  （core/request_context.py:30，默认 "multi-agent-default"，skip 名单含 default 两个值）。

## 6. watermark DB 写法 — 【无 CAS，后写者必胜】(P1-1)

`SyncMemorySummaryStore.save_summary_state`（auto_compact.py:265-283）：

```sql
UPDATE public.chat_sessions SET summary=%s, summary_through_message_id=%s,
       summary_token_count=%s, summary_updated_at=NOW() WHERE session_id=%s
```

无条件 UPDATE。规格 §八的场景（A through=100 后完成覆盖 B through=105）**今天必然发生**。
migration 040 仅有 `summary_through_message_id / summary_token_count / summary_updated_at`，
**无 summary_version / summary_source_hash / summary_model**。读侧
`get_summary_state`（auto_compact.py:210-225）与写侧之间也无任何竞态防护。

## 7. thread-local / ContextVar 情况 — 【实现有缺陷】(P1-1c)

- 递归守卫：`manager.py:267` `_l5_thread_local = threading.local()`，`_run_l5` 置位（manager.py:341,346）。
- 但 async 上下文（生产主路径）走 fire-and-forget：`manager.py:326-338` `loop.create_task(run_auto_compact_async)`
  → `asyncio.to_thread(run_incremental_summary)` → `_invoke_llm_with_timeout` 又 submit 到
  `_llm_executor`（auto_compact.py:290,382）。
- `ThreadPoolExecutor.submit` **不传播 contextvars 也不共享 threading.local**：
  摘要 LLM 的 preflight 重入防护，实际靠「executor 线程里 `get_current_session_id()` 取不到值
  → 命中 skip 名单」**意外成立**。脆弱：任何人给 submit 加 context 传播即失效。
- 应改 `contextvars.ContextVar` + 提交时显式 `copy_context().run`（或 await inside context）。

## 8. message trim 算法 — 【实现有缺陷】(P1-2)

- `trim_messages_to_budget`（token_budget.py:69-99）：SystemMessage 全保留；**逐条**从新到旧，
  单条放不下就 `continue`（不 break）→ 保留集合**非连续后缀**，可出现「丢中间新消息、留更老小消息」。
- `_trim_keep_last`（manager.py:431-444）：把 `messages[-1]` 摘出保住——位置法定义"当前问题"。
  今天消息层只有 Human/AI/System（§1），位置法暂未误伤；但语义 pin（confirmation/业务实体/
  活跃 tool 对）概念不存在。
- **tool 原子组**：逐条独立丢弃，会把 `assistant(tool_calls)` 与 `ToolMessage` 拆散。现网消息层
  无 ToolMessage（§1）→ 潜在缺口，trim 函数需要原子组保证 + 测试（规格 §十一）。
- L4 fold 边界按 user 消息切轮（collapse.py:107-112），轮内消息整段同折，今天不拆对；
  但 fold 候选切片 `messages[fold_start:fold_end+1]` 会**吞掉夹在中间的 SystemMessage**
  （除头部外），当前注入策略下头部 System 在 fold_start 之前，暂无实害。

## 9. L3 dependency 信息来源 — 【未实现 dependency-aware】(P1-3)

- 现状 latest-first：按 dict 插入序从尾往前完整保留（micro_compactor.py:113-115）。
- 可复用的依赖信息（均在 scheduler.py:231-252 现场可用）：
  `plan.edges`（全 DAG）、`edges.get(item.step_id)`（当前步直接前驱 = 注入集合）、
  meta `{step_id, tool=capability, status}`、`step_results[*].capability`。
- 降级结构统一 `{step_id, tool, status, preview, compacted}`（micro_compactor.py:68-77），
  无 SQL/RAG/业务动作/HTTP/Report 分型压缩（规格 §十三）。L1 preview 不可拼回的约束已实现
  （micro_compactor.py:57-66 + tool_guard.is_compacted_preview）。

## 10. RAG trim 策略 — 【部分实现，机械尾删】(P2-1)

- `trim_texts_to_budget`（token_budget.py:102-126）：顺序保留、超预算尾部整体丢。
- 正确性完全依赖「输入序 = 价值序」假设。RAG 链有 rerank（tests/test_chain_rerank_order.py），
  但 hard trim 的 `rag_context` 由调用方透传（manager.py:234-239），manager 不感知
  rerank_score / parent-child / source 归属 → 同文档多 chunk 可整簇消失、无 source diversity 保证。
- 无 RAGBudgeter。

## 11. metrics — 【部分实现】(P2-4)

已有（observability/metrics.py 定义，context_budget/metrics.py 引用）：
`context_compactions_total{level,action}`、`context_tokens_saved_total{level}`、
`context_budget_overflow_total{stage}`、`context_compaction_latency_seconds{level}`、
`context_autocompact_llm_tokens_total{kind}`、`context_l5_total{status,reason}`（reason 固定枚举
防基数，metrics.py:94-97）、`context_protected_facts_total{type,result}`（低基数映射 :98-118）、
SSE context 事件（ContextVar sink + 进程级 64 条缓冲，:148-223）。

缺（对照规格 §二十）：分项 token 分解（system/history/rag/po/tool_schema/response_format）、
消息数前后/投影数、po/rag 条数、`l5_lock_conflict` / `l5_cas_conflict` reason、
`token_counter_provider` / `token_counter_estimated`、predicted usage。
日志面：现有日志只记 token 数/条数/id（manager.py:474-476、auto_compact.py:445-452），
未见对话全文/RAG 全文输出 → 泄露面当前干净（保持即可）。

## 12. 测试覆盖 — 【部分实现】

已有 6 文件约 60 用例（tests/context_budget/）：L4 折叠语义 7、manager 17（预算公式/env 覆盖/
裁剪保留 system+last/overflow 标记/不突变）、L1 guard 12（含 Skill 双路径集成）、L3 6、
ProtectedFacts 6、auto_compact 12（mock LLM 增量/失败回退/水位线）、phase5_hardening。

Golden 评测：**已存在** evaluation/context_budget/{golden_eval,driver,waterline}.py +
datasets/context_budget_golden.json + evaluation/run_context_budget_eval.py——覆盖
long_history_recall / protected_fact_recall / patched 分层（真实 DB+真实 LLM 双轨）。

缺（对照规格 §22/§23）：B 当前消息非 user 尾部、C tool 原子组、D injection role 安全、
E/F 并发与 CAS race、G dependency-aware、I RAG budget、K 模型切换重算、L VL、
overflow_rate；golden 的 dependency/rag_evidence/tool_protocol/injection/model_switch 维度。

---

## 13. 问题分类总表（对照任务优先级）

| # | 任务项 | 分类 | 一句话依据 |
|---|---|---|---|
| 0 | **preflight 接线（审计新发现）** | **实现有缺陷（生产死码）** | preflight 只挂 `__call__`（proxy.py:1531），业务 44 处全走 invoke 族，`llm(` 直调 0 处 → L4/L5 生产永不触发 |
| 1 | P0-1 模型真实 Token Budget | **未实现** | tiktoken 全局唯一（token_budget.py:36）；budget 公式无 tools/response_format/multimodal/模型窗口；llm_models 无窗口字段 |
| 2 | P0-2 动态历史禁升 SystemMessage | **实现有缺陷** | fold_rebuild/auto_compact.py:591-593 与 memory/service.py:97-99 把 LLM 摘要包成 SystemMessage；无 untrusted 边界 |
| 3 | P1-1a L5 多 Worker 单飞 | **未实现（跨进程）** | `_l5_inflight` 进程内 set（manager.py:265）；无 Redis 锁 |
| 3 | P1-1b watermark CAS | **未实现** | save_summary_state 无条件 UPDATE（auto_compact.py:271-275）；无 version 字段（040 迁移） |
| 3 | P1-1c async 重入守卫 | **实现有缺陷** | threading.local（manager.py:267）管不到 executor 线程；现靠 ContextVar 断链"意外"防重入 |
| 4 | P1-2 Semantic Pin | **未实现** | `_trim_keep_last` 位置法（manager.py:431-444）；无 confirmation/实体 pin |
| 4 | tool 原子组 | **未实现（潜在）** | trim 逐条独立丢（token_budget.py:88-97）；现网消息层无 ToolMessage，属防御缺口 |
| 5 | P1-3 dependency-aware L3 | **未实现** | latest-first 按插入序（micro_compactor.py:113-115）；DAG 信息在 scheduler 现场可用未用 |
| 5b | L3 工具类型分型压缩 | **未实现** | 降级结构通用 preview 单一形态（micro_compactor.py:68-77） |
| 6 | P2-1 RAG relevance budget | **部分实现（有缺陷）** | 机械尾删（token_budget.py:102-126），不感知 score/source 分组 |
| 7 | P2-2 predicted usage + 滞回 | **未实现** | 只看当前用量（manager.py:187,276）；`CONTEXT_L5_TARGET_RATIO` 存在但仅打日志（manager.py:384），不驱动压缩目标；L4 无 target |
| 8 | P2-3 ProtectedFactRegistry | **部分实现** | 正则抽取+supersede+补丁已好（auto_compact.py:94-172）；来源仅 chat 文本，无业务态/结构化输出/确认态，无 priority |
| 9 | P2-3b Projection 可追踪 | **部分实现** | ContextFold 元数据齐（collapse.py:30-68）但 **FoldRegistry 全仓无实例化**（死类）；L5 摘要无版本/来源范围元数据 |
| 10 | P2-4 观测升级 | **部分实现** | 见 §11 缺口清单 |
| 11 | 日志不泄露内容 | **已实现（保持）** | 日志仅 token 数/条数/id/hash 类 |
| 12 | 配置收口 | **部分实现** | config/memory.py 集中；缺 L4_TARGET、LOCK_TTL；无重复 env 名 |
| 13 | 红线：原始 chat_messages 不变 | **已实现** | 全链只动 active context；L5 只推进水位线（040 迁移注释明确红线）；测试 messages_not_mutated 等覆盖 |
| 14 | Golden 评测 | **部分实现** | 已有事实保真双轨评测；缺 injection/dependency/RAG/tool-protocol/model-switch/overflow 维度 |

## 14. STOP B 实施口径建议（P0，含接线修复）

1. **接线修复（P0 前置）**：`_preflight_context` 挂进 `__getattr__` 的 invoke/ainvoke 与
   `_BoundLLMProxy.invoke/ainvoke`（stream 族仅 fast-path 计数，不做结构改写——流式输入形态
   与 invoke 相同可安全走同一入口；异常软失败原样放行不变）。
2. **TokenCounterRegistry**：`backend/context_budget/token_counter.py`（registry + calibrated
   fallback），`count_tokens(text, model=None)` 兼容旧签名；预算侧接 `llm_models` 注册表
   （新增 `context_window` 列，migration 044，兼容旧数据回填默认）+ tools/response_format 计数。
3. **去 SystemMessage 化**：L4 projection 与 L5 摘要改为「固定 system policy 声明一次 untrusted +
   HumanMessage 包 `<historical_context>` 数据」（方案 A）；memory/service.py L2 注入同步改。
   L2_SUMMARY_MARKER 识别逻辑同步兼容新旧两种形态（fold_rebuild / _is_replaceable_summary）。

红线不变：L1-L4 保持零 LLM、确定性；L5 失败不阻断；原始消息不动。
