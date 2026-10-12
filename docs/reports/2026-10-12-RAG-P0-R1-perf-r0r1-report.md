# RAG 检索性能专项优化验收报告（R0 + R1）

> 任务：RAG 检索性能专项优化——R0 基线与指标、R1-A 请求级 Embedding 复用、R1-B 简单问句误触发 MultiQuery 修复。
> 基线：GitHub 最新 main `d1b9668`（PR #33 / #36 已合并）；分支 `task/rag-perf-r0r1-20261012`。
> 结论：**R0 PASS / R1-A PASS / R1-B PASS**（检索质量与安全门禁见 §7/§8）。R2/R3 本轮只交付设计（§9）。

---

## 1. Git 基线与隔离

| 项 | 值 |
| --- | --- |
| GitHub main（实测 API） | `d1b96688bf32f6f593cfd934d3a0efbeef10ce55` |
| PR #33（rag 审核可见性 + 答案缓存收口） | merged，`ca91b3918e3d408d17a348415c194d9b999b259c` |
| PR #36（rag.search 15s→45s 超时缓解） | merged，`1fe16e126be930e06a7159feb526f87a6f2413f2` |
| 任务工作树 | `.worktrees/rag-perf-r0r1-20261012` |
| 分支 | `task/rag-perf-r0r1-20261012` |
| 基线提交 | `d1b9668`（与 origin/main 同 SHA） |
| 主仓工作区 | `D:\Program Files\workplace\agent`（原 main `631f153`，未改动） |

最新 main 的真实默认状态（**不是**旧工作区推断）：`rag.search` 超时 **45s**、
`_ANSWER_CACHE_HARD_DISABLED=True`（答案缓存硬关闭）、`RAG_RETRIEVAL_DEADLINE_MS=8000`、
`RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS=1800`、`RAG_MULTI_QUERY_FANOUT_TIMEOUT_MS=4500`。

## 2. 修复前的真实调用链

以用户提供的 Trace 为准（`8b8b67f75bf1` 父 / `38cb6ec125b7` 子），本次施工前的端到端构成：

```
chat 请求 17.2s
├─ input_guard                 1ms
├─ session_load / memory       1.40s   （其中 memory.embedding_query 远程向量化 1.05s，候选 0）
├─ router                      0.57s   （缓存 miss；routing.domain 远程向量化 0.30s）
├─ skill_executor → rag.search 14.70s  ← 85.6%
│   ├─ 父工具→rag-service 前置  ~4.0s  （审计 §3.1 实测，原因未证实，见 §6.2）
│   └─ RAG 内部 11.2s（子 trace 实测）
│       ├─ Chunk 检索          3.54s   （Doc 级 1.44s + 增强混合检索 2.08s）
│       ├─ Rerank              0.43s
│       └─ LLM 生成            6.45s   （输出仅 104 字）
└─ reporter                    5ms
```

## 3. Embedding 重复调用的精确位置与次数

**位置（容器内真实链路栈取证，`--trace-embed`）**：

| # | 调用栈（帧序） | 性质 |
| --- | --- | --- |
| 1 | `retrievers.py:551 _stage1_select_docs → :494 _authorized_doc_search → pgvector_store.py:601 similarity_search_with_score → embed_query` | **Doc 级**文档相似度检索，串行，1 次 |
| 2 | `enhanced_hybrid_retrieval.py:17 _timed_retrieval → base.py:95 retrieve → pgvector_store.py:601` | **Chunk 级 hybrid 向量腿**，在检索线程池内，1 次 |
| 3 | 同上（第二路） | 并行分支 1 次 |
| 4 | 同上（第三路） | 并行分支 1 次 |

即：**同一条 query 在一次问答里被独立向量化 4 次**（Doc 级 1 次 + 并行 3 次），
三者输入文本完全相同；MultiQuery 触发时每个变体再各自 +1。审计报告 P2-1
的「6 次」与本次实测的 4~5 次同源（都来自 Doc/Chunk/Hybrid/变体路径各自重嵌）。

**次数（真实 provider，容器内直连真实 KB 与云端 embedding）**：

| 场景 | provider query 向量化次数 | 每次远程耗时 |
| --- | --- | --- |
| 修改前（基线 `d1b9668`）3 次问答 | 15 | 0.75~1.55s（`duration_ms` 实测） |
| 修改后（本分支）3 次问答 | **4** | 首次 0.78~1.45s，后续复用 0 |

## 4. 修改文件与设计理由

| 文件 | 改动 | 设计理由 |
| --- | --- | --- |
| `backend/rag/embedding_singleton.py` | 新增请求级 embedding 缓存、进程级有界 LRU、单 key singleflight、自愈作用域、失败不入缓存 | 唯一共用 embedding 出口，所有路径（Doc/Chunk/Hybrid/变体）都经过 `_TrackedEmbedding.embed_query`；在此收口不触碰任何检索算法语义 |
| `backend/observability/metrics.py` | 新增 6 个低基数 embedding 指标；`record_rag_stage` 增加 `embedding` 阶段 | 复用既有 Prometheus 与 stage 枚举，不新建指标体系；标签仅 kind/provider/hit_source |
| `backend/rag/context.py` | `RagRequestState.embedding_stats` | 与 meta/faithfulness 同口径挂在请求上下文，避免单例字段并发串扰 |
| `backend/rag/chain.py` | `_execute` 开/关 embedding 作用域；新增 `_emit_embedding_stats`（ctx + Trace event + stage 指标 + 日志） | 检索入口是唯一能覆盖全部取向量路径的位置；Trace 事件保证逐请求可追溯 |
| `backend/rag/retrieval/hybrid.py` | 新增单一事实来源 `is_simple_fact_query`、`SIMPLE_FACT_PATTERNS`、`SIMPLE_PREFIXES`；简单事实只降级「强信号」 | 只降级强信号、只对单子句生效 → `门票多少钱` 仍 vector_only，Tier-2 标识符/专名语义不变 |
| `backend/rag/retrieval/multi_query.py` | 兜底 `_is_complex` 改为引用上述单一来源；简单事实豁免同时对强信号与业务关键词生效；兼容再导出旧常量 | 修复「分类器降级后两套顺序矛盾」这一根因，不新增分类调用 |
| `backend/tests/rag/test_embedding_request_cache.py`（新增） | 9 例：请求内去重、变体不共用、进程级复用、模型隔离、并发单飞、失败不入缓存、键不含明文、有界、documents 行为不变 | 覆盖任务书 §四 R1-A 全部验收项 |
| `backend/tests/rag/test_multi_query_simple_fact_routing.py`（新增） | 20 例：简单事实/复杂多约束/分类器降级三类 | 覆盖任务书 §五 R1-B 验收项 |
| `artifacts/rag-perf-r0r1/*` | 真实链路测量与对照脚本（`measure_ask.py`、`check_retrieval_parity.py`） | 让"修改前后真实数据"可复现，而不是只报缓存命中率 |

**缓存语义边界（关键设计约束）**：

- Embedding 缓存键 = `provider | model | 维度 | 配置签名` + `sha256(实际输入文本)`；
  **不含** k / filter / KB / 租户 —— 这些属于检索结果缓存，继续由
  `RagRequestState.retrieval_cache`（键含 query/filter/k）承担，两套缓存不互相代替。
- 文本按原样参与哈希：不同变体、不同前缀规则永不共用向量；换模型/维度/
  凭据版本即换命名空间，与索引侧 `INDEX_EMBEDDING_MISMATCH` 同口径。
- 请求级作用域在 retrieval 线程池内**自愈**（contextvars 不跨线程继承），
  且自建者负责 flush + 上报，避免变体计数随线程静默丢失。
- 失败不入缓存；进程级缓存只存向量与哈希，不保留 query 明文；LRU 上限默认 256。

## 5. 修改前后阶段耗时对比

## 5.1 本次可对照的真实数据

| 指标 | 修改前（基线 `d1b9668`） | 修改后（本分支） | 数据来源 |
| --- | --- | --- | --- |
| provider query 向量化 / 3 次问答 | 15 | **4** | 容器内计数包装（`provider_embed_calls`） |
| 单次问答 query 向量化 | 5 | **1.33** | 同上 |
| 单次问答 embedding 累计耗时 | ≈2.5~7.5s（5 次 × 0.75~1.55s，串行部分） | ≈0.8~1.5s（首嵌）+ 0（复用） | `rag_embedding_duration_seconds` / `duration_ms` |
| 端到端 p50（3 次样本） | 23.34s | 21.82s | `elapsed_ms` |
| 端到端均值（3 次样本） | 22.36s | 20.64s | `elapsed_ms` |

样本量仅 3 次且生成模型耗时波动大（同一问句 7.6s~25.1s，与审计 §5.1「11s~48s」
一致），**因此不承诺端到端秒数改善**；可确定且可复算的收益是 provider 向量化次数
与 embedding 耗时。

## 5.2 修改前基线（历史 Trace，作为 R0 基线口径）

| 指标 | 实测 | 来源 |
| --- | --- | --- |
| 端到端 | 17.17s | `trace-d2500db8efe6.json`（用户提供） |
| rag.search | 14.70s | 同上 |
| RAG 子 trace P90 / P95 | 16.3s / 19.1s（627 条） | 全链路审计报告 §3.1 |
| rag.search 超时线 | 45s（缓解）；父图路径满预算 ≈19.75s | `backend/core/tool_runtime/policy.py` |

## 6. 性能指标与数据来源

## 6.1 本轮新增/补齐的观测（R0）

| 要求 | 落地 | 说明 |
| --- | --- | --- |
| 阶段耗时 `rag_stage_duration_seconds{stage}` | 复用既有 Histogram，新增 `embedding` 阶段 | `record_rag_stage("embedding", 累计出站耗时)` |
| 每请求 `query_embed_count` | `rag_embedding_requested_per_scope` + `EmbeddingRequestStats`（requested/computed/request_hits/process_hits/unique_inputs/dedup_ratio/duration_ms） | 主请求 + 线程池自建作用域分别观测；Trace 增加 `embedding_stats` 事件 |
| 真实出站次数与复用 | `rag_embedding_calls_total{kind,provider}`、`rag_embedding_cache_total{hit_source}`、`rag_embedding_duration_seconds{kind}` | 与 provider 调用一致（容器实测 4 = 4） |
| Answer Cache hit/miss | 复用既有 answer cache 指标位（本轮未改缓存行为，硬关闭状态保持） | 设计见 §9 R2-B |
| rag.search p50/p95 区分父/子 | 复用既有 trace + Tool Runtime 指标；子 trace 由 `child_trace_ids` 关联 | 本轮未新增平行超时/指标体系 |

标签基数控制：全部标签为固定低基数枚举（`kind`/`provider`/`hit_source`/`result`/`stage`），
query 原文、request_id、tenant 一律只进 Trace。

## 6.2 前置 ~4s 缺口（诚实结论）

本轮**未能证实**父工具体与 rag-service 接客之间的 ~4s 构成。已具备的关联能力：
父 span 的 `child_trace_ids`、子 trace `parent_id`、两端的 `start_time/end_time`。
现状：该区间无任何服务端埋点（HTTP 连接、容器网络、rag-service 接收时刻未落点），
需要既有 Runtime/网关补一个接收时刻埋点，属跨任务面（见 §9 R3-B），本轮未改。

## 7. 检索准确率与引用回归

- **向量等价（决定检索质量的关键）**：缓存路径 vs 直连 provider，float32 位模式比较。
  实测 4 个文本中 2 个位模式完全一致（`max_abs_diff = 0.0`），另 2 个差异
  `≤ 1.96e-4`——这是云端 embedding 服务端批处理固有的非确定性，不是缓存引入的
  偏差（缓存返回的就是首次出站算出的那个向量）。检索名次未受影响（见下）。
- **召回等价**：优化版向量库 Top-8 实测
  `faq_常见问题FAQ.md#e07b25dc7b_2 (0.364469)` →
  `sop_客户退款处理流程.md#bfcdf7850f_3 (0.442922)` → … → `p4_kb_seed_doc.md#71a1ad2d5f_10 (0.609691)`，
  与修改前 Trace `38cb6ec125b7` 的召回一致（同 FAQ 文档、相关度 0.97 档、同一引用
  `doc:e07b25dc7b`）。基线版 parity 记录已由 §5 的 A/B 与历史 Trace 佐证。
- **routing 语义**：`test_query_tier_fix.py`（67 例含历史 Tier 语义）、
  `test_multi_query_*.py`、`test_classify.py` 全绿；简单事实只降级强信号，
  Tier-2 标识符/专名与 Tier-1 短问行为不变。
- **引用/拒答**：生成与 Evidence Gate 未改动，答案链路等价（同一 prompt 版本）。

## 8. 安全、权限、计费验收

| 项 | 结论 | 依据 |
| --- | --- | --- |
| 租户/KB/权限隔离 | 无串扰 | embedding 缓存键仅含模型身份+文本，不含任何主体维度；检索结果缓存仍按 query/filter/k 隔离 |
| 跨模型复用 | 禁止 | 键含 provider/model/维度/配置签名；测试 `test_model_identity_change_never_shares_vectors` |
| 文本变体串用 | 禁止 | 原样哈希；测试 `test_distinct_query_variants_keep_own_vectors` |
| query 明文留存 | 不保留 | 键为 sha256；测试 `test_request_cache_holds_hash_not_raw_query` |
| 失败重试 | 不受影响 | 异常不入缓存；测试 `test_failure_is_not_cached` |
| 计费 | 无重复计费 | 只有真实出站的调用写 `llm_usage`（`_record` 在 compute 内），命中复用不记账也不重复计费；用量行数量随出站次数下降 |
| 提示词/模型治理 | 未绕过 | 未新增 LLM 调用；仍走 `prompt_service` 与 `get_llm_for_role` |

## 9. R2/R3 最小施工方案（仅设计，不写生产逻辑）

## 9.1 R2-A 生成模型优化（设计）

- 现状：`rag.qa` 走主链 `llm` 单例，实测单次生成 6.45s / 104 字，波动 7.6~25.1s。
- 实验矩阵：当前 reasoning 模型 / 同 Provider non-thinking / 既有轻量 non-thinking
  （`multi_query` 角色已用此法，`backend/config/model_roles.py`）。
- 必测：简单事实、多文档整合、数字/时间/政策条件、无答案拒答、引用准确性、
  证据冲突、长上下文边界；对比准确率、引用、Token、TTFT、总时长、成本。
- 门禁：准确率/引用不得退化；否则回滚角色绑定（单值，无需发版）。
- 依赖：统一 LLMProxy + Prompt 版本 + 计费（已具备，不新建）。

## 9.2 R2-B 答案缓存安全恢复（设计）

- 先查明硬关闭原因：`backend/rag/answer_cache.py` 注释 + PR #33 + 审计 §10.2
  （审核状态跨进程失效窗口：版本号本地缓存 10s、`invalidate_kb` 异常被吞、
  自然到期不触发失效、TTL 1h）。
- 恢复前提：把「TTL 失效」换成「证据版本/授权复核」——缓存值携带 doc 版本指纹
  与授权范围，命中时按当前 registry/权限重校验；证据已删/越权/版本变化即 miss。
- 必测：内容更新、删除、版本变化、权限变更、tenant/KB/department 隔离、
  prompt/模型变化、证据失效、拒答与失败是否允许缓存。
- 风险：错答复用优先级高于性能收益；未通过复核实测前保持硬关闭。

## 9.3 R2-C 高频 FAQ 快捷路径（设计）

必须**同时**满足：人工维护的 FAQ 标准答案或明确 FAQ 结构、完整匹配当前意图、
证据有效且未过期、权限校验通过、无跨文档冲突、无需推理/计算/多条件整合。
禁止仅凭 `rerank top1 > 阈值` 放行。保留引用、证据校验与审计记录。

## 9.4 R3-A Remote RAG Streaming（设计）

- 目标：rag-service → app → SSE 的真实增量传输。
- 必须设计草稿态与终态：未过 Evidence Gate 的候选正文只能以明确的「草稿/校验中」
  状态展示，终态以既有 `done.answer` 为准；不得把无法撤回的错误内容当作已验证答案。
- 断连/超时/异常要有终止状态；与现有前端契约兼容。

## 9.5 R3-B 超时与取消传播（设计）

与第四阶段 Runtime 治理统一设计，**不新建** RunControl/预算系统。需要区分：
请求逻辑取消、后端任务中止、Provider 可取消能力、不可终止的同步调用、
迟到结果丢弃、已消耗 Token 的真实计费。前置 ~4s 缺口的接收时刻埋点在此设计内补齐。

## 9.6 R3-C 双阶段检索优化（设计）

在 embedding 去重与路由修复完成后，再测量 Doc 级筛选是否仍有浪费。
**不得**为减少一次检索而删除 Doc 级筛选（会降低候选召回，见 §3 的位置 1）。

## 9.7 R3-D 路由与记忆前置开销（设计）

分别测量 routing.domain 的缓存 miss 与 Memory 零候选场景。
不能因为单次候选为 0 就断言所有 Memory 检索都应跳过（不同模型/索引/业务过滤
导致的零候选属正常工作）。可共享的只有「同模型同文本的向量」，已在 R1-A 覆盖。

## 10. 遗留问题与回滚

- 遗留：前置 ~4s 缺口未证实（§6.2）；端到端样本量小（§5.1）；
  `_last_embedding_stats` 只反映主线程作用域（权威口径是 Prometheus 与 Trace 事件）。
- 回滚：R1-A 单点回滚 = 把 `_cached_embed` 的复用短路（或设
  `RAG_EMBED_PROCESS_CACHE_ENABLED=false` 关闭跨请求复用）；R1-B 单点回滚 =
  还原 `_classify_query_tier` / `_is_complex` 的判定顺序。两处均无数据迁移。
- 未执行验证不得报告为通过：本轮未做生成模型替换实验、未恢复答案缓存、
  未改 SSE 流式，均按要求留待批准。

## 11. Commit / PR

- 分支：`task/rag-perf-r0r1-20261012`（PR **#41**，https://github.com/Jayus-07/agent/pull/41）
- 基线：`d1b9668`（= GitHub 最新 main）
- 最终 Commit：**`0caf5c0`**（单一提交，含 R0/R1 实现 + P0 凭据整改 + P1-A/B/C/D + 报告/任务文档/脱敏证据）
- 历史状态：原 `1e948a7`/21216bc 两个提交含明文 Redis 连接串日志，已整体重写替换（仅本分支）
- CI：`Detect RAG changes` = success；`scan`（新增敏感扫描门禁）= **failure（有意为之）**，
  命中 `origin/main` 既有 3 个含真实 API_KEY 的 travel 脚本；`RAG Smoke` 以 PR checks 为准。
- 未合并 main。

## 12. 结论

| 项 | 结论 |
| --- | --- |
| R0 | **PASS** — 五类指标可从真实请求读取，embedding 计数与 provider 调用一致，标签低基数，无敏感明文 |
| R1-A | **PASS** — 单次问答 provider 向量化 5 → 1.33（3 次样本 15 → 4），失败不入缓存、模型隔离、并发单飞、有界、无明文 |
| R1-B | **PASS** — 简单事实问句不再触发 LLM 改写，复杂/多子句仍触发，主分类器与兜底同源同序 |
| R2 / R3 | 未实施（按要求仅设计） |
---

# 附录 A：PR #41 审查整改（2026-10-12 二次验收）

> 审查意见：https://github.com/Jayus-07/agent/pull/41#issuecomment-6119261498
> 结论：**P0 部分 PASS / 部分 BLOCKED（凭据轮换需用户执行）；P1-A PASS；P1-B PASS；P1-C PASS；P1-D PASS（结论已修正）**。

## A1. P0 凭据泄漏

**已确认的泄漏面（不是"PR 未合并就不算泄漏"）**：

| 位置 | 泄漏内容 | 处置 |
| --- | --- | --- |
| 本分支 7 个日志文件（`ab_*.log`、`parity_optimized.log`、`raw/embed_trace*.log`、`raw/verify_stats.log`） | 容器日志里的 Redis 连接串（含明文口令） | 删除文件 + **重写分支历史**（仅本任务分支，见 A1.3） |
| 本分支 `rag-service.env.example` | 模板里误留真实 PGPASSWORD 值 | 改为占位符 |
| **origin/main `scripts/travel_concurrency_evidence_check.py` 等 3 个脚本** | **真实 API_KEY 明文（既有泄漏，非本 PR 引入）** | 本轮不改（Zcode 正在动 Travel 路径）；已登记为独立风险 |
| 本地工作树 `artifacts/rag-perf-r0r1/rag-service.env`（从未提交，已 gitignore） | 完整容器环境（DB/JWT/API Key） | 保留用于复现；**不入库** |

**根因（代码级）**：`backend/infra/redis/client.py` 直接打印完整连接串 ——
`logger.info(f"[Redis] connected: {REDIS_URL}")` 与 AuthRedis 同款。已修复为
`safe_url_for_log()`（去掉 userinfo 与 query），并同步修掉 AuthRedis 行；
日志实测输出 `redis://***@localhost:6379/0`。

**自动化门禁**：新增 `scripts/scan_secrets.py`（零依赖）+
`.github/workflows/secret-scan.yml`（PR 区间历史扫描 + main 全量）。
命中只打印 `文件:行号 + 类型`，不回显内容。

**仍存在但已登记的风险（不得声称已彻底清除）**：
1. `origin/main` 的 3 个 travel 脚本含真实 API_KEY（提交 `96539b7` 引入，早于本 PR）；
2. 仓库本地存在 `backup/*` 分支带着历史残留（非 fetch ref，不在远端）；
3. 本分支改写历史后，**已经被 GitHub 缓存/被 fork/被 clone 的旧 commit 仍可能可检索** ——
   彻底消除依赖凭据轮换 + GitHub 侧支持。因此凭据轮换状态为 **BLOCKED**：

| 凭据 | 状态 | 需要用户执行 |
| --- | --- | --- |
| Redis 口令（`REDIS_PASSWORD`，40 位） | **BLOCKED：未轮换** | 换新口令 → 同步 `.env`/compose/APISIX → 重启 Redis 与依赖服务 → 确认旧口令失效 |
| `API_KEY`（`ak_…`，47 位，**已在 main**） | **BLOCKED：未轮换** | 该 key 建议直接吊销并重建；同时清理 3 个脚本里的硬编码 |
| JWT_SECRET / SMTP_PASSWORD / PGPASSWORD | 未见提交记录（仅本地 env） | 建议顺带轮换 SMTP 与 DB 口令；JWT_SECRET 轮换会强制全端重登 |

## A1.3 历史清理（分支限定）

- 只重写 `task/rag-perf-r0r1-20261012`；main 与其他分支、其他工作树未触碰。
- 重写方式：`git reset --soft <base>` + 单一干净提交（原来的两个提交都带泄漏日志）。
- 强制推送前核对：分支为本人任务分支、远端同名 ref 由本 PR 持有、无他人提交。
- 重写后 `scripts/scan_secrets.py --history` 必须 0 命中（见 A6 证据）。

## A2. P1-A 失败计费与审计（PASS）

- `_cached_embed` 的异常分支恢复 `_record(..., status="error")`：token 恒为 0（不虚构消费），
  `finish_reason="error"`，仍走既有 Billing V2 事件结构。
- 失败同时计入 `computed`（真实出站次数）并新增
  `rag_embedding_call_errors_total{kind,status}`；缓存命中与进程级命中不产生新费用行。
- 计量粒度已在代码注释与报告写明：账本按**外层一次调用**记，inner SDK 自带 1 次重试，
  真实 HTTP 尝试数可能更多且拿不到逐次 usage —— 不把外层一次调用等同于所有底层尝试。

## A3. P1-B 请求级统计（PASS）

- 改为**请求总账**（`_RequestEmbeddingRecord`）：入口建账，随 `copy_context` 进检索线程池，
  池线程只贡献自己的 scope，父请求 `end_embedding_request()` 汇总；
  嵌套请求不重复统计（总账只在最外层创建）。
- 明确区分 `request_hits`（请求内去重）与 `process_hits`（进程级缓存命中），并提供
  `hit_source` 标记；`unique_inputs`、`duration_ms` 一并给出。
- 与调用栈取证对账：真实链路 `provider query=4`，请求总账 `computed` 与之一致（见 A6）。

## A4. P1-C singleflight（PASS）

- **删除淘汰式 key 锁表**（旧实现达到 64 条即淘汰"最久未用"，可能淘汰仍在使用/等待的锁，
  导致同 key 建第二把锁并重复出站）。
- 改为**固定条纹锁**（`_EMBED_LOCK_STRIPES`，默认 64）：同一 key 恒映射同一锁，
  锁永不淘汰、数量恒定；不同 key 仍并发（仅哈希碰撞串行）。
- 新增在途登记：持锁者失败时在 `finally` 解除，等待者发现缓存仍无值会自行重试
  （失败即解除，不因他人失败永久卡住）。
- 验收覆盖：80 个不同 key 并发（每 key 恰好出站一次）、>65 key 的锁切换压力、
  进程缓存开关两态、Provider 失败与超时、32 个不同 key 的并行性（不被全局大锁串行化）。

## A5. P1-D MultiQuery（PASS，原结论已修正）

**修正原报告的错误结论**：基线 `d1b9668` 的兜底 `_is_complex` 本身就把简单事实豁免放在
业务关键词之前，`_classify_query_tier` 也没有业务关键词分支 —— 「退款审核时间是多少？」
在基线**不会**误触发 MQ。原测试场景不成立，已在报告与测试中如实修正。

**真实缺陷**（`artifacts/rag-perf-r0r1/probe_r1b_delta.py` 逐条取证）：旧兜底自持一套
业务关键词规则，与主分类器真实分叉，例如「差评怎么处理」「审核时间是多久」
兜底判复杂、主分类器判 hybrid。修复方式：兜底不再自持规则，改为复用唯一判定入口；
主入口抛错时**保守不改写**（保双路召回，只跳过改写），不再出现两套结论。

同时删除了不可达分支：`is_simple_fact_query` 在强信号命中时必然返回空串，
因此"强信号 + 简单事实 → 降级"的判定从未生效，已从两处判定链移除并加护栏测试。
强信号（对比/分别/各自…）恒为 MQ。

## A6. 二次验收证据

| 项 | 结果 |
| --- | --- |
| 定向回归（20 个文件） | **282 passed** |
| 敏感信息扫描（`scan_secrets.py --tree`） | 本分支无凭据 URL 命中；`artifacts/` 已无日志（见 A1） |
| Redis 日志脱敏 | 实测输出 `redis://***@localhost:6379/0` |
| 失败计费 | 新增用例断言失败写 `finish_reason=error`、token=0、`computed` 计入 |
| 并发/故障 | 新增 80-key 并发、锁条纹、两态缓存、失败恢复用例 |
| 真实链路 | provider 向量化 3 次问答 15 → 4；请求总账 requested=1/computed=1/kinds={'query':1} 与真实出站一致（`artifacts/rag-perf-r0r1/evidence/`） |
| RAG Smoke | 见 PR checks |
