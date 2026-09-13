# 上下文保持与"自我发挥"防护改造方案

> 基于三路代码审计（记忆/CS 图、RAG 防线/trace、Reporter/计划解析）的事实提取，逐项给出改动点。
> 行号以 2026-09-14 工作区为准，动手前先 `git diff` 核对。

## 0. 总览表

| ID | 优先级 | 改动面 | 收益 | 风险 | 需确认 |
|---|---|---|---|---|---|
| R1 reporter prompt 修复 | **P0** | 2 文件 | 堵住最大自我发挥口子（禁止编造规则从未生效） | 低 | 模板变量不匹配是否已知 bug |
| R2 摘要失败可见化 | **P0** | 2 文件 | 消除最难排查的静默上下文丢失 | 无行为变化 | 否 |
| R3 RAG fail-visible | **P0** | chain.py + metrics.py | 异常时防线状态可观测 | 无行为变化 | 否 |
| R4 CS 子图接入对话历史 | **P0** | 4 文件 + 开关 | 修复客服多轮失忆 | 中（token/行为变化） | CS 无历史是否刻意设计 |
| R5 计划解析 repaired 标记 | P1 | 2 文件 | 垃圾计划可追溯 | 无行为变化 | 否 |
| R6 Reporter 编造检测 | P1 | 1 文件 | 数字编造量化 | 低 | 否 |
| R7 跨轮中间态摘要注入 | P2 | 3 文件 + migration | 多轮引用上轮结果 | 中 | checkpointer 策略取向 |
| R8 专家正文摘要传递 | P2 | 2 文件 | 链式专家拿到前位结论 | 低 | 否 |
| R9 decay 调度入口 | P2 | 1-2 文件 | L3 记忆真正衰减 | 低 | 否 |
| R10 上下文注入量观测 | P2 | runner + tracer | 看板基础数据 | 低 | 否 |

---

## 1. P0 逐项详情

### R1 Reporter prompt 修复（审计新发现，优先级最高）

**问题（两条叠加）**：
1. `reporter_system.yaml` 第 12 行的"禁止编造任何不存在于 step_results 中的数字、日期、金额、百分比、事实陈述"**从未到达 LLM**——`reporter.system` 这个 key 在全项目无任何 `.py` 调用方；全 LLM 路径实际渲染的是 `reporter.summary`（reporter.py:174）。
2. `render_sync("reporter.summary", question=..., outputs_text=...)` 传的变量与 yaml 声明的 `data_summary` **不匹配**（registry 见 backend/prompts/registry.py:60-73）——全 LLM 路径的数据输入可能残缺。
3. 另：单步骤透传旁路 reporter.py:129-133（output>5 字符即裸透传）排在 structured 渲染之前，**单条 SQL 结果也是裸透传**，不走表格渲染。

**改动点**：
- `backend/agents/reporter/reporter.py:172-186`：改为
  - `system = prompt_service.render_sync("reporter.system")`（激活禁编造规则），human 用 `reporter.summary` 渲染且**变量对齐**：把 `_build_data_summary`（:438-459）的产物传入 `data_summary`，`outputs_text` 继续走 `_format_step_outputs`（:317-343）；
  - 两份 yaml（`backend/prompts/defaults/reporter_summary.yaml`、`reporter_system.yaml`）的 variables 声明同步对齐；
  - 单步骤透传旁路（:129-133）加条件：`capability == "sql.query"` 或 output 含表格特征时不透传，落到 structured 路径。
- `reporter.py:153-159`（一句话总结 invoke）：加 `max_tokens=64`。

**配置/开关**：无（修 bug 性质）。注意 DB 中如有 `reporter.summary` 的 active_version，发布新 yaml 后需经 `/api/prompts` 发布或 cache bust（service.py:261）才生效。

**测试**：`tests/agents/test_reporter_prompt_contract.py`——断言渲染后的 system 消息含"禁止编造"；断言 `data_summary` 非空；单步 sql.query 不走裸透传。

**验收**：`python -c` 调 `generate_final_answer` 打印完整 prompt，肉眼确认规则 2 在 system 内。风险/回滚：纯 prompt 内容变化，出问题回滚 yaml 即可。

### R2 摘要失败可见化

**问题**：`backend/memory/manager.py:61-70` `_run()` 把所有 DB/摘要异常静默吞为 `None`（loop 未就绪、超时 5s、异常三个路径全静默）；`backend/memory/session.py:52-54` 摘要失败降级为 `conversation[:500]` **并落库覆盖**旧摘要。超过 10 轮（L1 截 20 条，short_term.py:23）的消息在摘要失败时两头落空且无迹可寻。

**改动点**：
- `manager.py:61-70`：`except Exception` 分支加 `logger.error(f"[MemoryManager] end_turn 失败: {e}")` + `degradation_alerts_total{code="memory_end_turn_failed", level="warn"}.inc()`（现有指标，backend/observability/metrics.py）。
- `session.py:52-54`：失败时**不调用** `update_summary`（保留旧摘要），`conversation[:500]` 只进日志；warning 升 error。
- `runner.py:312-316`：`end_turn` 改为返回 bool，False 时在 trace 上 `add_event("memory_end_turn_failed", level="warn")`（trace_collector 在 iter_events 作用域内可取 current）。

**测试**：mock `srepo.needs_summarization` 抛超时 → 断言：日志含 error、`degradation_alerts_total` +1、DB 中 summary 保持旧值。

**验收**：人为断开 DB 跑一轮对话，`/metrics` 出现该 counter，日志无静默。回滚：无行为变化，直接 revert。

### R3 RAG fail-visible（不改 fail-open 语义，只补可见性）

**事实**：9 处 fail-open 中 6 处完全无 trace 标记；Gate1/Gate2 异常放行后 span 仍标 `status="success"`，trace 上与正常通过不可区分（chain.py:945-946、:1019-1021）。`backend/rag/tracer.py` 是 re-export shim，真实实现在 `backend/observability/tracer.py`（Span.status 支持 success/error/skipped；`_fold_skipped_spans` 会把 skipped 折叠进 root.metrics["skipped_stages"]）。

**改动点（backend/rag/chain.py）**：

| 位置 | 现状 | 改为 |
|---|---|---|
| :851-854 `_gate_wrap_retrieve` | warning + 透传 docs | + span.add_event("gate_degraded", level="warn", attributes={"layer":"pre_wrap","error":str(e)[:200]})，函数需拿到 trace（改签名传参或 `trace_collector.current()`） |
| :926-929 Gate1 反序列化失败 | 透传 | 同上，layer="gate1_deserialize" |
| :940-946 Gate1 异常 | passthrough + span success | 同上，且 span status 改 `"skipped"` |
| :976-977 实体校验忽略 | warning 忽略 | 同上，layer="gate1_entity" |
| :989-992 风险等级降级 | debug + low | 同上，layer="risk_level"，level="info" |
| :1014-1021 Gate2 异常 | passthrough + span success | 同上，layer="gate2"，span status `"skipped"` |
| :1140-1145 ClaimVerifier | span skipped（已有） | + counter（下） |
| :1192-1196 Faithfulness | span skipped | + counter；**修 NameError 隐患**：`faith_span = None` 预初始化（:1166 import 失败时 except 块引用未定义变量会二次炸） |
| :138/:120/:153 prompt 静默落 DEFAULT | `except: pass` | + `logger.warning("[RAGChain] prompt_service 不可用，rag.qa 回退内置默认")` |

**trace 事件规范**：
- 事件名：`gate_degraded`；attributes：`{"layer": str, "error": str[:200], "action": "passthrough" | "skip_check" | "default_prompt"}`
- span.metrics 附加键：`gate_degraded_layer`
- span status：异常放行一律 `skipped`（复用现有折叠机制，不动 `_aggregate_status` 的 rejected > error > success 优先级）

**指标与告警**（backend/observability/metrics.py）：
- 新增 `rag_gate_degraded_total{layer}` Counter（layer 取上表 8 个值）。
- 告警规则建议：`rate(rag_gate_degraded_total[5m]) > 0.1`（layer 维度，即 5 分钟内同一 layer 超过 ~3 次）触发 warn；持续 30 分钟触发 critical（说明防线长期失效，非偶发）。
- 现有 `rag_reject_rate > 0.3`、`rag_hit_rate < 0.7` 告警保留不变。

**是否阻断/降级**：全部保持现 fail-open 行为，不阻断；SelfCorrection 失败链（:745-747 → `_reject`）已自带硬出口，不动。**顺带决策项**：`ENABLE_FAITHFULNESS` 默认 false（config/rag.py:261），faithfulness 阈值拒答（:565-589）默认整条防线不启用——建议灰度开启后再评估。

**测试**：monkeypatch gate 评估抛异常 → 断言 span.status=="skipped"、root.metrics["skipped_stages"] 含对应 gate、counter +1、最终答案仍正常产出（fail-open 不回归）。

### R4 CS 子图接入对话历史

**问题**：`cs_graph_node.py:34-40` 只传 `user_message/user_id/session_id/conversation_id/cs_route` 五字段；`CSGraphState`（graph_state.py:39-75）无历史字段；cs_supervisor LLM 兜底只看 `user_message[:200]`（supervisor.py:238）。客服多轮指代必然失忆。

**与 conversation_id 状态机的关系**：`cs_state_loader`（graph_builder.py:40-74）经 `load_snapshot` 恢复的是**流程状态**（conversation_status/handling_mode/handoff_state/confirmation_state/pending_action），管"进行到哪一步"；chat_history 管"说过什么"。二者正交，R4 不触碰状态机，新增字段纯增量。

**改动点**：
1. `backend/customer_service/graph_state.py:39-75`：CSGraphState 新增 `chat_history: list`（输入段）；`new_cs_graph_input()`（:77-108）加参数 `chat_history: list | None = None`。
2. `backend/orchestration/graph/cs_graph_node.py:34-40`：从主图 state 取 `state.get("messages", [])`（runner 经 make_initial_state 已注入 L1 恢复的近 20 条），过滤 SystemMessage 后取**最近 10 条**，每条 content 截断 [:500]，传入 cs_input。N 取值理由：L1 上限 20 条中一半，CS 子图 recursion 上限内 5 专家+supervisor 各自 prompt 增量可控（10×500≈5k 字符）。
3. 注入消费点（两处，最小侵入）：
   - `backend/customer_service/supervisor.py:235-243`：prompt 加一行 `f"最近对话:\n{history_text}"`（复用截断）；
   - `backend/customer_service/experts/base.py`（run_expert_safely，:50-104）：提供 `get_history_text(state) -> str` helper，knowledge/query/complaint 三个走 LLM 的专家在各自 prompt 拼接（action/handoff 是流程编排，不注入）。
4. Token 保护：helper 内先按条数后按字符双截断，总量 >4000 字符时只保留最近 5 条。

**配置/开关**：`CS_CHAT_HISTORY_ENABLED`（默认 true，backend/config/customer_service.py）+ `CS_CHAT_HISTORY_MAX_MESSAGES`（默认 10）。出问题关开关即回滚。

**测试**：`tests/customer_service/test_cs_history_injection.py`——mock MemoryService 返回含"订单 A123"的历史，user_message="那个订单发货了吗"，断言 knowledge 专家收到的 prompt 含 "A123"；开关关闭时不含。

**验收**：真实两轮对话（第一轮问订单、第二轮指代），trace 中 knowledge span 的 input 含历史。

**风险**：prompt 变长（cost+latency）；历史含 PII 注入客服 prompt——复用 memory 层已有 PII 清洗（service.py store 管线 L172-212 的 PII 步骤仅在写入侧，读取侧需确认，若无需在 helper 复用同一脱敏函数）。

---

## 2. P1 逐项详情

### R5 计划解析 repaired 标记

**问题**：`backend/shared/json_extractor.py` Layer3 修复（:78-91）与 Layer4 暴力正则（:94-102）会把混在解释文字里的半成品 JSON 抢救成"合法计划"，直传 Supervisor，无任何痕迹。叠加事实：planner `max_tokens=1024`（config/llm.py:107）可能截断长计划 JSON → **制造**更多 Layer3/4 的输入。

**改动点**：
- `json_extractor.py`：`_run_pipeline`（:105-153）每层成功返回时记录层号；新增 `extract_json_with_meta(text) -> tuple[dict, dict]`，meta=`{"layer": 0-4, "repaired": layer >= 3}`；`extract_json_or_empty` 保持兼容（内部调新函数丢弃 meta）。
- `backend/agents/planner/planner.py:135`：改用带 meta 版本；`repaired=True` 时：
  - `state["alerts"].append({"type": "plan_json_repaired", "layer": L})`（alerts 字段已存在于初始 state，events.py:195-215）；
  - `logger.warning(f"[Planner] JSON 经 Layer{L} 修复，原始输出前 200 字: ...")`；
  - （可选）同 cache key 记录，评估时按 session 聚合。
- **是否影响 Supervisor/阻断**：不阻断。`normalize_plan`（plan_utils.py:32-77）的白名单（:43-45 丢弃非法 capability）已防垃圾 capability 直传；标记只为可追溯。
- **配套**：planner.py:123-128 invoke 加 `config={"timeout": LLM_REQUEST_TIMEOUT}` 显式化；评估 `PLANNER_LLM_MAX_TOKENS` 1024 → 2048 的截断率（先观测再改）。

**测试**：输入 `"好的，这是计划：{"nodes": {"1": ...}} 以上"` → 断言返回计划 + meta.repaired=True + alerts 落盘。

### R6 Reporter 编造检测（观测，不阻断）

**改动点**：`reporter.py` 全 LLM 路径生成后（:205 之后）加 `_fabrication_check(final, outputs_text)`：
- 证据数字集：`re.findall(r'\d+(?:\.\d+)?', outputs_text)` 去重（含 step_results 截断前的原文，注意 :334-335 的 [:3000] 截断会把部分数字截掉——检测用未截断原文）；
- 回答数字集同法提取，剔除日期模式（`\d{4}-\d{2}-\d{2}`）与引用编号 `[n]`；
- 回答中存在、证据中不存在的数字 ≥2 个 → `degradation_alerts_total{code="reporter_fabrication_suspect"}` +1 + trace add_event（level=warn，attributes 带可疑数字列表 [:10]）。不阻断输出。

**测试**：step_results 无"327"而 LLM 返回含 327 两处 → counter +1；正常回答 → 不触发。

---

## 3. P2 逐项详情

### R7 跨轮中间态摘要注入

**问题**：checkpointer 默认关（config/__init__.py:77）且 thread_id 每轮唯一（runner.py:169-173，`agent-{session_id}-{ms}`），即使开启也不跨轮合并；跨轮只有 L2 的 question+answer 对（runner.py:312-316），plan/step_results/SQL 结果全丢。

**方案（轻量摘要，不开 checkpointer）**：
- `backend/memory/service.py:113-139` `end_turn`：落 turn 后追加保存 `last_turn_digest`——`{"plan": [(step_id, capability, status)], "results": {step_id: str(output)[:200]}}`；SQL 步骤保留 `sql` 与 `row_count`（从 SQLResult dict 取）。
- 存储：`chat_sessions` 表加列 `last_turn_digest JSONB`（新 migration，走现有 alembic，编号顺延）。
- `start_session`（service.py:76-80 附近）：digest 存在则注入 SystemMessage：`"上一轮任务与结果摘要（供追问引用，勿编造未提及的数据）：..."`，与现有 L2/L3 注入同级，同样受 `trim_messages_to_budget`（:98-104，2048 token）约束——digest 注入后 budget 优先级设为高于 L3。
- **checkpointer 实验开关**（独立决策）：`MAIN_GRAPH_CHECKPOINTER_SESSION_SCOPED`——开启时 runner.py:169-173 的 thread_id 改为 `agent-{session_id}`（去掉时间戳），用现有 PostgresSaver（checkpointer.py:23-36 已含 setup() 与 TTL 清理 :40-43）。灰度开关默认关，因同 session 并发轮次会串状态。

**测试**：第一轮 sql.query 出 row_count=42 → 第二轮问"刚才查询结果多少行"→ 断言 planner 或 RAG 输入 prompt 含 42。

### R8 专家正文摘要传递

**问题**：expert_history 只存 `{expert, status, duration_ms}`（各专家自 append，如 knowledge.py:100-105）；cs_supervisor LLM 兜底只看专家名单（supervisor.py:234）。

**改动点**：
- 统一收敛点：`backend/customer_service/experts/base.py` `run_expert_safely`（:50-104）里 append 时加 `"response_digest": (result.response_draft or "")[:200]`——各专家不用改（现状是各专家自己 append，收敛到 base 更干净，属小重构，注意 5 处 append 全删防重复）。
- `supervisor.py:235-243` prompt 加行：`f"已执行 Expert 及结论摘要: {[(e['expert'], e.get('response_digest','')[:100]) for e in expert_history]}"`。
- 隐私：digest 仅 200 字、仅进 LLM prompt 与 state，不新增落库面；`cs_audit_entries` 维持现状。

**测试**：knowledge 先执行返回"订单 A123 已于 9 月 10 日发货" → complaint 的 supervisor 兜底 prompt 含该摘要。

### R9 decay 调度入口

**问题**：`MemoryDecayService.run()`（decay.py:9-21，>180 天 ×0.9、>90 天 ×0.95、<0.2 归档）全项目无调用方，L3 记忆永不衰减。

**改动点**：复用客服 checkpointer 清理 daemon 模式（checkpointer.py:40-43 `start_cleanup_daemon`）：backend/app 启动 lifespan 中起 daemon 线程，每 24h 调 `MemoryService.run_decay()`。开关 `MEMORY_DECAY_ENABLED`（默认 true）+ `MEMORY_DECAY_INTERVAL_HOURS=24`。

**测试**：手动调 run() 断言返回 {"decayed": n, "archived": n} 结构 + 首次启动日志。

### R10 上下文注入量观测（看板基础）

**改动点**：`runner.py:105-109` start_session 后：`trace add_event("context_injected", attributes={"l1_messages": len(l1.messages), "summary_injected": bool, "l3_facts": n, "tokens": est})`（est 用现有 trim 的 token 估算函数）。SSE meta 帧可顺带带 ctx 计数（前端调试用，可选）。

### 其他确认项的处置

- **空 final_answer 不入历史**（runner.py:312-316）：guard BLOCK 不入历史是合理设计；error 路径建议仅记 `chat_request_total{status="error"}`（已有），暂不改写入行为，观察一周 trace 再定。
- **Send 传 messages 但 skill 不用**：已证实 skills 全目录无 chat_history 使用（grep 零命中），business_analysis 依赖 previous_outputs（skill.py:64,90-100）。**保留透传不删**（R4/R7 的 history-aware 能力会用到），在 docs/AGENT_DESIGN.md 标注"messages 为下游预留"。
- **workflow 截断**：direct_executor.py:271 `[:500]`/:293 `[:200]` 是 final_answer 拼接与留痕快照，非上下文注入路径，暂不动；若 R7 落地后 digest 源改用 :293 的 ctx.outputs，需同步放宽为 [:300]。

---

## 4. 回归测试用例清单（第 7 项）

新建 `backend/tests/context_retention/`：

| 文件 | 用例 | 断言 |
|---|---|---|
| test_cs_history.py | 多轮指代 | R4 开启时 expert prompt 含上轮实体；关闭时回归现行为 |
| test_summary_visibility.py | 摘要超时/DB 断连 | counter+1、日志 error、旧 summary 不被 [:500] 覆盖 |
| test_long_session.py | >10 轮（21+ 条消息）+ 摘要失败 | 日志出现 memory_end_turn_failed，无静默 None |
| test_cross_turn_digest.py | 跨轮追问上轮 SQL | R7：下轮 start_session 注入的 SystemMessage 含 row_count |
| test_expert_chain.py | knowledge→complaint 链式 | R8：supervisor prompt 含 response_digest |
| test_reporter_guard.py | step_results 无 X 而 LLM 输出 X | R1：system prompt 含禁止编造；R6：suspect counter |
| test_gate_degraded.py | Gate1/2/ClaimVerifier 分别抛异常 | R3：span=skipped、skipped_stages 聚合、counter、答案仍产出 |
| test_plan_repaired.py | 带解释文字的 LLM 输出 | R5：meta.repaired=True、alerts 落盘 |

## 5. trace 审计看板字段（第 8 项）

数据源：SQLite `data/trace_store.db`（5000 行滚动）+ `/metrics`。看板按 trace 维度展示：

| 字段 | 来源 | 用途 |
|---|---|---|
| ctx_messages / ctx_tokens | R10 事件 context_injected | 每节点上下文量基线 |
| summary_injected / summary_failed | service.py + R2 counter | 摘要链路健康 |
| root.metrics.skipped_stages | tracer 现有折叠 | RAG 防线被跳过的轮次占比 |
| rag_gate_degraded_total{layer} | R3 counter | 防线异常放行趋势（分 layer 告警） |
| metadata.rejection{layer,reason} | 现有 _reject :650 | 拒答分布（已有） |
| alerts[].type=plan_json_repaired | R5 | 垃圾计划占比 |
| reporter_fabrication_suspect | R6 counter | 编造嫌疑率 |
| trace_finish_total{status} / leaked_spans | 现有 | 管线整体健康（已有） |

查询入口：`TraceStore.list_since(cutoff, only_rejected, limit)`（trace_store.py:181-218）已支持按 rejected 过滤，需扩一个 `only_degraded` 列（R3 落地后加摘要列 `degraded`）。

## 6. 三个待确认点的验证方法（第 9 项）

1. **CS 无历史是否刻意**：
   ```bash
   git log --follow --oneline -- backend/orchestration/graph/cs_graph_node.py
   git log -p -- backend/customer_service/graph_state.py | grep -n "chat_history\|messages"
   grep -rn "多轮\|指代\|对话历史" docs/AGENT_DESIGN.md docs/ARCHITECTURE.md
   ```
   看 cs_graph_node 引入 commit 的 message 是否说明"状态机承载多轮"；再对照 handoff/confirmation 状态机覆盖的场景列表判断"对话语义记忆"是否在范围外。
2. **config={"timeout"} 是否生效**：写 5 行脚本分别用两个 provider 实测：
   ```bash
   cd backend && python -c "
   from backend.agents.llm import get_llm   # 按实际 import 路径
   llm = get_llm()
   import time; t=time.time()
   try: llm.invoke(['hi'], config={'timeout': 0.001}); print('NO TIMEOUT')
   except Exception as e: print(type(e).__name__, time.time()-t)"
   ```
   若 0.001s 未抛超时则该写法无效，需改为 provider 级 `LLM_REQUEST_TIMEOUT` 或 per-call `max_retries`/绑定 client。重点核对 `CS_SUPERVISOR_LLM_TIMEOUT_MS=800`（supervisor.py:249-256）是否真实生效。
3. **prompt_service DB 是否覆盖线上模板**：
   ```bash
   cd backend && python -c "
   from backend.prompts.service import prompt_service
   p = prompt_service.get_active('rag.qa')      # get_active :107-140 返回带 source/version
   print(p.source, p.version)"                  # source='db' 即被覆盖
   ```
   或直接查元库：`SELECT key, active_version FROM prompt_definitions;`（agent_memory 库，service.py 的 repo 对应表）。覆盖了则 R1 改 yaml 后必须走发布流程。

## 7. 最小落地顺序

1. **R1**（reporter prompt 修复）——改动最小、堵最大口子；先跑第 9.3 项确认 DB 覆盖状态再改。
2. **R2**（摘要失败可见化）——纯加日志/指标，零行为风险。
3. **R3**（RAG fail-visible）——零行为风险，一次补齐 8 处 + 指标；含 Faithfulness NameError 小修。
4. **R4**（CS 历史）——唯一有行为变化的 P0，带开关灰度；落地前先跑第 9.1 项确认设计意图。
5. **R5、R6**——可观测增强，随意穿插。
6. **R7/R8/R9/R10**——R7 需 migration，放最后；R9 独立随时可做。

## 8. 待确认清单（2026-09-14 已验证，结论如下）

- [x] **CS 无历史非刻意设计**：`git log -S chat_history` 在 cs_graph_node/graph_state 历史零命中；引入提交（f503a1b）说明只谈流程状态机与 checkpointer，未提对话文本；且 `CS_CHECKPOINTER_ENABLED` 默认 false、用 InMemorySaver（重启即失）且 CSGraphState 无历史字段。→ **R4 按原方案做**。
- [x] **DB 未覆盖模板**：agent_memory 库只有 chat_messages/chat_sessions/memory_records 三张表，prompts/prompt_versions 表不存在，DB 覆盖链路整体未启用。→ **R1 直接改 `backend/prompts/defaults/*.yaml` + 重启即生效**，无需发布流程。
- [x] **`config={"timeout"}` 实测无效**：ChatOpenAI（qwen3.7-plus）下 `config={'timeout':0.001}` 与 `config={'request_timeout':0.001}` 均不抛超时、调用正常完成。→ **CS supervisor 的 800ms 与 query 专家的 3s 超时形同虚设**，实际受控于构造期的 `LLM_REQUEST_TIMEOUT=30s`。修法（新增 R0 小项）：删掉两处无效 config，改用 `asyncio.wait_for(asyncio.to_thread(...), timeout=...)` 包裹（同 skills/base.py:307-310 的既有模式），或按调用方构造带 timeout 的模型实例。
- [x] **ENABLE_FAITHFULNESS 维持 false**（现状不开启），但 R3 必须先修 chain.py:1192 的 faith_span 未绑定 NameError 隐患；修复后在测试环境以 `FAITHFULNESS_REJECT_SCORE=0.5` 灰度开启，观察 `rag_reject_rate` 一周后再决定生产默认值。
