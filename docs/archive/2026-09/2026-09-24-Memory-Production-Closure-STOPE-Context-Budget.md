# Memory Production Closure — STOP E 实施报告：L1/L2/ContextBudget 职责边界与 Token 生命周期收口

> 日期：2026-09-24 ｜ 前置：STOP A（`79d4668`）/ B（`3e16e66`）/ C（`538145a`+`d306be5`）/ D（`6d157ac`）均冻结
> 本轮收口 **Memory L1/L2 与 ContextBudget 的职责边界**（E1-E10），不做 STOP F 的 threshold 调优。

---

## 1. Verdict

**STOP_E_PASS=true**（E-I1~I10 逐条对照见 §12；测试证据见 §14）

## 2. Frozen Baseline

STOP A `79d4668`｜STOP B `3e16e66`｜STOP C `538145a`+`d306be5`｜STOP D `6d157ac`。本轮开工前 `git merge-base --is-ancestor` 五提交全部验证在 HEAD ancestry 内。

**本轮前置修复（与本 STOP 无关但必须先行）**：发现 ContextBudget 收口 STOP B（`69b2f4e`）是**悬空提交**（不在任何分支），其工作区内容与该提交逐字节一致但从未落入 main——导致 HEAD 的 `rag/chain.py:456` import 不存在的 `budget_rag_indices`（RAG 证据路径 import 即炸）。已按严格 pathspec 恢复为 `01bf8c8`（10 文件 diff-hash 逐一验证与 69b2f4e 完全一致、期间无其他提交触及，零回退），恢复后 `tests/context_budget` 182 passed。**ContextBudget 冻结基线自此在 main 上真实成立。**

## 3. Root Cause（本轮修复的三个缺陷）

1. **E8-P1｜end_turn 摘要阻塞请求关闭路径 + 稳态每轮重复摘要**：`end_turn` 在 SSE 流关闭的 finally 路径同步等待摘要协程（`MemoryManager._run` 至多等 5s）；触发是条数制（≥50 条）且无滞后门，稳态每轮 +2 条水位线增量恰好达到 L5 `MIN_DELTA=2` → **每轮一次摘要 LLM 调用**，且耗时超 5s 时误报 `memory_op_failed` 告警（实际后台仍在完成）。
2. **E3-P2｜fallback 全量摘要无输出帽**：`SessionMemory.summarize` 增量路径失败回退全量路径时 `llm.invoke(prompt)` 不带 `max_tokens`——异常 provider 可返回无限长摘要。
3. **E4-P2｜裁剪优先级从未被显式契约化**：预算压力下 L2/L3 数据块最先被裁（见 §5），该行为正确但无测试锁定、无文档记载。

## 4. Token Ownership Matrix（E1）

| Layer | 输入 | 输出 | 限制方式 | token owner | 允许丢数据 |
|---|---|---|---|---|---|
| L1 | raw messages | recent messages ≤20 | **条数**：`SHORT_TERM_MAX_MESSAGES=20`（`short_term.py:18-23`）；查询层 2×20（`service.py:97`） | memory L1 retention policy | 旧消息可丢（FIFO 环形） |
| L2 trigger | 全部历史 | summary | **条数**：`SESSION_MAX_MESSAGES=50`（`session_repo.py:67`）+ 水位线增量滞后门 ≥10（本轮新增，`config/memory.py`） | memory L2 policy | 旧历史由摘要替代（原始行永不删） |
| L2 summary | 旧摘要+增量 | ≤512 token 摘要 | **provider 级** `max_tokens=CONTEXT_L5_SUMMARY_MAX_TOKENS=512`（`auto_compact.py:417`；fallback 路径本轮补齐 `session.py`） | provider 输出帽（确定性） | 摘要失败保留旧摘要 |
| L3 | retrieved memories | memory_context 0~5 条 | **条数+分数**：`MEMORY_MAX_INJECTED=5` + gate ≥0.45（`retriever.py`） | memory L3 policy | gate 以下可丢 |
| 装配预裁剪 | L2 块+L3 块+L1 | 最终注入列表 | **token**：`history_budget ≤ HISTORY_TOKEN_BUDGET=2048`（`service.py:216-233`，预算**派生自** ContextBudgetManager.history_budget） | ContextBudgetManager（派生，非第二权威） | L2/L3 数据块→旧 L1 依次 |
| ContextBudget | assembled messages | final context | **token**：`prepare_llm_context`（`manager.py:109`），budget=min(窗口,模型窗口)-768-256-reserved | **最终唯一 hard-budget authority** | 旧 history→RAG→旧 PO；L4/L5 |
| LLM Proxy | final messages | provider payload | `_preflight_context`（`proxy.py:1573`）挂 invoke/ainvoke/stream/astream/generate/bind_tools 全形态；str 输入不过（内部摘要/提取调用，自有限额） | 同上（执行点） | 同上 |

**审计结论**：无第二 hard-budget authority——memory 预裁剪的预算数值直接调用 `context_budget.history_budget()` 派生（`service.py:221`），token 计数统一走 `context_budget.token_counter`；`MEMORY data 可能绕开 ContextBudget` 不成立（工具路径 `memory_search_tool` 结果以消息形态进 agent 循环，同样过 proxy preflight）。

## 5. Current Pipeline（E4，实测装配顺序）

```text
runner.py:399 start_session
  → [L2 policy SystemMessage, <historical_context> AIMessage]   ← 有摘要时插头部
  → [L3 policy SystemMessage, <memory_context> AIMessage]        ← 紧随其后
  → 最近 ≤20 条 L1 原始历史（连续去重）
  → memory 预裁剪：trim_messages_to_budget(budget=history_budget)
      贪心从最新往回保留；SystemMessage 全保留；超预算时丢弃顺序：
      L3 data → L2 data → 最旧 L1 → … → recent L1（先牺牲注入块）
  → 各节点装配 [业务 System prompt, *history, HumanMessage(当前问题)]
  → proxy _preflight_context（唯一 hard gate）：
      L2 trim（System+语义 pin 豁免）→ L4 collapse(≥0.80) → 确定性
      hard trim → L5 AutoCompact(≥0.90, fire-and-forget/内联)
```

- **当前 user query 永不被裁** ✓：proxy 语义 pin「最后一条 HumanMessage」+ 显式 PinnedContext（`pin.py::collect_pin_indices`，测试 §14-I1）。
- **核心 System policy 永不被裁** ✓：SystemMessage 无条件保留（两层一致）。
- **memory_context 允许被裁** ✓（无 System 豁免，§14-I3/I6 实测超预算即裁）。
- **L2 summary 允许被裁** ✓（同上）。
- **old L1 vs memory 谁先被裁**：memory 数据块先于 old L1（注入块是最旧位置且非 pin）。**该优先级本轮冻结为显式契约**——它是「summary+L3 不会挤没 recent conversation」的保证机制；代价是数据块被裁后其 policy SystemMessage 成为孤儿（悬空引用文案，cosmetic，P2 已登记 §15）。
- **tool result 裁剪优先级**：assistant(tool_calls)+ToolMessage 原子组整组保留/丢弃；**活跃** tool 对被 pin。

## 6. L1 Contract（冻结）

条数制：环形缓冲 20 条（`SHORT_TERM_MAX_MESSAGES`）；start_session 查询 2×20 后按 `(role,content)` 连续去重注入。L1 不做 token 控制——token 交由装配预裁剪与 ContextBudget 分层负责。

## 7. L2 Trigger Contract（冻结，本轮修正）

- 触发：`message_count ≥ SESSION_MAX_MESSAGES=50`（**条数制**；任务书假设的 `SESSION_SUMMARY_TRIGGER_TOKENS` 在代码中不存在，实测无需引入——L1 窗口 20 条 + proxy 硬预算已保证无溢出，token 触发只增加复杂度）。
- **滞后门（新增）**：水位线后可摘要增量 `≥ CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES=10` 才调 LLM（`service.py::_summarize_if_needed`）；不足则攒批。消除稳态每轮重复摘要。
- 摘要时机：end_turn 落库 save_turn 后**后台任务**执行（本轮修正，原为同步等待）。
- 原始消息处理：`chat_messages` 永不删改（L5 红线），摘要只推进 `summary_through_message_id` 水位线（CAS 原子）。
- 重复/抖动：水位线单调递增 + CAS 拒绝旧结果（stale_waterline）→ 无振荡；滞后门消除每轮重触发。

## 8. Summary Max Token Contract（冻结）

1. 输出帽：`max_tokens=CONTEXT_L5_SUMMARY_MAX_TOKENS=512`，provider API 契约级硬帽；**增量路径与 fallback 全量路径一致**（后者本轮补齐）。
2. 确定性保护：ProtectedFacts 校验补丁上界 = `CONTEXT_L5_MAX_PROTECTED_FACTS=40` 条追加 → 摘要总量确定性有界（≈512 + 补丁 ≤1.3k token）。
3. 配置化：`CONTEXT_L5_SUMMARY_MAX_TOKENS / _TEMPERATURE / _TIMEOUT_SECONDS` 全部 env 可覆盖。
4. 不依赖 prompt 措辞做 token 契约 ✓（「请简短总结」仅辅助）。
5. 摘要不随会话无限膨胀 ✓：每轮增量摘要把「旧摘要+新消息」重新压缩进同一输出帽（§14 E-I7 由帽+补丁上界共同保证；若 provider 违反 max_tokens 契约返回超长摘要，装配预裁剪与 proxy 硬裁剪兜底，不会溢出 provider 窗口）。
6. 失败回退：返回 None → 旧摘要+旧水位线保留（幂等可重试），主链不受影响（`test_summary_visibility.py` 既有锁定 + 本轮后台化后 `memory_summary_failed` metric 保留）。

## 9. L3 Contract（沿用 STOP D，零改动）

candidate 20 → SQL eligibility → gate ≥0.45 + global 白名单 → rank/merge → 0~5 条 → 安全数据块 → 仅注入条 mark_accessed。本轮仅验证其产物在预算链路中的行为（§14）。

## 10. ContextBudget Final Authority（冻结）

`prepare_llm_context` 是唯一最终裁剪点：L2 trim → L4 collapse（滞回 0.80→0.65）→ 确定性 hard trim（旧 history→RAG→旧 PO）→ L5 AutoCompact（≥0.90，失败安全回退）；执行点 = proxy `_preflight_context` 全调用形态；超限最终 warning + `record_overflow` + 安全降级放行。memory 层的 `history_budget` 调用是**同一 manager 的派生函数**（E-I9 成立）。

## 11. Role Safety（E6）

- L3：STOP D 已冻结（policy SystemMessage + `<memory_context>` AIMessage）；本轮零改动，§14-I10 回归通过。
- L2：审计确认已是同一模式（`build_historical_context`，`role_safety.py:48`）——摘要历史数据只进 AIMessage 数据块，SystemMessage 仅固定 policy 文本；auto_compact 的 `fold_rebuild` 重建投影同模式。**无「历史内容获得 system 权限」缺陷，无需修改。**

## 12. Invariants 对照（E7）

| Invariant | 状态 | 证据 |
|---|---|---|
| E-I1 当前 query 永不被挤掉 | ✓ | pin 语义 + `test_extreme_long_memory…`/`test_final_payload…` 断言 last message |
| E-I2 核心 system policy 不被替代 | ✓ | 两层 trim SystemMessage 无条件保留（断言内建于 `trim_messages_to_budget`） |
| E-I3 memory data 参与 budget | ✓ | `test_long_l2_l3_memory_participates_and_honors_budget`（真库超预算整块裁掉） |
| E-I4 summary data 参与 budget | ✓ | 同上（L2 数据块同被裁剪链处理） |
| E-I5 payload ≤ input budget | ✓ | proxy 预检断言 `used_tokens <= input_budget` 且 `overflow=False` |
| E-I6 极长 memory 不 overflow | ✓ | 4.2 万字记忆 → 装配层丢弃 + proxy 层不超限 |
| E-I7 summary 不无限增长 | ✓ | provider max_tokens=512 + 补丁上界 40 条（§8） |
| E-I8 summary failure 主链降级 | ✓ | 后台任务 fail-safe + `test_summary_visibility.py` 全部保留通过 |
| E-I9 无第二 hard-budget authority | ✓ | memory 预裁剪预算派生自 `context_budget.history_budget`（§4/§10） |
| E-I10 STOP D safe injection 不退化 | ✓ | `tests/memory` 85 passed（含 STOP D 20 项专项）零改动通过 |

## 13. Performance（E8）

- **normal turn**：读路径零变化（单 embedding + 单 DB 查询 + mark UPDATE）；新增滞后门判断 = 2 个轻量 SQL（`get_summary_state`/`summarizable_before_id` 仅在条数达标后执行）。
- **summary-trigger turn**：LLM 摘要调用从「每轮一次」变为「每 ≥10 条增量一次」；且不再阻塞流关闭（后台执行，原实测可阻塞至多 5s）。无新增每轮 LLM/embedding/RPC。

## 14. Tests

全部 `PGPORT=5433` + `--no-cov`：

| 轮次 | 范围 | 结果 |
|---|---|---|
| 1 | 新增 `tests/memory/test_stop_e_summary_contract.py`（4）+ `test_stop_e_budget_invariants.py`（3，真 PG 全链） | **8 passed**（首轮 4 failed→修复测试代码后全绿；断言差异均为 ProtectedFacts 补丁属正常行为） |
| 2 | `tests/memory` 全量（含适配后的 `test_summary_visibility.py`） | **85 passed**（STOP D 基线 77 + 新增 8） |
| 3 | `tests/context_budget` 全量 | **182 passed** |
| 4 | runner trace/stream + direct_flow_e2e + rag memory isolation 组合 | **11 passed** |

覆盖场景：normal short / below-trigger / 近阈值 / 跨水位线 / summary generated / summary bounded（帽断言）/ summary failure / long L2 + long L3 / long current query / extreme long memory / memory empty（0 注入合法为既有 STOP D 契约）。PostgreSQL 均为真实权威库（5433）。

## 15. Remaining Risks

- **P2 孤儿 policy SystemMessage**：数据块被裁后其固定 policy 文案仍保留（悬空引用，模型可能看到「接下来 <memory_context> 内…」却无数据块）。cosmetic、无安全影响；修复需动冻结的 `trim` 分组语义，收益不匹配风险，不做。
- **P2 str 输入不过 preflight**：内部摘要/提取等 `llm.invoke(str)` 不走预算链路（各自 prompt 自限）。既有设计，登记在案。
- STOP F：0.45 threshold 实证调优、Golden、irrelevant injection rate（本轮未动）。
- STOP G：真 provider 八场景 + `memory.retrieve` span + metrics 实机序列。

## 16. Changed Files

| 文件 | 变更 |
|---|---|
| `config/memory.py` | +`CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES=10` |
| `config/__init__.py` | 导出该常量（import + `__all__`） |
| `memory/service.py` | `end_turn`：save_turn 同步提交、摘要拆为 `_summarize_if_needed` 后台任务（条数触发 + 增量滞后门 + fail-safe metric） |
| `memory/session.py` | fallback 全量摘要补 `max_tokens`/`temperature`（与增量路径同帽） |
| `tests/memory/test_summary_visibility.py` | 适配后台化（捕获后台协程显式驱动，原断言意图不变） |
| `tests/memory/test_stop_e_summary_contract.py` | 新增 4 用例（触发口径/滞后门跳过/攒批恢复/输出帽） |
| `tests/memory/test_stop_e_budget_invariants.py` | 新增 3 用例（真 PG 装配级 E-I3/I4/I9 + E-I6/I5/I1 + E-I5/I10） |

## 17. Commit

`fix(memory): close session and context budget boundaries`（本 STOP 单一提交，hash 见 git log）
