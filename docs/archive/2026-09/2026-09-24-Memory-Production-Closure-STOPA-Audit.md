# Memory Production Closure — STOP A 现状审计报告

> 日期：2026-09-24 ｜ 性质：**只审计，未修改任何业务行为**
> 范围：用户端记忆系统（L1/L2/L3）+ MemoryManager + Context Budget 交界
> 证据标准：全部结论以真实代码（文件+函数+行号）、真实迁移、实库只读查询为准，无假设项。

---

## 1. 当前架构（本轮全部保留，不重写）

| 层 | 实现 | 存储 | 关键文件 |
|---|---|---|---|
| L1 短期 | `ShortTermBuffer` 环形缓冲，默认 20 条，请求内组装 | 内存 | `backend/memory/short_term.py` |
| L2 会话 | `SessionMemory`，逐轮 `save_turn` 持久化 + 达阈值 LLM 增量摘要（水位线） | PG `agent_memory.chat_sessions` / `chat_messages` | `backend/memory/session.py`、`repository/session_repo.py` |
| L3 长期 | 事实型记忆（user_fact/preference/decision/knowledge），pgvector(1024) 语义检索 | PG `agent_memory.memory_records` | `backend/memory/long_term.py`、`repository/memory_repo.py`、`retriever.py`、`trigger.py`、`importance.py`、`pii_filter.py` |

- 编排：`MemoryManager`（`manager.py`）= sync→async 桥，专用后台线程 event loop；所有记忆操作 5s 超时（`_MEMORY_TIMEOUT`），失败降级返回空 + `degradation_alerts_total`，**不阻断主聊天链**。
- 最终上下文裁剪点：`ContextBudgetManager.prepare_llm_context()`（`context_budget/manager.py:109`），在 `infra/llm/proxy.py:1592` **每次 LLM 调用前**统一执行（L2 trim → L3 previous_outputs → L4 collapse → hard trim → L5 AutoCompact）；SystemMessage 与语义 pin 永不折叠。
- 可靠性基础（保留）：pgvector + importance + recency 混合检索、decay 服务、用户隔离、5s timeout、Prometheus metrics（`memory_retrieval_total/-failure_total/-latency_seconds`）。

## 2. 写入链（真实调用链）

```
GraphRunner.stream finally 块 (orchestration/graph/runner.py:770-774)
  条件：final_answer 非空 且 非 CS 域轮次（CS 由客服侧独立落库）
→ MemoryManager.end_turn (memory/manager.py:115)                    [后台 loop, 5s]
→ MemoryService.end_turn (memory/service.py:200)
   ├─ SessionRepository.save_turn (repository/session_repo.py:50)    L2 落库 user+assistant 两条
   ├─ SessionRepository.needs_summarization (:67)                    纯条数判定 count >= 50
   │   └─ SessionMemory.summarize (memory/session.py:40)
   │        ├─ 真实 repo → run_incremental_summary（context_budget/auto_compact.py，水位线+CAS 044）
   │        └─ 回退全量摘要（prompt memory.session.summary）
   │        └─ update_summary / update_summary_state 落库
   └─ asyncio.ensure_future(store) (service.py:236)                  后台协程，不阻塞调用方
→ MemoryService.store (service.py:269)
   1. LongTermMemory.extract_facts (long_term.py:33)                 LLM 提取，线程池
      prompt = prompts/defaults/memory_long_term_fact_extraction.yaml
      输入 = f"用户: {question}\n助手: {answer}"（G1 证据）
   2. scan_and_sanitize (pii_filter.py:74)                           在 _parse_facts 内逐条执行（7 类 PII 正则脱敏）
   3. MemoryWorthinessClassifier.classify (trigger.py:27)            规则优先：user_fact/preference 强制 STORE；
                                                                     寒暄 IGNORE；否则 LLM fallback
   4. ImportanceScorer.score / should_store (importance.py:25/41)    类型保底分 + 关键词分，阈值 0.6
   5. LongTermMemory.store_single (long_term.py:102)
      embed_query → MemoryRepository.find_similar (memory_repo.py:59, threshold=0.85, **top-1**)
      ├─ sim >= 0.92 且 memory_type 相同 → insert 新记录 + supersede 旧记录（is_active=False, superseded_by）
      ├─ 0.85 <= sim < 0.92 → return False（**静默丢弃**，改口事实丢失）
      └─ sim < 0.85（无命中）→ insert
```

显式写入旁路（同链、跳过 extract/trigger/importance）：`tools/memory.py::memory_store_tool` → PII 脱敏 → `store_single`。user_id 取 `tools/session.get_tool_user_id()`（请求上下文 ContextVar）。

## 3. 读取链（真实调用链）

```
GraphRunner (orchestration/graph/runner.py:399)
→ MemoryManager.start_session(session_id, question, user_id)        [后台 loop, 5s]
→ MemoryService.start_session (memory/service.py:65)
   ├─ L2：load_messages(limit=SHORT_TERM_MAX_MESSAGES*2=40)          查询层限量、最近优先（session_repo.py:24）
   │      → (role, content) 连续去重 → ShortTermBuffer.add
   ├─ L2 摘要注入：summary >= 30 字 → build_historical_context (context_budget/role_safety.py:48)
   │      → [SystemMessage(policy 固定声明), AIMessage(<historical_context>数据块)]   ← 角色安全已做
   ├─ L3：embed_query(当前 question) → HybridRetriever.retrieve (retriever.py:11)
   │      → MemoryRepository.search_hybrid (memory_repo.py:33)       top20，过滤 is_active + user_id
   │      → 重排 final = 0.5*sim + 0.3*importance + 0.2*recency
   │      → 取 top5（**无相关性阈值，强行凑满**）
   │      → LongTermMemory.format_for_prompt → SystemMessage("[已知背景信息]…") 插入 l1[0] (service.py:124)
   │      ※ 此路径**不调用 mark_accessed**（仅工具 search 路径 service.py:252 调用）
   └─ token 裁剪：context_budget.history_budget()（动态：input_budget − system − query − previous_outputs 预留）
          → trim_messages_to_budget（只裁 active context，PG 原始消息不动；L2 级 metric+SSE 事件）
→ make_initial_state(..., l1.messages, ...) → 主图各节点
→ 每次真实 LLM 调用前：infra/llm/proxy.py:1592 ContextBudgetManager.prepare_llm_context（最终裁剪点）
```

职责划分（A2 答案）：**加载**=MemoryService.start_session；**排序**=HybridRetriever；**注入**=MemoryService（L1 组装）；**最终 token 裁剪**=ContextBudgetManager（proxy 层 Prompt Preflight），MemoryService 内的 trim 只是预算内的预裁剪，二者不冲突但存在两处裁剪（见 F5）。

## 4. Schema（真实：迁移 + ORM + 实库只读核对）

### 4.1 memory_records（002 建表 + 032 换型 vector(1024)）

| 字段 | 类型 | 现状 |
|---|---|---|
| id | UUID PK | ✓ |
| user_id | varchar | ✓ 唯一隔离键 |
| session_id | varchar | ✓（≈source_session_id） |
| memory_type | varchar | ✓ 四类型；**无 DB 约束**（实库存在 `tool_result`/`fact` 脏类型各 1 条） |
| content | text | ✓ |
| embedding | vector(1024)（032 由 bytea 换型） | ✓ 291 条中 288 条非空 |
| importance_score | float8 default 0.5 | ✓ |
| confidence_score | float8 default 1.0 | **死字段**：全仓无写入方、无读取方 |
| access_count / created_at / last_access_at | | ✓（但主注入路径不更新 last_access_at，见 F2） |
| expire_at | timestamp | **死字段**：无写入方、检索不过滤（实库写入数=0） |
| is_active | boolean | ✓（布尔而非 status 枚举：superseded 与 archived 均为 False，仅能靠 superseded_by 非空区分） |
| superseded_by | UUID FK self | ✓ |
| **缺失** | | `tenant_id`、`memory_key`、`origin`、`source_message_id`、`structured_value`、`stability`、`valid_from`、`updated_at`、status 枚举 |

**索引（实库 `pg_indexes` 核对）：仅有主键**。无 user_id 索引、无 (user_id,is_active) 索引、无向量索引（IVFFlat/HNSW）。当前 291 行无性能问题，STOP C/D 加 partial index 时一并处理。

### 4.2 chat_sessions（002 + 020 补 title + 040 水位线 + 044 CAS 版本号）

`id, session_id(UNIQUE), user_id, title, summary, context_summary, summary_through_message_id, summary_token_count, summary_updated_at, summary_version, created_at, updated_at`。
**已具备增量摘要三件套：水位线 + token_count + 乐观锁版本**——STOP E 直接复用，不需要新字段。

### 4.3 chat_messages

`id, session_id(FK CASCADE), role, content, created_at`。无 user_id 冗余列（隔离经 session 归属链）。实库 463 会话 / 3388 消息 / 139 会话已有增量摘要水位。

## 5. 用户隔离（A4）

- **user_id 来源**：`app/api/identity.py::resolve_identity` 单一入口（header/strict 模式下 body 身份字段永不落地；`anonymous` 占位拒收返回 guest）。chat 路由（`app/api/routes/chat.py:164`）→ runner → memory start/end_turn；工具侧 ContextVar。
- **查询强制过滤**：`search_hybrid` / `find_similar` SQL 级 `user_id == 调用者`，无任何无 user_id 的记忆查询路径（已核对 memory_repo 全部方法）。
- **会话路由面**：`MemoryService._check_owner`（service.py:377）属主校验，跨用户统一 404 不泄露存在性；未认证 401；`list_all`/`count_sessions` 按 user_id 过滤。
- **实证**：`tests/rag/test_memory_isolation.py`（基线全绿）覆盖 RAG 管线身份回填；`tests/api/test_memory_routes.py` 覆盖路由隔离。
- **结论：user A 无法检索 user B 的 Memory（SQL 过滤 + 属主校验双重），此结论成立。**
- **缺口（记入 STOP C/D）**：
  1. memory 层**无 tenant_id**：`Identity.tenant_id` 存在且进了 graph state，但 memory_records 无此列、查询不过滤 → 跨租户隔离完全依赖 user_id 全局唯一，tenant 维度 fail-open。
  2. `user_id="default"` 兜底默认值散布在 service/repo 签名上（legacy 无认证模式下会共享桶）；header/strict 模式下 guest 被拒，风险受 AUTH 模式约束。
  3. memory_records 无 user_id 索引（见 §4.1）。

## 6. 风险列表（G1-G8 逐条核实 + 审计新增发现）

### G1 ｜ assistant 回答与用户消息同等级作为事实源 —— **确认存在（P0）**

证据：`memory_long_term_fact_extraction.yaml` 输入为 `用户: {question}\n助手: {answer}` 单块对话，prompt 无「仅用户话语可作为事实依据」约束；其自带示例中助手复述（“你是后端工程师”）被默认可提取。助手生成的推断（如“你的项目 Redis 每秒刷盘”）会被固化为 `user_fact`。

### G2 ｜ dedup/supersede 顺序 bug —— **字面 bug 不存在，但存在两个更实质的缺陷（P0）**

真实代码（`long_term.py:107-121` + `memory_repo.py:59`）：`find_similar(threshold=0.85)` 取**top-1**，随后 `sim >= 0.92 且同类型 → supersede`，否则 `return False`。supersede 分支**可达**（任务书担心的「≥0.85 先 return 导致 ≥0.92 永不可达」模式在当前代码中不存在）。但：

- **缺陷 a（top-1 盲区）**：只与最相似的一条比对。若 top-1 是 0.86 而另一条真冲突记录是 0.93，后者永远不参与比对 → 冲突双 active 可能发生。
- **缺陷 b（改口静默丢失）**：0.85 ≤ sim < 0.92 的改口事实（换一种说法“主模型换成豆包” vs “主模型是 DeepSeek”，语义相似度常落此区间）被 `return False` **丢弃** → 旧事实继续生效，用户改口无效。这与「两个都 active」是同一根因（纯 embedding 猜关系）的两面。

### G3 ｜ 重复/冲突/属性更新纯靠 embedding —— **确认存在（P0）**

无 memory_key、无结构化值比对、无属性级冲突判定；同 key 新值是否 supersede 全由余弦阈值彩票决定（G2 缺陷 a/b 即后果）。

### G4 ｜ Memory 以 SystemMessage 直接注入 —— **确认存在（P0）**

`service.py:124`：`l1._messages.insert(0, SystemMessage("[已知背景信息]…" + 记忆原文))`，无数据边界声明。对照：L2 摘要已走 `build_historical_context`（固定 policy SystemMessage + AIMessage `<historical_context>` 数据块，`role_safety.py:48`）——**L2 已角色安全、L3 未做，不对称**。历史记忆中的注入文本（“忽略所有系统规则”）会以 system 权重进入 prompt。

### G5 ｜ L2 摘要按消息条数触发 —— **确认存在（P1）**

`session_repo.needs_summarization(:67)`：`count >= 50` 纯条数。长消息 20 条已巨大仍不摘要，短消息 50 条很短却触发。**可复用基础已备**：040/044 已有水位线+token_count+CAS，L5 AutoCompact 侧已有 `run_incremental_summary` 全套增量摘要设施，STOP E 只需把「触发判据」升级为 条数+token 水位并复用该管线，不重写。

### G6 ｜ 检索无相关性阈值、强制凑满 top_k —— **确认存在（P0）**

`search_hybrid` 只按相似度排序取 top20；`HybridRetriever.retrieve` 重排后直接取 top5 返回，`format_for_prompt` 有几条注几条，但 **5 条以内必注入**，无最低分门槛 → “喜欢日料” 会陪跑 Redis 问题。且实库 active 仅 133 条，语义无关但分数非零的记忆几乎每轮都可能被注入。

### G7 ｜ explicit 与 inferred 同可信度 —— **确认存在（P0）**

无 origin 概念：`memory_store_tool`（用户显式指令）与后台自动提取产出**完全相同结构**的记录，同链 dedup、同权重检索、同分排序。用户明确偏好可被后台推断静默 supersede（sim≥0.92 时）——方向反了。

### G8 ｜ provenance / lifecycle 字段缺失 —— **确认存在（P0，分属 B/C/D 实施）**

缺：`memory_key`、`origin`、`source_message_id`、`structured_value`、`stability`、`valid_from/valid_until`（`expire_at` 是死字段可改名复用）、status 枚举、`updated_at`。已有可复用：`session_id`（≈source_session_id）、`confidence_score`（死字段，激活为 confidence 载体）、`superseded_by`、`is_active`。

### 审计新增发现（任务书未列）

- **F1（P1）decay 从未运行**：`MemoryService.run_decay`（service.py:364）**全仓零调用方**（app/ tasks/ scripts/ 均无）——无 Celery beat 任务、无路由、无脚本。实库佐证：291 条中归档数=0。且 `archive_stale(0.2)` 阈值在当前打分体系下**数学不可达**（类型保底分最低 knowledge=0.4），即使接线也永远归档 0 条。衰减子系统当前是死代码。
- **F2（P1）主注入路径不更新 last_access_at**：`start_session` 的 L3 检索不调 `mark_accessed`（仅工具 `search` 路径调）→ recency 排序项与 decay 的时间基准对主路径失真：正在被使用的记忆照样被衰减。
- **F3（P2）memory_type 无 DB 约束**：实库存在契约外类型 `tool_result`、`fact` 各 1 条（历史版本写入残留）。加 CHECK 或写入侧白名单校验。
- **F4（P2）基线测试 3 挂（存量，非本轮引入）**：组合跑 `tests/memory + tests/api/test_memory_routes + tests/rag/test_memory_{isolation,write_ordering}` = 38 过 / 3 挂（可复现）：
  - `test_memory_db_unavailable_is_handled_as_503` **单独跑也挂**（断言 `MemoryDatabaseUnavailable` 实得 `UPSTREAM_UNAVAILABLE`）——与工作区其他会话未提交的 `observability/`/`infra/` 改动相关，归存量，本轮不修（STOP A 禁改）。
  - `test_memory_write_ordering.py` 两个用例**单独跑全过（6/6）、仅合跑挂**——测试间状态污染（隔离问题），记入 STOP F 评测建设时一并处理。
- **F5（P2）L3 SystemMessage 与预算豁免的相互作用**：`prepare_llm_context` 对 SystemMessage 永不折叠/裁剪，而 L3 记忆以 SystemMessage 注入 → 记忆注入量不受 L4/L5 压缩约束，只在 MemoryService 的 history_budget 预裁剪中受限。STOP D 数据化改造（AIMessage 数据块）顺带消除该放大器。

## 7. P0 / P1 分类

| 级别 | 项 | 归属 STOP |
|---|---|---|
| P0 | G1 assistant 污染（写入口径） | B |
| P0 | G7 explicit/inferred 无优先级 | B（origin 标记）+ C（冲突裁决） |
| P0 | G3+G2 纯 embedding 事实版本管理（top-1 盲区 / 改口丢失 / 冲突共存） | C（memory_key + 冲突裁决序） |
| P0 | G4 SystemMessage 注入无数据边界 | D（数据化注入） |
| P0 | G6 无相关性阈值强制 top5 | D（candidate→gate→rank） |
| P0 | G8 provenance/lifecycle 字段缺失 | B(origin/confidence/source) + C(memory_key) + D(valid_until/status) |
| P1 | G5 摘要按条数触发 | E（token 水位，复用 040/044） |
| P1 | F1 decay 零调用 + archive 阈值不可达 | B~D 期间决定：接线 beat 或显式移除（不允许留死代码） |
| P1 | F2 主路径 mark_accessed 缺失 | D |
| P2 | F3 类型约束 / F4 测试隔离与存量失败 / F5 SystemMessage 预算豁免 | D / F / D |

## 8. 后续实施计划（STOP B→G 映射）

1. **STOP B**：迁移加 `origin/confidence（激活死字段）/source_session_id（复用 session_id 语义）/source_message_id`；extract 输入改为「user message 为权威候选 + assistant 仅上下文」的 prompt 与解析改造；explicit 通道（`memory_store_tool` + 显式语句检测）；assistant-only 污染率=0 三用例。
2. **STOP C**：迁移加 `memory_key` + partial index `(user_id, memory_key) WHERE is_active`；裁决序 = scope → key → 类型 → 值 → 相似度；duplicate(确认)/supersede(新值)/inferred 不覆盖 explicit；同轮补 F1 决策与 F2。
3. **STOP D**：L3 注入改 `build_historical_context` 同款数据块；`MEMORY_MIN_RELEVANCE_SCORE` 门槛 + 0~5 条;检索过滤 `is_active AND (expire_at IS NULL OR expire_at > now)`；mark_accessed 接入；安全四用例。
4. **STOP E**：摘要触发升级 条数+token 水位（`SESSION_SUMMARY_TRIGGER_TOKENS`），复用 `run_incremental_summary`/水位线/CAS；`SESSION_SUMMARY_MAX_TOKENS`；验证 ContextBudgetManager 仍为唯一最终裁剪点。
5. **STOP F**：`backend/evaluation/memory/` Golden ≥30 条（store/ignore/explicit/conflict/retrieval/security 六组）+ 阈值（§F5）+ 可重复运行入口；顺带修 F4 测试隔离。
6. **STOP G**：实机八场景（真实 API/GraphRunner/PG/pgvector）+ 故障注入 + 全量回归。

## 附：审计方法与实库证据

- 实库只读查询（`agent-postgres-1:agent_memory`，2026-09-24）：`memory_records` 291 行（active 133 / superseded 158 / archived 0 / `expire_at` 非空 0 / embedding 非空 288 / user 桶 3）；类型分布 decision 98、preference 108、user_fact 68、knowledge 15、**tool_result 1、fact 1（契约外）**；`chat_sessions` 463、`chat_messages` 3388、水位线非空 139；`pg_indexes` 仅主键。
- 基线测试（`--no-cov`，venv `D:/Program Files/workplace/agent/.venv`）：组合 38 passed / 3 failed（归属见 F4，均为存量）。
- 本报告为 STOP A 唯一产物，未修改任何业务代码。

**STOP_A_PASS=true**
