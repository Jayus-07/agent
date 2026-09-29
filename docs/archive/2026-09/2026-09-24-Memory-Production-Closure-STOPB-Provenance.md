# Memory Production Closure — STOP B 实施报告：事实来源与 Provenance 收口

> 日期：2026-09-24 ｜ 前置：STOP A（`79d4668`，STOP_A_PASS=true）
> 报告遵循任务书 §57 返回格式。所有结论基于真实代码与真实数据库。

---

## 1. Verdict

**STOP_B_PASS=true**

附注（非阻塞）：§47-49 实机验收中「真实 LLM 提取→写入」环节被外部依赖阻塞——当前部署的豆包模型 embedding 返回 `AllocationQuota.FreeTierOnly`（免费额度耗尽，属既存环境问题，见 §13/§16）。防线正确性由「真实 PostgreSQL + mock LLM/embedding」测试（项目测试铁律：只 mock 外部边界）+ 代码级 evidence 校验保证；配额恢复后建议在 STOP G 实机验收中复验真实提取写入。

## 2. Root Cause / Goal

STOP A 确认的两个 P0：①自动提取把 assistant 回答与用户话语当平权事实源（G1）；②explicit/inferred 无 provenance、confidence_score 是死字段（G7/G8）。本轮只解决这两件事，不跨 STOP 收编。

## 3. Changed Files

| 文件 | 变更 |
|---|---|
| `backend/sql/migrations/047_memory_provenance.sql` | 新增：`origin VARCHAR(16) NOT NULL DEFAULT 'legacy'` + `source_message_id INTEGER NULL` |
| `backend/memory/models/memory.py` | ORM 加 `origin`/`source_message_id` 列 |
| `backend/memory/long_term.py` | `MemoryFact` 扩展 provenance 字段；`clamp_confidence`；`_evidence_from_user` 代码级硬防线；hedged 措辞 confidence 封顶；`_parse_facts` 新 4 段协议（fail-closed，删除旧 Format 2/3 兜底）；`store_single` 落库 provenance + 入口防线纵深 |
| `backend/memory/service.py` | `end_turn` 从 `save_turn` 返回值确定 `user_message_id`（try 外预初始化，防失败路径 UnboundLocalError）；`store()` 强制 `origin=inferred`、透传 `source_message_id`、全链 rejected metrics |
| `backend/tools/memory.py` | `memory_store_tool` → `origin=explicit` + explicit 默认置信 + `memory_explicit_total` |
| `backend/config/memory.py` + `config/__init__.py` | `MEMORY_ORIGIN_*` 三枚举、`MEMORY_EXPLICIT_DEFAULT_CONFIDENCE=0.98`、`MEMORY_INFERRED_DEFAULT_CONFIDENCE=0.7`、`MEMORY_HEDGED_CONFIDENCE_CAP=0.55` |
| `backend/observability/metrics.py` | `memory_extraction_candidate_total`、`memory_extraction_rejected_total{reason}`、`memory_explicit_total`、`memory_inferred_total` |
| `backend/prompts/defaults/memory_long_term_fact_extraction.yaml` | 重写：`<user_evidence>`/`<assistant_context>` 分块 + 6 条铁律 + 4 段输出协议（`类型|内容|置信度|用户原话片段`） |
| `backend/tests/memory/test_memory_provenance.py` | 新增 15 用例（Case B1-B11 全覆盖） |

## 4. Before

提取输入为 `用户: {q}\n助手: {a}` 平权文本；prompt 无来源约束（示例还把助手复述当可提取源）；`_parse_facts` 接受两段格式无任何证据校验；`MemoryFact`/`memory_records` 无 origin/source_message_id；`confidence_score` 恒为默认 1.0 无人读写；`memory_store_tool` 与后台提取产物完全同构；`save_turn` 返回值未被利用，无消息级追溯。

## 5. After

- **事实来源契约**（§3-§5/§23 落地）：用户消息=唯一证据源，assistant=仅语境。防线三层：①prompt 结构化分块（权限差异显式表达）；②**代码级 evidence 校验**——候选必须携带「用户原话连续片段」且归一化后能在用户消息中找到子串，否则拒绝（`REJECT_ASSISTANT_ONLY`）；③fail-closed——旧两段格式/无证据段一律拒绝（宁可少写，不可脏写）。
- **Provenance**：`origin` 由代码强制赋值（自动管线=inferred，工具=explicit，dataclass 默认=legacy），不信任模型输出；`confidence_score` 服务端 clamp[0,1]（非法/缺失回退 0.7）；hedged 用户原话（"可能/考虑/看看"…）强制封顶 0.55（防 assistant 复述升级为高置信"决定"，Case B4）；`store_single` 入口二次 clamp + origin 非法值回退 legacy（防线纵深）。
- **并发安全**（§13-14 落地）：`save_turn` 已返回 `(user_msg, assistant_msg)`，`end_turn` 在 commit 前确定 `user_message_id` 并作为**不可变参数**传入后台 `store`——后台禁止回查 latest（无此逻辑），并发 turn 不串轮（Case B6 实证）。

## 6. Write Pipeline（改造后）

```
runner.py:773 → MemoryManager.end_turn → MemoryService.end_turn
  ├─ save_turn → (user_msg, assistant_msg) → user_message_id 确定
  └─ ensure_future(store(question, answer, session_id, user_id, source_message_id=user_message_id))
       extract_facts（<user_evidence>/<assistant_context> 分块 prompt，LLM 线程池）
       → _parse_facts：4 段协议解析 + clamp + evidence 子串校验 + hedged 封顶
       → 【拒】rejections → memory_extraction_rejected_total{reason}
       → 强制 origin=inferred、source_message_id 绑定
       → worthiness gate（IGNORE → not_worthy）
       → importance gate（<0.6 → low_importance）
       → store_single（二次 clamp → dedup/supersede → duplicate 计数）
       → memory_records（origin / confidence_score / source_message_id / session_id）
memory_store_tool → PII → MemoryFact(origin=explicit, confidence=0.98) → store_single
```

## 7. Fact Source Contract

`user_message = evidence（权威）`；`assistant_answer = context only（非权威）`。context-assisted 场景（§24，"以后就按这个格式来"）保留：evidence 片段来自用户原话即通过校验，语义补全可来自 assistant 语境——事实的**决定来源**仍是用户。

## 8. Provenance Contract

| 字段 | 语义 | 赋值方 |
|---|---|---|
| `origin` | 写入通道：explicit/inferred/legacy（通道语义，非语义确定度——Case B2） | 代码强制，非模型 |
| `confidence_score` | [0,1]，explicit 默认 0.98、inferred=LLM 返回 clamp（回退 0.7）、hedged 封顶 0.55 | 代码 clamp |
| `session_id` | source session provenance（复用既有列，未新增 source_session_id） | 现有 |
| `source_message_id` | 产出记忆的 role=user 消息 id；legacy 与 tool 通道为 NULL（不造假） | save_turn 确定后透传 |

## 9. Database Changes

Migration `047_memory_provenance.sql`（幂等，已应用于实库 agent-postgres-1/agent_memory，docker exec psql 直执行）。legacy 策略：`NOT NULL DEFAULT 'legacy'`——存量 291 条统一标记 legacy（不伪装 inferred/explicit），查询/观测无 NULL 分支。**不建 DB FK**：chat_messages 随会话 ON DELETE CASCADE（002），而长期记忆必须跨会话存活；本列当前无 JOIN 查询路径，逻辑 provenance 足够。**不建索引**：无点查路径，避免过度设计（STOP C 的 partial index 一并考虑）。

## 10. Explicit / Inferred Behavior

- `memory_store_tool`（用户/Agent 显式"记住"通道）→ `origin=explicit`、confidence=0.98、`source_message_id=NULL`（ContextVar 请求上下文无 message id，如实置空，session 归属仍可追溯——任务书 §17 允许并已在工具注释注明）。
- 后台自动提取 → `origin=inferred`、confidence=提取器返回值 clamp。
- 当前主聊天无显式意图检测器，按任务书 §16 **不新增** LLM 意图分类链（零新增 LLM 调用，B14 ✓）。
- explicit 与 inferred 冲突时的优先级裁决 = **STOP C ownership**（本轮两条通道产物共存、互不覆盖）。

## 11. Concurrency Safety

`source_message_id` 在 `save_turn` 完成（flush 生成 id）时确定，以参数形式进入后台协程闭包——两轮并发 `store()` 交错执行时各自携带自己的 id（Case B6：id=3740/3742 类比，测试中以双 save_turn + `asyncio.gather` 实证不串轮）。代码中不存在"后台查询最新用户消息"的路径。

## 12. Tests

命令均为 `PGPORT=5433`（权威库；5432 为宿主机原生同名旧库——本机双 PG 坑，与 sql-agent 收口同解法）+ `--no-cov`：

| 轮次 | 命令 | 结果 |
|---|---|---|
| 第一轮（新增） | `pytest tests/memory/test_memory_provenance.py -q --no-cov` | **15 passed** |
| 第二轮 | `pytest tests/memory -q --no-cov` | **33 passed**（修掉本轮引入的 1 个回归后全绿，见下） |
| 第三轮（组合） | `tests/memory + tests/api/test_memory_routes.py + tests/rag/test_memory_{isolation,write_ordering}.py` | **53 passed / 3 failed**——3 个失败与 STOP A 基线**完全一致**（`test_memory_db_unavailable_is_handled_as_503` 存量失败：他人未提交的 observability/infra 改动致断言 `UPSTREAM_UNAVAILABLE≠MemoryDatabaseUnavailable`；`test_memory_write_ordering` 两用例仅合跑挂、单跑 6/6 过：既有测试隔离问题）。**无新增失败，基线未扩大（B17 ✓）** |
| 补充 | `tests/orchestration/graph/test_runner_trace_finalize.py + test_stream_events_flow.py` | **26 passed**（runner 记忆接线无回归） |

Case 覆盖：B1 assistant-only 写入率=0 ✓｜B2 inferred+provenance 落库 ✓｜B3 tool→explicit ✓｜B4 hedged 封顶 ✓｜B5 通道区分 ✓｜B6 并发不串 id ✓｜B7 旧调用 legacy 兼容 ✓｜B8 migration 存量 legacy ✓｜B9 clamp 六种形态 ✓｜B10 PII 先于落库 ✓｜B11 store 异常不阻断 ✓。

**本轮引入并已修复的回归**：`end_turn` 初版把 `user_message_id` 赋值放在 try 内，`save_turn` 失败路径触发 `UnboundLocalError`——被存量测试 `test_summary_visibility` 抓到，已修（预初始化提到 try 外）。

## 13. Real DB Validation（§46）

实库只读查询（agent-postgres-1:agent_memory）：
- `information_schema`：`origin`/`source_message_id` 两列存在 ✓；`origin` NOT NULL 无例外行 ✓
- 存量迁移：291 条全部 `origin='legacy'`，content/embedding/importance/is_active/superseded_by 零改动 ✓（Case B8）
- 新记录写入正确性：B2/B3/B6/B10 在**同一权威库**真实写入并断言（origin/source_message_id/confidence/PII 脱敏），测试后清理——当前实库 inferred=0/explicit=0 的原因即 §14 所述配额阻塞（真实 LLM 提取无法产生新记录），非字段不生效（测试已证明字段真实写入）。
- app 镜像已 rebuild + 重启（healthy），prompt v3 已发布 DB（`draft→testing→evaluation→passed→published`，active_version=3，发布流程 audit 留痕），容器内 `render_sync` 实测返回新协议模板（853 字符，含 `<user_evidence>`）✓。

## 14. Metrics / Trace

新增（Prometheus，实机 /metrics 已见注册）：`memory_extraction_candidate_total`、`memory_extraction_rejected_total{reason=assistant_only|not_worthy|low_importance|duplicate|error}`（固定枚举，无高基数 label）、`memory_explicit_total`、`memory_inferred_total`。复用：`memory_retrieval_total/-failure_total`、`degradation_alerts_total`。Trace span：store 链运行于 MemoryManager 后台线程（无请求 trace 上下文），按任务书 §30"不自建 tracing"原则本轮以 metrics+结构化日志替代（日志仅记 origin/type/confidence/source_message_id/计数，无 content/PII），trace 贯穿留 STOP G 统一评估。日志安全：新日志不打印 memory content 原文与用户话语 ✓。

**实机降级证据（B13/B11 真实发生）**：实机验收期间豆包 embedding 返回 `AllocationQuota.FreeTierOnly`（免费额度耗尽，既存环境问题，其他会话同日同坑），uvicorn 日志：`[MemoryService] L3 检索失败，降级继续`；metrics 实测 `degradation_alerts_total{code="MEMORY_L3_RETRIEVAL_FAILED"}=1`、`memory_retrieval_total{status="degraded"}=1`——**主聊天正常返回、无 500**（真实故障注入级证据）。GraphRunner 实机执行链完整：ask → 路由 → RAG 拒答 → `end_turn` 落库（chat_messages id 3740-3743）→ `store` 触发 → 提取异常 → `memory_extraction_rejected_total{reason="error"}=2`（exec 进程内实测）→ 进程不崩溃。

## 15. Compatibility

- 旧记录：291 条 legacy 可读可检索（ORM 默认值兜底），content/embedding/未动 ✓（B9/B10）
- 旧 API：`/memory/*` 路由、`memory_search_tool` 签名不变 ✓；`store_single` 签名不变（provenance 走 fact 字段）✓
- 旧调用方：`MemoryFact` 基础构造默认 legacy ✓（B7 实证）
- `save_turn` 返回值契约未变（本就返回 tuple，仅开始被消费）
- 主聊天接口/SSE 帧序不变；user_id 隔离测试全绿（B12 ✓）

## 16. Remaining Risks

**STOP C ownership**：memory_key 与结构化值、top-1 blind spot、0.85~0.92 改口静默丢弃、explicit vs inferred 冲突优先级裁决、tenant scope（含 (user_id,tenant_id,memory_key) partial index）、decay 死代码正式决策（接线 beat 或移除，本轮未动）、memory_type CHECK 约束与脏类型清理。
**STOP D ownership**：L3 SystemMessage 裸注入（G4）、relevance threshold、mark_accessed 主路径缺失、expire_at 检索过滤、L3 SystemMessage 享受预算豁免（F5）。
**本轮遗留**：①实机"真实 LLM 提取写入"待豆包配额恢复后复验（外部动作：充值或管理台关闭 use-free-tier-only；FreeTierOnly 为既存登记问题）；②tool 通道 `source_message_id=NULL`（ContextVar 无 message id，接通需 request_context 扩展，收益/成本待 STOP C 评估）；③`memory_extraction_rejected_total` 的 `assistant_only` 计数目前只能由测试与生产流量观测（实机提取被配额阻塞，暂无生产样本）。

## 17. Commit

见提交 `fix(memory): harden fact provenance and explicit origin`（hash 以 git log 为准，路径限定提交，仅含 §3 文件清单 + 本报告）。
