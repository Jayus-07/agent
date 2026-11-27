# Agent Platform 代码级面试背书与线上演示指南

> 本文档完全基于仓库最新真实源码与近期演进（截至 commit `bfcb35a`）整理，专为面试前快速记忆、线上系统演示及应对技术深挖而编写。

---

## 一、 2 分钟项目介绍（标准背诵稿）

> **记忆骨架：1 个定位 + 3 个端 + 4 层架构 + 4 大核心业务 + L1-L5 统一记忆治理**

“这个项目是我按生产级标准从零搭建并已部署上线的**面向企业/电商场景的多域 AI Agent 平台**。

**业务上**，它把**企业知识问答（RAG）**、**自然语言查数（NL2SQL）**、**智能客服**和**旅游规划（V2）**四大核心场景，收敛到同一套平台底座上（早期探索过的选品漏斗，后来为了收敛稳定性和生产质量做了架构裁剪下线）。

**架构上**，整体分为四层：
1. **接入与安全边界**：有三个独立的 Next.js 前端（用户端、管理端、客服坐席工作台），所有流量统一经过 **Apache APISIX 网关**做 JWT 验签、按用户/IP 限流和身份头注入；FastAPI 后端再叠加 API Key、Bearer JWT 的 Redis 会话闸以及 `GATEWAY_TRUST_SECRET` 防直连伪造，彻底做到了不信任客户端透传的 `user_id`。
2. **AI Runtime 编排层**：核心基于 **FastAPI + LangGraph**。请求进 `/chat/stream` 后，先过 Input Guard 门禁、记忆装配和指代解析，再由统一的 `RoutingEngine` 拍板去向——简单能力走 `tool_selector → skill_executor` 两段直连、固定流程走 Workflow、复杂任务走 Plan 并行调度（LangGraph `Send` API）并在满足多步条件时无缝移交后台 `TaskGraphExecutor` 跑断点续跑，或者进入客服/旅游垂直域图，最后通过支持**断线重连（F2 Resume Protocol）**的 SSE 流回传前端。
3. **L1～L5 统一记忆与上下文治理**：在模型调用前，不是简单做列表切片，而是建立了一套从 **L1 工具预览截断**、**L2 会话历史动态裁剪**、**L3 长期事实画像与技能前置输出拓扑压缩**、**L4 零 LLM 可逆折叠** 到 **L5 AutoCompact 单飞增量摘要与 Fail-closed 硬门禁** 的完整生命周期治理体系，保证长对话不失忆、关键业务约束零遗失，且防死循环扣费。
4. **能力、数据与可观测层**：**PostgreSQL + pgvector** 同时承载业务、会话、图 Checkpointer 以及原生 `tsvector` GIN 倒排全文索引；**Redis + Celery** 双队列跑 RAG 索引与长期记忆提取等异步任务；自研 Tracer（PG 为主，支持 OTLP 镜像导出）配合 **Prometheus + Grafana** 做全链路监控和进程内存水位告警。”

---

## 二、 线上服务器演示动线（4 分钟高分带看路线）

面试时**主动引导面试官按“工程深度”看系统**，不要只当成聊天框来测：

1. **第一站：用户端 `/agent` — 秀“流式编排与断线恢复”（1 分钟）**
   - **操作**：发一个复合问题，让界面展示出路由、工具调用进度和流式输出。
   - **解说**：“您看这里前端通过 SSE 接收 `meta / status / thinking / log / delta / usage / context` 等结构化事件。背后不是简单的 `while` 循环，而是 `RoutingEngine` 做了六阶段路由决策，而且我做了 **F2 断线恢复协议**——每帧带单调递增 `seq`，哪怕移动端切后台断网，重连 `/stream/resume` 也能原子补发漏掉的帧，不会丢字。”
2. **第二站：用户端 — 秀“RAG 混合检索 + 防幻觉零容忍”（1 分钟）**
   - **操作**：先问一个知识库里有精确数字/条款的问题（展示 `[1][2]` 内联引用）；再问一个知识库里没有的问题（展示主动拒答）。
   - **解说**：“这里底层是 **pgvector 向量 + PostgreSQL 原生 GIN 倒排全文**做加权 RRF 融合再重排。为了防大模型胡说八道，我在最后加了 **Evidence Gate 多道防线**，其中包含一个**纯程序化的 `ClaimVerifier`**，会逐条核对引用里的数字、单位、日期和金额，零容忍，大模型自己说‘没问题’也绕不过去。”
3. **第三站：用户端 — 秀“NL2SQL 问数与安全隔离”（1 分钟）**
   - **操作**：用自然语言查一次业务数据，展示生成的 SQL 和结果。
   - **解说**：“问数最怕越权和删库。我的做法是先过 `SQLPolicyContext` 按租户和角色限定可见表，再用 `sqlglot` AST 解析把租户条件**原地注入到每一层 SELECT/CTE**，配合**全函数正向白名单**和只读数据库账号，安全被拒的 SQL 直接走终态拦截，绝对不让模型重试绕过；并且对云端模型显式禁用了思考（thinking），耗时压到了 2 秒左右。”
4. **第四站：管理端（6 组二级分区）+ Grafana — 秀“生产级治理”（1 分钟）**
   - **操作**：切到管理端，快速点开「知识库工作台」「模型与供应商」「运行监控 / 服务健康与告警」。
   - **解说**：“我把管理端重构成了 6 大分区。模型 Key、价格预算、知识库权限和文档审核都在这里热管理。文档入库后先写候选代次，发布时做原子 CAS 切换；后台还配了 Prometheus 进程内存 83% 水位告警和全链路 Trace。”

---

## 三、 8 张源码级技术亮点背诵卡片

### 卡片 1：RAG 全文检索架构演进（PG GIN 替代单机 BM25 pickle）
- **痛点**：早期用单机 `rank_bm25` + 磁盘 pickle 快照，在 4C8G 容器环境下全量加载和重算 IDF 极易 OOM，而且多文档并发发布时要抢 `.publish.lock` 文件锁，非常脆弱。
- **真实方案**：
  - 在 `rag_vectors` 表增加 `tsv_content tsvector` 列和 GIN 倒排索引（迁移 `156_rag_vectors_tsv_gin.sql`）。
  - 写入端用 `jieba.cut_for_search()` 分词后通过 `to_tsvector('simple', ...)` 行级原子落库；检索端 `PGFullTextRetriever` 用 `ts_rank_cd` 打分，100% 兼容上层 RRF 融合和权限过滤。
- **收益**：实现毫秒级**行级增量全文检索**，彻底消除 pickle OOM 和文件锁竞争，且天然跟随 `collection` 原子切换。

### 卡片 2：RAG Indexer 7 阶段管线重构 & 并发发布 CAS
- **痛点**：原 `indexer.py` 膨胀到 3500+ 行难以维护，且以前经常出现“文档刚入库排队，管理员把它下线了，结果迟到的异步任务又把它覆盖上架”的并发 Bug。
- **真实方案**：
  - **7 阶段物理拆分**（`backend/rag/indexing/stages/`）：`ParseStage`（10 种格式 AST + OCR）→ `FilterStage`（SHA256/SimHash/PII）→ `QualityStage`（**付费前 0 费用完整性预检**，不合格直接阻断不调付费 Embedding）→ `MetadataStage` → `EnrichStage` → `EmbeddingStage`（并发批处理 + Redis 缓存）→ `PersistStage`（异常逆向回滚）。
  - **两阶段隔离发布 CAS**：候选期向量写入 `{main}::cand:{gen}` 隔离命名空间；提交点 `register_published()` 按 `(active_generation, expected_base_status)` 做数据库双基准 CAS，若文档已被下线/送审或被新代次抢先，迟到任务输掉 CAS 并自动清理候选，**撤销永远优先于排队发布**。

### 卡片 3：全平台 L1～L5 统一记忆与上下文治理架构（王牌亮点）
- **架构全景**：
  - **L1（单轮/工具级防护）**：`short_term.py` 内存消息缓冲 + `tool_guard.py` 工具结果 Token 检查截断并生成 `ToolResultRef` / preview 结构，发出 `level="L1", action="tool_compact"`。
  - **L2（会话记忆与动态裁剪）**：`session.py` 在 PG 持久化会话历史；模型调用前 `manager.py::history_budget()` 动态计算可用历史空间，`_trim_semantic()` 执行语义裁剪（`PinnedContext` 业务锚定与 System 消息永不丢弃），发出 `level="L2", action="history_trim"`；`end_turn()` 后后台异步触发增量摘要。
  - **L3（长期事实画像与输出拓扑）**：`long_term.py` 抽取用户偏好与事实，PG 事务内 Outbox 异步解耦提取；8 态版本链裁决（删除屏障、乱序防护、推断不覆盖显式）；`micro_compactor.py` 压缩前置步骤 `previous_outputs`（`level="L3"`）。
  - **L4（零 LLM 可逆折叠）**：用量达到 80% 触发 `collapse.py`，确定性替换早期对话为 `ContextFold`（`[Earlier conversation folded]`），滞回逐步收紧保留轮数至 65% 安全区，产生 `level="L4", action="collapse", reversible=True`，pin 消息不折叠。
  - **L5（AutoCompact 增量摘要单飞与硬门禁）**：用量达到 90% 触发 `auto_compact.py`，单飞锁递归防重入；轻量摘要模型压缩并跑 **40 条 `ProtectedFact` 白名单（用户禁令/实体/日期/金额）确定性补丁**；若全量压缩后仍超限，标记 `overflow=True`，统一由 LLM Proxy 抛出 `ContextBudgetExceededError` **Fail-closed 硬拦截**，绝不盲目超长发送。

### 卡片 4：三层记忆（L1/L2/L3）事务 Outbox 与 8 态冲突裁决
- **Bridge 超时保护**：`MemoryManager` 在独立守护线程跑事件循环，对外 5s 超时主动 `future.cancel()` 并上报降级，绝不卡死主对话。
- **同事务 + Outbox 防丢**：`end_turn()` 在**同一 PG 事务**内原子提交 L2 消息、幂等收据（`pg_advisory_xact_lock` 防并发重放）与 L3 提取 Outbox（Celery 仅传 UUID），回答收尾不等待队列；即使队列挂了，后台 `recover_memory_extractions()` 也能扫描补投。
- **冲突裁决 8 态版本链**：`resolve_keyed()` 依次过身份加锁 → **删除屏障（`BLOCKED_BY_DELETE`）** → `find_active_by_key(for_update=True)` → 同值重申（`REAFFIRMED` 只升不降）→ **乱序事件防护（`BLOCKED_STALE_EVENT`）** → `origin` 矩阵裁决（`inferred` 撞 `explicit` 返回 `CONFLICT_BLOCKED_EXPLICIT`；覆盖走原子 `deactivate` + `insert` + 回填 `superseded_by`）；Dreaming 整合每日聚类合并，安全类内容（过敏/禁忌等）豁免衰减。

### 卡片 5：LangGraph 编排、F2 断线恢复与后台 `TaskGraphExecutor`
- **主图与域边界**：`/chat/agent` 入口强制锁定 `agent` 产品域，不生成跨助手 handoff；`RoutingEngine` 缓存键纳入了路由策略版本、**`manifest` 指纹、模型角色指纹和 Prompt 版本指纹**，且不缓存降级/澄清结果。
- **F2 断线恢复（`stream_resume.py`）**：`StreamRecord` 注入单调 `seq`，客户端断连时**不杀后台 producer**；重连 `/stream/resume` 在同一把锁内原子拷贝重放尾并接回实时队列，缓冲溢出则诚实返回 404 `STREAM_NOT_RESUMABLE`。
- **后台长任务移交 & 租约 Fencing（`task_executor.py`）**：复杂计划经 `critique` 后移交后台 `TaskGraphExecutor`（强制使用 PostgreSQL Checkpointer）；节点边界通过 `_guard_lease()` 校验 `execution_id` 租约防止双活旧 Worker 写脏状态，且**每次恢复都重查 `auth.users` 最新权限**，撤权立即生效。支持静止态下的确定性 `PlanRevision`（`base_plan_version` 乐观锁 409）。

### 卡片 6：NL2SQL 纵深防御与 5 倍延迟优化
- **六层安全（`policy.py` + `sql_validator.py` + `executor.py`）**：
  1. `precheck` 前置 `sql.read` 门禁（未过权限不调 LLM）；
  2. `sqlglot` AST 解析，按 `TablePolicy.data_domain` 对所有 `exp.Select`（含 CTE/子查询/UNION）**原地注入参数化租户/部门条件**；
  3. 强制表/Schema/列白名单（含 CTE 列白名单收紧）；
  4. **全函数正向白名单（`POSITIVE_FUNCTION_ALLOWLIST`）**，而非脆弱的黑名单；
  5. 强制 `LIMIT`、禁止 `SELECT INTO` 与锁子句、自动补 PG 保留字双引号；
  6. `agent_readonly` 只读账号 + `SET TRANSACTION READ ONLY` + `statement_timeout`。
  - **终态分类**：安全拒绝标为 `TERMINAL_DENY_REASONS`，严禁进入重试循环。
- **延迟优化**：针对思考型云端模型在 SQL 生成和路由阶段过度推理的问题，显式禁用 thinking（`extra_body={"thinking": {"type": "disabled"}}`，`temperature=0.0`），端到端耗时从 **8.7–18.8s 降至 1.7–3.9s**。

### 卡片 7：RAG 三层路由、精确标识符加成与程序化防幻觉
- **三层路由（`query_router.py` / `hybrid.py`）**：简单 FAQ 走 `vector_only` 降本提速；普通或含精确编码的走 `hybrid`；复杂多条件/跨文档走 `hybrid_multi_query`（带 20s 共享 Deadline 与 6s 改写子预算，防止慢查询拖垮全链路）。
- **精确标识符加成**：针对长数字、SKU 编号在 RRF 融合中容易被语义检索淹没的问题，专门注入 `_EXACT_TOKEN_BONUS` 加权。
- **多道 Evidence Gate**：`Retrieval Gate` → `Rerank Gate` → **程序化 `ClaimVerifier`**（正则+规则核对 `[En]` 原文中的数字+单位/日期/金额/百分比，零容忍）→ `Faithfulness` → `SelfCorrectionStrategy`。全部通过才写记忆，防止拒答幻觉污染上下文。

### 卡片 8：智能客服派单状态机 & 生产可观测闭环
- **客服派单回收（`reaper.py` + `offers.py`）**：严格遵守 `conversations -> handoffs -> (agent)` 锁序结合 `SKIP LOCKED`，不依赖 Redis 分布式锁；Offer 30s 未认领自动释放并本单永久排除该坐席；超过尝试次数或总 deadline 时**同事务原子关单 + 创建 `GD-YYYYMMDD-XXXX` 留言工单 + 写 Outbox**，事务提交后有界并发异步压缩用户诉求回填。
- **可观测与容量告警**：自研 Tracer（PG 主存）+ `OtelSpanMirror`（OTLP 异步镜像出口）；`llm_call_span.py` 在 proxy 层统一埋点保证与计费账本 1:1 同源不双计；Prometheus 新增 `agent-platform-memory-capacity`（RSS > 640MB / 768MB 上限 83% 持续 5m 告警）并支持 SSH 隧道远程零负载观测。

### 卡片 9：内置沙盒商城（Mall）与订单协同闭环（实战演示绝杀项）
- **背景与痛点**：很多候选人讲客服 Agent 时，被问到“订单数据从哪来、怎么联调改单退款”就会露馅说是写死的 Mock。
- **真实方案**：
  - 项目在用户端直接实现了一套**内置沙盒商城（`frontend/src/app/mall`）与订单中心（`my-orders`）**，具备商品浏览、模拟下单与真实 `mall_orders` 数据落库；
  - **淘宝级三入口统合**：在商城页通过浮窗一键呼起 **订单咨询抽屉（M1）**，服务端自动注入并锁定当前正在浏览或咨询的 **`order_ref`（M2）**；
  - **订单卡即时本地渲染（M3）**：在 AI 对话或转人工客服时，消息流中直接渲染高度结构化、可交互的订单卡片，坐席在客服工作台（`frontend-cs`）可直观核对商品、金额、在途物流并执行 B1–B8 业务动作（催单、改址、价保、退款等）。

### 卡片 10：Prompt 4 级读取体系与生产发布质量门禁（Release Gate 0–12）
- **Prompt 4 级高可用读取（`backend/prompts/`）**：
  - 避免将 Prompt 硬编码在代码或频繁打数据库，采用四级回退：`进程内字典快照（RLock 保护）` → `二级缓存（L1 本地内存 30s + L2 Redis TTL 300s）` → `PostgreSQL 持久表` → `本地 YAML 文件兜底`；
  - 管理端修改发布后，秒级失效缓存（`clear_cache()`）实现**零停机热更新与一键版本回滚**；
- **生产发布流水线（Release Gates 0～12）**：
  - `scripts/release.sh` 与 GitHub Actions self-hosted runner 深度集成，发布前强制过 12 道门禁：Gate 0（Commit 与镜像 Digest 强校验）、Gate 5（只读数据库权限对齐与回滚防线）、Gate 10（Celery Worker 探活）、Gate 11（主模型 24h 真实外呼健康度验证）、Gate 12（Prometheus 告警与目标在线检查），杜绝带病上线。

### 卡片 11：平台双向 MCP 协议出口与生态兼容
- **双向架构（`mcp_servers/` & `backend/infra/mcp_client.py`）**：
  - **向下消费**：系统作为 MCP Client，消费高德地图、天气等外部 MCP Server 工具（配置级接入，快照 15s 刷新，写操作强一致复验）；
  - **向上暴露**：自研 `mcp_servers/protocol_app.py` 实现了平台自身的 **MCP Server**，通过 `schema_adapter.py` 将系统内部的 RAG、SQL、知识检索等 Tool 自动转译为标准 MCP 协议，可直接接入外部 Cursor、Claude Desktop 等第三方开发生态。

### 卡片 12：RAG 工业级 Parent-Child（父子块层级切分与回溯召回）
- **核心痛点**：传统固定切分要么太小（缺乏上下文，模型答非所问），要么太大（向量相似度被语义稀释，检索不准）。
- **两级解耦架构（`backend/rag/preprocessing/chunking.py`）**：
  - **Leaf Chunk（200 tokens）**：小粒度、高语义密度，负责在 PGVector 和 GIN 倒排索引中做精准向量/关键词匹配；元数据打上所属 `parent_chunk_id`；
  - **Parent Chunk（800 tokens）**：大粒度，覆盖完整 Section 业务章节或跨叶子滑动分组；无结构文档自动构造 Virtual Parent，杜绝孤儿块。
- **检索端回溯与截断保护（`backend/rag/retrieval/retrievers.py`）**：
  - **`attach_parent_context`**：检索命中的 Leaf 提取其 `parent_chunk_id`，批量回溯获取对应的大 Parent 块拼入上下文送给 LLM；
  - **`_truncate_leaves_keep_parents` 截断保护**：Top-K 截断时仅截断 Leaf 数量，Parent 块作为上下文载体**绝不占用 $k$ 的名额**（解决传统实现把回溯出的 Parent 误截断的死穴）；
  - **`attach_referenced_context` 表格协同**：同文档 Leaf 引用的 Markdown 费用表/规格表（`references=[{"table_id": "...", "relation": "refers_to"}]`）自动关联拉取，防止解读条款时缺失数据表。

### 卡片 13：Agentic RAG（Self-Correction 自反思重试与动态改写闭环）
- **核心痛点**：传统 RAG 是单向线性流水线（检索 $\rightarrow$ 失败即死），一旦初次检索没命准就直接幻觉或拒答。
- **反思自救回路（`backend/rag/chain.py::_try_self_correct`）**：
  - **LLM 自报协议（Gate 3）**：Prompt 约束模型在输出头部附带隐藏元数据注释 `<!--META{"can_answer": false, "reason": "no_evidence"}-->`；
  - **区分“真没有”与“没找对”**：模型自报无法回答时，`SelfCorrectionStrategy` 并不直接放弃，而是结合原问题与拒答根因调用 `try_rewrite` 动态生成改写 Query，触发二次自救检索；
  - **变体缓存跨轮复用**：改写 LLM 一次产出多条变体，存入内存缓存 `_cached_rewrites`，跨轮重试依次取出变体重检，不再重复调用 LLM 产生额外开销；
  - **二次生成双重安全门禁**：二次生成的答案依然强制通过程序化 `ClaimVerifier`（正则核验时长、金额、日期、百分比）零容忍核验，绝不允许自纠错成为绕过安全防护的后门；
  - **严格步数熔断（`can_retry()`）**：受 `max_retries`（默认 1~2 次）硬限制，重试均在 OpenTelemetry 中独立记录 `SelfCorrection` Span；重试仍不达标立即收敛至统一拒答路径 `_reject`，彻底杜绝死循环与延迟失控。

### 卡片 14：基于 AST 的四层纵深 SQL 安全与多租户隔离架构（`SQLPolicyGuard`）
- **核心痛点**：外层正则或简单拼接 `WHERE tenant_id = 'xxx'` 在面对 `UNION ALL`、深层子查询（Subquery）或多表 JOIN 时存在致命漏洞（外部 WHERE 根本无法约束子查询与 UNION 分支内部的数据可见性，导致跨租户数据泄漏）。
- **四层纵深防御体系（`backend/sql/policy.py`）**：
  - **Layer 1 权限门（Fail-Closed）**：显式校验 `sql.read` 权限点；`user_id` 缺失直接阻断，杜绝未绑定身份的旁路调用；
  - **Layer 2 6层 AST 硬校验（`sqlglot`）**：根节点必须为只读 `exp.Select`，禁止 DDL/DML，只允许正向白名单函数（拦截 `pg_read_file`, `sleep`, `benchmark`），强制 `LIMIT` 安全上限（默认 100，最大 1000）；
  - **Layer 3 真实物理表解析（`_collect_table_refs`）**：严格解析 AST 中的物理表节点，剔除 `exp.CTE` 别名伪装，对每一个物理表进行角色权限匹配，任一表未授权一票否决；
  - **Layer 4 基于 Select Scope 的三维租户条件原地注入**：遍历 AST 中所有的 `exp.Select` 作用域（含子查询与 UNION 分支），提取直接引用的物理表与别名，读取 `schema_loader.get_table_policy(table)`，调用 `sel.where(cond, copy=False)` 原地在 AST 节点树追加参数化隔离条件并提取 `params` 字典，防范 SQL 二次注入与跨租户逃逸。

### 卡片 15：混合检索底层原理：HNSW 拓扑调优（80→100 实证）与 RRF 量纲隔离
- **核心痛点**：稠密余弦打分 $[0, 1]$ 与稀疏全文打分 $[0, +\infty)$ 机制截然不同，无法直接归一化加权；高维向量空间存在拓扑聚合，图搜索容易收敛于局部次优解。
- **HNSW 参数调优与 80 $\rightarrow$ 100 生产踩坑（`backend/config/rag.py`）**：
  - 建库参数 `m=16, ef_construction=64` 兼顾内存与图连通性；
  - **80 $\rightarrow$ 100 生产实证**：在 5440 块知识库压测中，真实余弦相似度 0.62 的核心发票块（精确排 #6）在 `ef_search=80` 时跌出 top9，导致 dense 3.0 权重名额被次优块挤占；平台将 `VECTOR_HNSW_EF_SEARCH` 调优锁定至 100，并在检索时通过 `SET LOCAL hnsw.ef_search = 100` 事务级会话下发；
- **RRF (Reciprocal Rank Fusion) 倒数排名融合（`backend/rag/retrieval/hybrid.py`）**：
  - 公式：$RRF(d) = \sum \frac{w_m}{60 + rank_m(d)}$；
  - 完全剥离绝对分数值，仅按相对排名融合，天生免疫长文档 BM25 分数飙高压制向量路的量纲漂移；
  - 配合 Cross-Encoder（BGE-Reranker-Large）做二次全注意力交互精排，形成“RRF 广度粗排 + Cross-Encoder 深度判定”双保险。

### 卡片 16：复杂文档结构驱动切分：7 种策略自适应路由、跨页表格合并与覆盖率审计
- **核心痛点**：单一切块策略无法适应企业级多结构混合文档（法律条款、FAQ、操作 SOP、财报表格），易发生条款断头、Q/A 分离或静默丢内容。
- **模块化策略矩阵（`backend/rag/preprocessing/chunkers/`）**：
  - 7 种专业策略：`Structure`（层级章节与 Parent-Child）、`Legal`（条款号严格绑定与标题继承）、`QA`（局部问答对原子化绑定）、`Step`（SOP 操作步骤原子块）、`Financial`（数值表格与维度行块）、`Semantic`（句间余弦断崖突变）、`Recursive`（滑动窗口兜底）；
  - **局部内容单元分派（`ContentUnitDispatchStrategy`）**：按 AST 嵌套层级递归拆解局部单元，各自匹配独立最佳策略并统一命名空间；
  - **跨页表格智能拼接（`_merge_cross_page_tables`）**：自动合并分页 PDF/DOCX 的续页表格数据行，保留完整阅读顺序；
  - **双重覆盖率审计（`audit.py`）**：`cleaning_coverage_audit`（防清洗造假）+ `coverage_audit`（Leaf 覆盖率 $\ge 98\%$ 对账），覆盖缺口触发硬异常阻断发布。

---

## 四、 高频追问“防翻车”话术

1. **Q：你的上下文与记忆是怎么设计的？很多项目长对话要么爆 Token 要么失忆，你怎么解决的？**
   - **答**：“我们平台把上下文和记忆划分为 **L1 到 L5 五层递进治理**：
     - **L1** 在工具执行完后就做截断，大工具输出转成预览对象（`ToolResultRef`），防止单个工具打爆；
     - **L2** 会话层做动态历史裁剪，通过 `PinnedContext` 锁定确认实体和订单号，系统 Prompt 绝不丢失；
     - **L3** 把长期偏好画像持久化进 PG，并通过同事务 Outbox 异步抽取，结合 8 态版本链裁决冲突；技能间则压缩输出；
     - **L4** 在用量超 80% 时做零 LLM 的可逆折叠，滞回压到 65% 安全区；
     - **L5** 在 90% 触发单飞增量摘要，并跑 **40 条关键受保护事实（禁令、日期、金额）确定性补丁**；如果全量压缩还超限，直接 **Fail-closed 拦截**报错，绝不超长硬发。
     这样既保证关键约束不丢，又彻底根治了死循环烧钱的问题。”
2. **Q：RAG 中你们的分块策略是怎么定的？怎么解决长文档切分后的上下文丢失？**
   - **答**：“我们采用了 **Parent-Child 两级分块与回溯机制**：
     - 切块阶段拆分为 **200 tokens 的 Leaf Chunk** 和 **800 tokens 的 Parent Chunk**。Leaf Chunk 负责做高精度的向量与关键词检索；Parent Chunk 覆盖完整章节并挂载 `parent_chunk_id`；
     - 检索召回时，命中 Leaf 后调用 `attach_parent_context` 回溯拉取大 Parent 块喂给大模型；
     - 这里我们踩过一个大坑：如果直接做 `docs[:k]` 截断，会把刚回溯出来的 Parent 块截掉；我们在代码中实现了 `_truncate_leaves_keep_parents`，Parent 块作为上下文载体不占 $k$ 的名额，彻底保证了模型能读到完整的业务条款。”
3. **Q：你们的 RAG 是传统 Pipeline 还是 Agentic RAG？初次检索效果不好怎么处理？**
   - **答**：“我们是具备 **动态反思自纠错（Self-Correction Loop）的 Agentic RAG**：
     - 当 LLM 自报证据不足（`no_evidence`）或置信度低于阈值时，系统不直接向用户抛出拒答，而是调用 `SelfCorrectionStrategy` 动态改写 Query 触发二次检索；
     - 为了杜绝死循环和成本失控，我们设置了 `max_retries=1~2` 硬熔断，并且二次救活的答案依然必须经过程序化 `ClaimVerifier` 逐字核验数字和日期；一旦重试仍失败，优雅降级为统一拒答，P99 延迟始终锁定在安全预算内。”
4. **Q：智能查数中你们是怎么防 SQL 注入和多租户数据越权的？**
   - **答**：“我们绝不用脆弱的正则过滤，而是基于 `sqlglot` 抽象语法树构建了 **四层纵深防护（`SQLPolicyGuard`）**：
     - 第一层权限门，显式校验 `sql.read` 与必须携带 `user_id`，空身份直接 Fail-closed 阻断；
     - 第二层 6 层语法硬校验，只允许只读 `exp.Select` 和白名单函数，拦截危险函数与强加物理 LIMIT；
     - 第三层表白名单，严格剥离 CTE 别名伪装，对全语法树引用的真实物理表逐个校验角色访问权限；
     - 第四层也是最核心的——**基于 Select Scope 的三维租户条件原地注入**：因为简单的外层追加 `WHERE` 根本管不到 `UNION ALL` 和深层子查询，我们遍历语法树中所有 `exp.Select` 作用域，按 Scope 提取表别名，调用 `sel.where(cond, copy=False)` 原地注入参数化的 `tenant_id = :p0` 条件并提取参数字典，彻底杜绝任何跨租户逃逸与二次注入。”
5. **Q：混合检索中你们是怎么解决不同检索器打分量纲不一致的？HNSW 调优踩过什么坑？**
   - **答**：“我们采用了 **RRF 倒数排名融合算法结合 Cross-Encoder 精排**：
     - 针对 PGVector 余弦分和 PG 全文检索分无法直接相加的问题，RRF 完全抛弃绝对分数，依据 $1/(60+rank)$ 相对位次融合，平缓了首位支配效应，免疫长文档词频堆叠引起的打分量纲漂移；
     - 在 HNSW 上我们踩过一个很深的坑：在 5440 块库做压测时，发票目标块排精确余弦 #6，但在 `ef_search=80` 时被局部流形死胡同挤出了 Top-9，导致 dense 3.0 权重名额被次优块抢占后重排拒答。后来我们将 `VECTOR_HNSW_EF_SEARCH` 调优到 100 并加了守卫断言，每次查询前通过 `SET LOCAL hnsw.ef_search = 100` 事务级下发，彻底解决了漏召回问题。”
6. **Q：为什么把原本的选品漏斗下线了？**
   - **答**：“早期确实做过选品漏斗和竞品监控的探索，但随着平台往生产级演进，我发现业务战线拉得太长会稀释稳定性。后来我做了架构收敛（#484），把选品漏斗下线，集中精力把 **RAG（GIN 倒排+发布 CAS）、NL2SQL（AST 参数化安全+延迟优化）、智能客服（派单状态机）和旅游 V2（乐观锁+中断恢复）** 这四块真正打穿到生产可用。”
7. **Q：这个项目里你用 AI 辅助写了多少？遇到过什么最难调的坑？**
   - **答**：“我把 AI 当成高频编码搭档，重复性的前端页面、CRUD 和测试模板让 AI 生成提效；但**架构边界、状态机和并发锁序必须自己把控**。比如之前踩过两个很深的坑：一个是单机 BM25 pickle 在 4C8G 容器里并发发布时抢锁甚至 OOM，最后下决心重构成 PG 原生 `tsvector + GIN` 行级增量索引；另一个是思考型模型做 NL2SQL 路由和生成时动辄十几秒，排查 Trace 后发现是模型默认开启了隐式 thinking，显式关闭后直接压到了 2–3 秒。”
8. **Q：如果记不清某个具体文件里的参数名怎么办？**
   - **答**：“具体配置常量我都收口在 `backend/config/` 和管理端 `sys_config` 热配置表里了，手头一时记不清精确的字段名，但它的**机制和边界**是……（直接切到上面卡片的架构原理）。”
