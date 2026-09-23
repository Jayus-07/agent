# Context Budget 生产收口 · STOP A 审计报告（Production Wiring Audit）

> 2026-09-23。逐文件审计 + 真实库/容器动态探针，非报告推断。
> 上一轮基线：`docs/2026-09-23-ContextBudget加固-STOP-A-审计报告.md`、`docs/2026-09-23-ContextBudget加固-完成报告.md`（STOP A `780891d` / B `050cce5` / C `310b4bf` / D `89fc8f2`，verdict HARDENING_PASS）。
> 本轮只做：migration 落地、生产 hook 接线、真实验收、小范围修复。不新增 L6、不重构 L1-L5。

---

## 0. 模型链路差异记录（本轮新事实：主链 = DB 配置的豆包）

上一轮报告写「当前 .env 主链 DeepSeek」，**已过时**。以当前 DB 为准（查询时间 2026-09-23）：

| 证据 | 值 |
|---|---|
| `llm_model_role_bindings` role=**main** | **doubao-seed-2.0-mini**（2026-09-22 16:34 绑定） |
| role=context_compactor（L5 摘要模型） | qwen3.8-flash |
| role=fallback | qwen3.8-flash |
| `llm_models` 豆包条目 | enabled=t，provider_id=`custom-doubao-seed-2-0-mini`（driver=openai 自建供应商），upstream_model_name=`doubao-seed-2.0-mini` |
| 近 3 天 `llm_usage` 真实流量 | doubao-seed-2.0-mini 619 次调用 / 675k tokens（另有上游真名记录，见下） |
| `.env` | `CONTEXT_L5_SUMMARY_MODEL=qwen3.8-flash`（与 DB 角色绑定一致）、`LLM_CONTEXT_LENGTH=8192` |

对 Context Budget 的影响（逐项核实）：

1. **TokenCounterRegistry 认识豆包吗**——`_CALIBRATION` 无 `custom-doubao-seed-2-0-mini` → 走 `_CALIBRATION_DEFAULT=(0.75, 0.33)` ×1.10 margin（token_counter.py:46）。比 deepseek 档（0.70）**更保守**，不会低估，无需改动；STOP D 用真实 usage 校验误差。
2. **`llm_models.context_length` 全表 NULL**（16/16 行）→ `resolve_model_context_window` 恒返回配置窗口 8192。「min(配置, 注册窗口)」中的注册项目前从未生效。对豆包 seed 2.0（真实窗口 ≥128k）8192 是保守安全值，**不构成缺陷**；是否给生产模型补填 `context_length` 留 STOP E 基线决策（填了也不改变 8192 预算，因配置窗口更小）。
3. **相邻发现（P2，本轮不修）**：`llm_usage` 中存在上游真名 `doubao-seed-2-0-mini-260215/260428` 的记录且 provider 被误标 `ollama`——根因：上游回显名与 DB `upstream_model_name` 不一致时 `canonical_model_id` 反查落空，`resolve_provider` 启发式不认识 `doubao` → 回落 default ollama 并告警。影响是**用量观测按 raw 名拆分 + provider 标签失真**，不影响预算/压缩正确性。登记为待办（修法：DB upstream_model_name 对齐上游回显名，或 canonical 增加 has前缀匹配——属模型治理域，不混入本轮提交）。

---

## A1. migration 044 审计（`sql/migrations/044_chat_sessions_summary_version.sql`）

| # | 问题 | 结论 |
|---|---|---|
| 1 | 幂等？ | **是**。`ADD COLUMN IF NOT EXISTS`（:16）；`init_db.apply_migration` 有 checksum 追踪（schema_migrations）+「已存在」降级逐语句补齐 |
| 2 | 已有数据处理？ | **是**。`NOT NULL DEFAULT 0` 存量行回填 0（PG 11+ fast default，不重写表） |
| 3 | 默认值 | `0`（= 从未写过摘要） |
| 4 | NOT NULL？ | 是 |
| 5 | CAS SQL 依赖字段 | 读侧 `get_summary_state` SELECT `summary_version`（auto_compact.py:221）；写侧 `UPDATE ... SET summary_version = summary_version + 1 WHERE session_id=%s AND COALESCE(summary_through_message_id,0)=%s`（:295-304）。依赖 040 的 `summary_through_message_id` + 044 的 `summary_version` |
| 6 | 本地目标库已执行？ | **否**（三重证据）：① `information_schema.columns` 无 `summary_version`；② `schema_migrations` 只有 040/041/042，无 044；③ **运行容器内动态探针** `SyncMemorySummaryStore('audit-probe').get_summary_state()` → `UndefinedColumn: column "summary_version" does not exist` |
| 7 | Docker 初始化路径必执行？ | 新库：**必执行**。db-migrate one-shot（`python scripts/init_db.py`）是 app 的 `depends_on: service_completed_successfully` 前置；`MIGRATION_TARGETS["044_chat_sessions_summary_version.sql"]="memory"` 已登记（scripts/init_db.py:131），未登记文件会 fail-fast 拒绝启动 |
| 8 | 存量库自动执行？ | `docker compose up` 重建 db-migrate 容器时会重跑（幂等，已应用的 skip）；**仅 restart 不会**。当前库落后 = 上次 db-migrate 运行早于 044 落地，之后没人再跑过 |
| 9 | 「新库有旧库没有」风险 | **当前真实存在，且已发生**：运行中的 app/worker 容器代码已含 CAS 逻辑（容器内 grep `summary_version` = 3 处），库缺列 → **生产 L5 每次尝试在读侧即抛 UndefinedColumn** → `_run_incremental_summary_locked` 兜底 except 捕获（auto_compact.py:607）→ 记 `provider_error` 后安全回退。聊天不炸，但 **L5 增量摘要整体静默失效**。修复属部署欠账而非架构缺陷 → STOP B B1 第一项 |
| 10 | rollback 必要？ | 否。纯增量列，回滚 = `DROP COLUMN summary_version`（可选，无数据破坏） |

---

## A2. RAGBudgeter 接线审计

机制已实现且完备（`rag_budgeter.py`）：score 贪心装入 + 同 source 上限 3 条让位（diversity）+ 全装不下保最高分 1 条 + 无分/口径不齐回退原序裁剪。**问题只在没有生产调用方传值**：

1. `rag_scores` 永远为空？——**是**。生产唯一入口 `proxy._preflight_context`（proxy.py:1416）调 `prepare_llm_context(messages=..., extra_reserved_tokens=...)`，不传任何 rag_*；全仓业务代码 0 处传 `rag_context=`（仅 `context_budget/__init__.py` docstring 示例与评测 runner）。
2. `rag_sources` 永远为空？——同上。
3. 最适接入点：**`rag/chain.py:444-456` 证据 token 预算裁剪处**。理由：此处（rerank → EvidenceGate 前置 → 版本过滤之后）的 `docs` 就是「最终参与 prompt 的 chunk」唯一确定点，随后 stuff 进 prompt（`create_stuff_documents_chain`）；当前用的是机械尾删 `trim_texts_to_budget`（EVIDENCE_TOKEN_BUDGET=3000，config/rag.py:381 默认生效）。把这里换成 RAGBudgeter 即达成「真实 metadata 驱动的价值优先裁剪」，不重跑 rerank、不伪造。proxy 层的 rag_context 形参**保持不接**：RAG 生成路径的证据已 stuff 进 messages，再传 rag_context 会双算（manager 把 rag_tokens 从历史预算中扣两次口径）。
4. score 与 chunk 顺序错位风险：**无**。`reranker.py:334-335` 把 `rerank_score` 写在每个 doc 的 metadata（同对象传递），接入时逐 doc 同序构造 `(texts, scores, sources)`，无按原始召回 index 对齐的环节；版本过滤与预算裁剪均保序。
5. parent/child 合并后 score 对应：检索链 = 混合检索 → **同文档扩展 → Rerank**（扩展在 rerank 之前）→ 最终 docs 全部经 compressor 打分；无分仅两种情形——rerank 服务不可用（metadata 标 `rerank_unreliable`，reranker.py:457）或 score 低于阈值被滤。两种情形都自然落入「无 score → 原序回退」。
6. 同 source 垄断：RAGBudgeter `_MAX_CHUNKS_PER_SOURCE=3` + 让位逻辑已实现；接入时 sources 显式传 `metadata.source_file`（比 `_guess_source` 正则启发可靠），启发式仅作 sources 未传时的兜底。
7. 无 score fallback：`budget_rag_texts` scores 为空或长度不齐 → `trim_texts_to_budget` 原序裁剪 = **与现状逐字节一致**，不报错。
8. backward compatible：是。chain 内部替换，不改对外签名。**一个实现注意点**：现有代码 `docs = docs[:len(kept_texts)]` 假设 kept 是输入**前缀**（机械尾删性质）；RAGBudgeter 的 kept 是价值序选出的**非前缀**子集 → 需按 index 映射回 docs（新增 `budget_rag_indices` 辅助，`budget_rag_texts` 委托之，行为兼容）；「首条文档即使超预算也保留」的既有语义由「全装不下保最高分 1 条」承接（rerank 序首条 = 最高分，语义等价）。

---

## A3. predicted_extra_tokens 审计

全仓搜索 `predicted_extra_tokens / prepare_llm_context / _preflight_context / bind_tools / response_format`，调用路径盘点：

| 调用路径 | 当前值 | 是否有真实可预测额外成本 | 是否接 |
|---|---|---|---|
| `proxy._preflight_context`（invoke/ainvoke/stream/astream，:1471/:1513/:1594） | 0 | **无**——kwargs 里的 tools/response_format 已在 preflight 内现算计入 reserved（:1402-1404）；bind_tools schema 在 `_BoundLLMProxy` 构造期折算缓存计入；其余对 token 有实质影响的注入（L2 摘要块、L3 previous_outputs、RAG 证据、CS 上下文）在调用时**已经在 messages 里**，preflight 全量计数 | 否 |
| RAG 生成调用（stuff 后 prompt，chain.py `_llm_stream`） | 0 | 证据已 stuff 进 prompt（messages 内）→ 已被计数；若再传 rag_context/predicted 会**双算** | 否 |
| L5 摘要 LLM 调用（str 形态 prompt，auto_compact.py:455） | 0 | 非 messages 形态，preflight 不介入；prompt = 模板 + 旧摘要 + delta 消息 + 受保护事实，长度由 `CONTEXT_L5_MAX_DELTA_MESSAGES=200` 与超时 30s 兜底 | 否 |
| bind_tools 路径（tool_selector.py:335） | 构造期折算 | 已计入（存量） | 已接 |
| 评测/注入式测试 | 显式传值 | 测试专用 | 不涉生产 |

**结论：`HOOK_SUPPORTED_BUT_NO_RELIABLE_PRODUCTION_SOURCE`** ——当前没有任何调用方「在进入 LLM 前已知、且尚未被现有 counter 计入」的额外输入成本。保持 0，不虚构预测值。重点防住的双算（tools schema / response_format / RAG 证据）在现实现中均不存在。若未来出现真正的调用方已知额外 payload（如新的 response_format wrapper），再按「能解释来源 + 未被计入」标准接入。

---

## A4. PinnedContext 审计

**机制现状（关键缺口）**：`pin.py::PinnedContext`（mark_index / mark_message_id / resolve）已实现，但 **`manager.prepare_llm_context` 签名根本不接受 pins**（manager.py:109-119）——pin.py docstring 里的 `prepare_llm_context(messages=..., pins=pins)` 是**预期 API，当前不成立**。`_trim_semantic` 只消费 `collect_pin_indices(msgs)` 的自动 pin（System / 最后 Human / 活跃 tool 对）。即：**显式业务 pin 从机制到调用方双双未接线**。

连带发现：L5 的 `extra_facts` 钩子同样未接线——`_run_l5` 调 `run_incremental_summary(session_id, store)` **不传 extra_facts**（manager.py:396-397），ProtectedFactRegistry 的业务来源（source=business/tool/domain）生产为空，只有正则链在工作。

逐域结构化状态盘点（只从已有 state 取，不建第二套状态）：

| Domain | 已有结构化状态 | 是否必须 pin | 数据来源 | 建议字段 |
|---|---|---|---|---|
| **Customer Service** | `pending_action`（confirmation_store，跨 turn 持久）、`confirmation_state`、`complaint_ticket_id`、ContextResolver `last_order_id`（权威业务实体，DB 持久） | **是（唯一 P0 级价值域）** | `cs_state_loader_node` 快照（graph_builder.py:52-68）+ ContextResolver | PIN_CONFIRMATION（当前确认轮相关消息）、PIN_ENTITY（含当前 order_id 的历史消息）；L5 侧同名事实进 extra_facts |
| SQL | sql.query 结果走 previous_outputs（L3 依赖感知分型压缩已覆盖）；执行时授权主体请求级存在 | 否 | step_results | —（L3 已保 dependency） |
| Travel | `brief`（含用户显式约束 must_visit）、`brief_fingerprint` | 否 | travel state | brief 是每轮 slot_filler 重建的结构化契约并整段进入 prompt，不依赖历史保真 |
| Selection | `funnel_context`、`brief`、`candidates` | 否 | selection_funnel state | 单轮图，产物经 final_answer 一次性交付 |
| Business Analysis | business.analyze 结果 | 否 | step_results | —（L3 已覆盖） |
| 通用 Agent Graph | plan/critique/supervisor DAG | 否 | plan.edges | —（L3 dependency-aware 已接线） |

**接线设计（STOP B B4 口径）**：

- 消息层事实：active context 的消息**不带稳定 id**（memory/session.py:27-28 `HumanMessage(content=...)`）→ mark_message_id 对生产历史无效。采用**内容锚定 pin**：`PinnedContext` 增加 `mark_content_match(value, kind)`，`resolve(messages)` 时按确定性字符串匹配现解析下标——对索引移位免疫，零猜测（域声明「pin 含该实体值的消息」，pin 模块不做内容理解）。
- 请求级通道：pin 模块增加 ContextVar 注册口（同 session_id 模式），`cs_state_loader_node` 从快照读 `pending_action`（含 order_id/refund_id 等）+ ContextResolver `last_order_id` 置位；proxy `_preflight_context` 读取并传入 `prepare_llm_context(pins=...)`；`_trim_semantic` 并集消费。
- **生命周期**：ContextVar 每请求置位/重置 → 「order A → order B」由下一轮 state（ContextResolver 已更新 last_order_id）自然替换，上一实体不再进入 pin 集合；无跨轮累积、无永久 pin。L2 裁剪永不丢 pinned；L4 折叠可回滚（fold_id 溯源）+ L5 侧由 extra_facts 进 ProtectedFactRegistry（critical 业务来源摘要必保留）兜底——两层合计满足「压缩历史仍保留当前订单/确认对象」。
- **不动**：不改 builder.py、不新增域状态、不把 pin 判定交给 LLM。

---

## A5. 运行环境审计

| 项 | 实测值 |
|---|---|
| FastAPI | `agent-app-1`：`python -m uvicorn backend.app.server:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 600`，**单进程（无 --workers）**，PID 1，Up (healthy) |
| 跨 worker race 可行性 | **可行**：同镜像再起一个临时 worker 容器（增量操作，不动现有 app），接入 agent-net，连同一 Redis/PG；C1 记录两侧容器/PID 与请求落点证明跨进程，绝不用同进程 asyncio task 冒充 |
| Redis | 缓存实例 `agent-redis-1`（容器内 `redis://redis:6379/0`，compose 显式注入覆盖 .env 宿主值）；Celery broker 独立（agent-redis-broker）。L5 锁走缓存实例（auto_compact.py:341-344）。注意其为 allkeys-lru：锁键被驱逐 = 提前释放（TTL 60s 主导，风险可接受，记录入基线） |
| PostgreSQL | `agent-postgres-1`（容器内 postgres:5432；宿主映射 **127.0.0.1:5433**；本机 5432 是原生 PG 同名库，勿混）。双库 agent_memory / agent_business |
| 迁移正式入口 | `db-migrate` one-shot 服务（复用 app 镜像跑 `scripts/init_db.py`，schema_migrations 追踪，幂等可重跑） |
| 观测 | Prometheus / Grafana / alertmanager 在跑 → C7 可直接查 `context_*` 指标真实生产数据 |
| 模型 | 主链豆包（DB 绑定）、L5 摘要 qwen3.8-flash，见 §0 |
| 容器代码版本 | app/worker 容器内代码与 HEAD 一致（含 CAS/preflight 接线），不存在「代码旧于工作区」的验收失真 |

---

## 结论

**本轮无「架构级 P0」**：CAS/Redis 单飞/preflight 四包装/L1-L5 机制经逐文件审计与上轮 173 测试成立。唯一 P0 是**部署欠账**：

- **P0-1：migration 044 未在目标库执行，而运行容器已含 CAS 代码 → 生产 L5 增量摘要整体静默失效**（动态探针实证 UndefinedColumn；fail-safe 吞掉不炸聊天，正是最危险处——没人会从用户侧发现）。修复 = STOP B B1 用正式入口执行，修后复测探针 PROBE_OK。

```
STOP_A_PASS=true
STOP_B_ALLOWED=true
```

## STOP B 实施口径（按本审计结论执行）

1. **B1 migration**：`docker compose run --rm db-migrate`（正式入口，幂等）。执行前查 information_schema（已查，无列）；执行后验证列/默认值/回填 + **二次执行幂等** + 容器探针复查 PROBE_OK + app **无需重启**（代码已就位，列存在即生效）。
2. **B2 RAG hook**：`rag_budgeter.py` 增加 `budget_rag_indices`（`budget_rag_texts` 委托，行为兼容）；`chain.py:444-456` 证据裁剪接线（scores=逐 doc `rerank_score`、sources=`source_file`；无分回退原序=现状）；kept 非 prefix 用 indices 映射 docs。验收用例：A(0.95/0.93/0.91) B(0.89) C(0.85) 预算不足时 diversity 生效、无 score fallback、kept 保序。
3. **B3 predicted_extra_tokens**：**不做生产接线**，报告记录 `HOOK_SUPPORTED_BUT_NO_RELIABLE_PRODUCTION_SOURCE`（双算风险已证：tools/response_format/RAG 证据均已计入）。补一个「不传值时行为不变」的回归断言即可。
4. **B4 PinnedContext**：manager `prepare_llm_context` 增加 `pins: PinnedContext | None = None` 形参 → `_trim_semantic` 并集消费；`pin.py` 增内容锚定 `mark_content_match` + 请求级 ContextVar 注册口；`cs_state_loader_node` 置位（pending_action/last_order_id）；`_run_l5` 的 `extra_facts` 从同一请求级业务事实注入（ProtectedFactRegistry critical 来源）。生命周期：每请求重建 = 天然 supersede/expiry。测试：supersede、expiry（无 state 无 pin）、无 pin 行为不变、CS 置位端到端。
5. **B5 测试**：新增 `test_rag_hook_wiring.py` / `test_pinned_context_wiring.py` / `test_migration_044.py`（幂等 + CAS 读写依赖列存在）/ predicted_extra_tokens 断言并入 wiring 测试。跑 `tests/context_budget tests/memory` + 受影响域 `tests/rag tests/customer_service` + 契约门四件套（--no-cov）。
6. 提交：`fix(context-budget): wire production context signals`（双重路径限定，不混其他会话文件：工作区现有 task_executor/task_authorization 等改动属任务运行时会话，禁碰）。
