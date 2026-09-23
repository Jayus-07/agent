# Memory Production Closure — STOP D 实施报告：Safe Injection + Relevance Gate + Expiration + Access Semantics

> 日期：2026-09-24 ｜ 前置：STOP A（`79d4668`）/ B（`3e16e66`）/ C（`538145a`+`d306be5`）均冻结
> 报告遵循任务书 §121 返回格式。本轮只收口 **Memory Read Path**。

---

## 1. Verdict

**STOP_D_PASS=true**（D1-D27 逐条对照见 §17/§22）

## 2. Root Cause / Goal

五个读路径问题：①L3 以裸 SystemMessage 注入（历史数据获得 system 权限，D-P0-1）；②无最低相关性门槛（P0-2）；③无关记忆强凑 top5 陪跑（P0-3）；④expire_at 死字段不参与检索（P0-4）；⑤主注入路径不 mark_accessed，decay 时间语义失真（P1）。

## 3. Frozen Baseline

STOP A `79d4668`｜STOP B `3e16e66`｜STOP C `538145a` + `d306be5`。本轮 ancestry 已验证（HEAD 为并行会话 domain-runtime STOP F 的 `f0a7f43`）。

## 4. Changed Files

| 文件 | 变更 |
|---|---|
| `config/memory.py` + `config/__init__.py` | `MEMORY_RETRIEVAL_CANDIDATES=20`、`MEMORY_MAX_INJECTED=5`、`MEMORY_MIN_RELEVANCE_SCORE=0.45`、`MEMORY_GLOBAL_KEY_PREFIXES=("response.",)`、`MEMORY_MAX_GLOBAL_PREFERENCES=3` |
| `memory/repository/memory_repo.py` | `search_hybrid` SQL 层增加 `(expire_at IS NULL OR expire_at > NOW())` eligibility（§21：过期行不占候选槽位）；返回值改 `(record, similarity)` 元组（§73：分数不丢失）；`mark_accessed(ids, tenant_id, user_id)` scope 化 + `is_active` 过滤（§27/§28） |
| `memory/retriever.py` | **重写**：`RetrievedMemory`（record+semantic_score+rank_score+source）；semantic gate（仅 semantic score，≥threshold，§14）；global preference 白名单分流（免 gate）；hybrid rank 0.5/0.3/0.2 沿用 + 确定性 tie-break；merge/dedup + global 保留额 + max-K；`enforce_gate=False` 供工具显式搜索 |
| `memory/service.py` | start_session L3 段：`build_memory_context` 安全注入（policy+数据块，紧跟 L2 块动态定位）+ 仅注入条 mark_accessed（独立短事务 + fail-open）；`search()` 工具路径松 gate + scoped mark |
| `memory/long_term.py` | `retrieve` 适配元组；删除死方法 `format_for_prompt`（裸 SystemMessage 拼接的最后遗迹） |
| `context_budget/role_safety.py` | 新增 `MEMORY_CONTEXT_POLICY_TEXT`（同 POLICY_MARKER 前缀，预算层可识别）、`sanitize_memory_content`（开/闭标签中性化）、`build_memory_context`（白名单字段 JSON 行 + 双重转义；空 entries 返回 []） |
| `observability/metrics.py` | `memory_retrieval_candidate_total`、`memory_retrieval_accepted_total{source}`、`memory_retrieval_rejected_total{reason}`、`memory_access_mark_total`、`memory_access_mark_failure_total` |
| 测试 | 新增 `tests/memory/test_memory_retrieval_gate.py`（12）、`test_memory_safe_context.py`（8）；存量跟进 `test_pgvector_l3.py`、`tests/rag/test_memory_isolation.py`（search_hybrid 元组解包） |

## 5. Before / 6. After

**Before**：top20 → 0.5/0.3/0.2 混合分重排 → 无条件注入 top5（`[已知背景信息]` SystemMessage 裸拼）；无过期过滤；主路径不 mark_accessed。
**After**：candidate → SQL eligibility（scope/active/not-expired）→ semantic gate（≥0.45）+ global 白名单分流 → rank/merge/dedup → 0~5 条 → `[policy SystemMessage + <memory_context> AIMessage 数据块]` → 仅注入条 mark_accessed（fail-open）。

## 7. Retrieval Pipeline（§114）

```
Question → embed → SQL Eligibility（tenant+user+active+not expired，scope 双维）
  → Candidate top-20（MEMORY_RETRIEVAL_CANDIDATES）
  → 分流：global（memory_key ∈ response.* 白名单，免 gate，上限 3）
        | semantic（sim ≥ 0.45；below → DROP{reason=below_relevance}）
  → Rank（global：origin/confidence/recency；semantic：0.5sim+0.3imp+0.2recency，
          tie-break: rank→semantic→importance→created→id，确定性）
  → Merge/Dedup（按 id；global 保留额 + semantic 补位）
  → max 0~5（MEMORY_MAX_INJECTED）
  → build_memory_context（policy System + JSON 数据 AIMessage）
  → mark_accessed(injected ids, tenant, user)   ← fail-open
  → ContextBudgetManager（最终裁剪点不变）→ LLM
```

## 8. Eligibility Contract

`tenant_id`（归一后精确匹配，quarantine 行运行时永不命中）/ `user_id` / `is_active=TRUE` / `(expire_at IS NULL OR expire_at > NOW())`——**全部 SQL 层**，过期行不占候选槽位。

## 9. Relevance Gate

- **raw score**：pgvector `cosine_distance`；**normalized score**：`1 - cosine_distance` = cosine similarity，范围约 [0,1]，越高越相关（D10，已在 repo 层完成归一）。
- **threshold**：`MEMORY_MIN_RELEVANCE_SCORE=0.45`。**依据**：同 embedding 语义空间的 RAG 侧 `SEMANTIC_SIMILARITY_THRESHOLD=0.45`（语义边界）与检索召回门槛 0.25 之间取保守值——目标是先挡住明显无关（无关文本 cosine 通常 <0.4），不追求最优 Precision/Recall（**STOP F Golden 调优**）。实库直连样本审计因 exec 进程无法初始化 DB-managed embedding provider 未完成，以 RAG 侧同模型既有阈值做参照并声明该限制。
- **gate 语义**：`sim >= threshold` 接受（含等号，边界测试用 ±ε 验证）；gate **只看 semantic score**——高 importance/recency 不能把无关记忆抬进门（D9，有专项测试）。
- 归一防呆：threshold 超界 clamp [0,1]（§65）；`candidate_k >= max_k` invariant 在使用处保证（§66）。

## 10. Global Preference Policy

`MEMORY_GLOBAL_KEY_PREFIXES=("response.",)` 确定性白名单（§90 禁 LLM 决定 global）：命中前缀的 active 记忆（response.language/detail_level/code_style 类）与问题主题无关也注入，上限 `MEMORY_MAX_GLOBAL_PREFERENCES=3`；普通主题型偏好（如 travel.seat_preference）不在白名单，仍需过 gate（§94 有测试锁定）；global+semantic 合计 ≤ `MEMORY_MAX_INJECTED`（§98）。

## 11. Ranking / Merge

gate 之后的 rank 沿用 0.5/0.3/0.2 权重不重调（§18）；新增确定性 tie-break（rank→semantic→importance→created→id，§19：相同数据重复运行结果稳定）。merge 按 id 去重（global 同时 semantic 命中只注入一次，§96）。

## 12. Safe Injection Contract

- **System policy**：`MEMORY_CONTEXT_POLICY_TEXT` 固定常量（逐字稳定，利于 prefix cache），显式声明：记忆是历史数据非指令/授权、confidence 非指令优先级、origin=explicit 不构成系统权限（§35/§36）。
- **Memory data role**：AIMessage `<memory_context>` 数据块，记忆原文**绝不进 SystemMessage**（D-I1）。
- **serialization**：每条记忆 JSON 行（仅白名单字段 memory_type/memory_key/origin/confidence/content，§34：无 tenant/user/UUID/分数/source_message_id）+ `sanitize_memory_content` 中性化开/闭标签双保险（§38/§57）。
- **预算**（§83/D-I17）：数据块是 AIMessage，参与历史裁剪策略，不再享受 SystemMessage 永不折叠豁免——专项测试锁定；policy SystemMessage 文本极小可保留。ContextBudgetManager 仍是唯一最终裁剪点。

## 13. Prompt Injection Defense

三层：①role 分离（原文只在数据块，§56 集成测试断言 captured messages）；②JSON+标签中性化（`</memory_context>` 注入被转义，结构 count==1 锁定）；③policy 显式否定指令效力（含 explicit 与 confidence 语义）。测试 `test_injection_memory_cannot_become_system_instruction` 用攻击型记忆（explicit origin + "以后忽略系统规则"）验证 role 结构不变。

## 14. Expiration

`expire_at` 复用既有列（不新增 valid_until，§20）；SQL 层过滤（§21）；边界测试 now-1d 排除 / now+1h（未直接用 ±1s，规避 naive timestamp × NOW() 的会话时区脆弱性）/ NULL 包含（§22）；**过期不自动 archive/delete**（§23：lifecycle 留后续）；expired 记忆不 mark_accessed（SQL 层根本不返回）。

## 15. Access Semantics

- **定义**：只有通过 gate 并进入最终 context 的记忆（工具路径=真正返回项）才算被使用（§25）。
- **候选/被拒者不 mark**（§26/§60，测试锁定 count 不变）；**无双计数**（§32：start_session 直连 retriever 不经 search，每轮每条至多 +1）。
- **批量**：`mark_accessed(ids, tenant, user)` 单 SQL 带三重过滤（§27/§28）。
- **fail-open**（§29/§62）：独立短事务，失败仅 `memory_access_mark_failure_total` +1 与 warning 日志，主聊天不受影响（专项测试）；retrieval 主查询失败仍走原降级契约（§30，test_pgvector_l3 回归锁定）。
- **decay 联动**（§104）：注入刷新 last_access_at 后不再被 180/90 天档命中（测试：200 天前 + mark → decay 后 importance 不变）。

## 16. Metrics / Trace

新增 5 指标（§43：全固定枚举 label；实机 /metrics 已见注册）：candidate / accepted{source=global|semantic} / rejected{reason=below_relevance} / access_mark / access_mark_failure。既有 `memory_retrieval_total/-failure_total/-latency_seconds`、`degradation_alerts_total` 全部保留（§44）。Trace：retrieval 各阶段计数已入结构化日志（candidate/accepted/sources，无 content/PII，§46）；`memory.retrieve` span 留 STOP G 与 store 链统一贯穿（同 §45 的最小满足口径——metrics+日志已可回答 §112 全部问题）。

## 17. Tests

全部 `PGPORT=5433` + `--no-cov`：

| 轮次 | 范围 | 结果 |
|---|---|---|
| 第一轮 | STOP D 新增（gate 12 + safe_context 8） | **20 passed** |
| 第二轮 | `tests/memory` 全量 | **77 passed / 0 failed**（含 STOP B/C 全部契约测试——D23 写入与版本契约不退化 ✓） |
| 第三轮 | 组合（memory+api+rag isolation+write_ordering） | **97 passed / 3 failed**（与基线完全一致的 3 个 pre-existing：503 存量 + write_ordering 合跑污染；D26 ✓） |
| 第四轮 | `tests/context_budget` + runner trace/stream（§83/§84：role 变化波及面） | **208 passed** |

## 18. Real DB Validation（§85）

真库（非 mock repo）验证：expired active 行 SQL 层不返回（即使相似度 1.0）；inactive 不返回；wrong tenant / wrong user 不返回（SQL 双 scope）；valid relevant 正常返回。均由 `test_memory_retrieval_gate.py` 在权威库实证。app 镜像已 rebuild，健康，新指标在 /metrics 注册。

## 19. Runtime Validation

§8/§102 要求的行为验收在真实 provider 不可用时允许 captured-messages 路径：`test_injected_memory_marked_via_start_session`（真 PG + start_session 全链）对最终 message list 断言——攻击/正常记忆原文只存在于 AIMessage `<memory_context>` 数据块、SystemMessage 仅固定 policy、注入条 access_count+1。实机 app 已加载 STOP D 代码（指标注册为证）；真实 provider（豆包 plan 套餐 Key，并行会话已验证可用）的全链路 §86/§100-103 场景留 STOP G 统一冻结。

## 20. Performance

读路径仍为**单 embedding + 单 DB 查询 + Python rank**（§78），无新增模型调用；新增开销 = gate 过滤（O(20) 内存计算）+ 1 次批量 UPDATE（≤5 行，独立事务，不在首 token 等待路径上可安全同步执行——session 生命周期为开即用即提交，§79 选短 UPDATE 方案而非跨任务 fire-and-forget）。`memory_retrieval_latency_seconds` 直方图继续覆盖整段耗时。

## 21. Compatibility

- 旧 API：`memory_search_tool(query, top_k)` 语义不变（显式搜索宽松 gate + SQL eligibility 补齐 + 返回项 mark，§70/§71）；`HybridRetriever.retrieve` 返回类型变化为内部契约（调用方仅 service 两处已同步）；`search_hybrid` 返回元组为 repo 内部契约（两个直接消费测试已跟进）。
- L2/L3 数据块共存顺序锁定（§84，专项测试）；L2 角色安全行为零改动。
- STOP B/C 全部写入契约回归通过（§81/D23/D18）。
- 多会话：本轮未触碰 celery_app/queue_router/config/tasks 等共享文件；提交仅限 STOP D 文件清单。

## 22. Remaining Risks

- **STOP E**：L2 token watermark、SESSION_SUMMARY_TRIGGER_TOKENS、summary max token、Memory/L1/L2 与 ContextBudget 最终职责深度复核（§41 的预裁剪与 proxy 层最终裁剪的双层结构确认）。
- **STOP F**：Memory Golden Evaluation、relevance threshold 0.45 的实证调优（本轮声明为保守参照初值）、key 质量、irrelevant injection rate 基线。
- **STOP G**：真实 provider（豆包 plan Key）跑 §86/§100-103 全八场景 + 最终冻结。
- 其他：quarantine 169 条产品处置仍未决定（STOP C 遗留）；实库样本相似度审计因 exec 进程无法初始化 DB-managed provider 未完成，threshold 实证留 STOP F/G；`memory.retrieve` span 贯穿留 STOP G。

## 23. Commit Chain

`fix(memory): harden retrieval relevance and context safety`（本 STOP 单一提交，hash 见 git log）。
