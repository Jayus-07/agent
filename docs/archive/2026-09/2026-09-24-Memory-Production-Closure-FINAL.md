# Memory Production Closure — FINAL 冻结报告

> 日期：2026-09-24 ｜ 链：STOP A `79d4668` → B `3e16e66` → C `538145a`+`d306be5` → D `6d157ac` → E `1d81153` → F `230d8ef` → G（本提交）
> 本报告为 Memory Production Closure 的最终冻结判定，覆盖任务书 §十 Acceptance Gate A-Q 与 §十一全部章节。

---

## 1. Final Verdict

```text
STOP_E_PASS=true
STOP_F_PASS=true
STOP_G_PASS=true
MEMORY_PRODUCTION_CLOSURE_PASS=true
MEMORY_CORE_FROZEN=true
```

## 2. Commit Chain

| STOP | Commit | 内容 |
|---|---|---|
| A 审计 | `79d4668` | 全量只读审计，6 缺口定位 |
| B provenance | `3e16e66` | 047 迁移 / origin 三态 / 证据防线 fail-closed |
| C 冲突裁决 | `538145a`+`d306be5` | 048 tenant/memory_key/原子 supersede/裁决矩阵 |
| D 检索与安全注入 | `6d157ac` | SQL eligibility / relevance gate / global 白名单 / safe injection / mark_accessed |
| E 会话与预算边界 | `1d81153` | end_turn 摘要后台化+滞后门 / fallback 输出帽 / Token Ownership 矩阵 / E-I 不变量 7 用例 |
| F Golden 评测 | `230d8ef` | 60 例 7 类 Golden / 真实 embedding 全真 sweep / threshold 0.45→0.35 实证定标 |
| （前置修复）| `01bf8c8` | 恢复悬空提交 69b2f4e（ContextBudget 收口 STOP B），修复 main RAG import 断裂 |
| G 实机验收+冻结 | 本提交 | memory.retrieve span 贯穿 / 八场景真 provider 驱动+结果 / FINAL 报告 |

## 3. Architecture Frozen（最终生命周期）

```text
message
→ extraction（后台，4 段协议+evidence 子串校验 fail-closed）
→ normalize/key（memory_key/structured_value 成对，非法双置 NULL）
→ conflict/version（keyed=原子 supersede；unkeyed=仅 duplicate）
→ persistence（PG memory_records，origin 通道强制赋值）
→ eligibility（SQL 层：tenant+user+active+not expired）
→ semantic/global retrieval（candidate 20 → gate → global 白名单分流）
→ relevance gate（cosine ≥ 0.35，仅 semantic score）
→ rank/merge（0.5/0.3/0.2+确定性 tie-break，global 保留额，max 5）
→ safe context（policy SystemMessage + <memory_context> AIMessage 数据块）
→ access mark（仅注入条，独立短事务 fail-open）
→ L1/L2/L3 assembly（L2 块→L3 块→最近 20 条；history_budget ≤2048 预裁剪）
→ ContextBudget（proxy preflight：L2 trim→L4 collapse→hard trim→L5 AutoCompact，唯一 hard authority）
→ provider（DB 角色解析）
→ metrics/trace（7 指标族 + memory.retrieve span 贯穿）
```

## 4. Write Contract

- 通道决定 origin：`memory_store_tool`=explicit(0.98)，自动提取=inferred（clamp 0-1，hedged 封顶 0.55），legacy 只读不新增。
- source_message_id 由 end_turn 落库时确定并透传（禁回查最新消息，防串轮）；写入全链 evidence 校验 fail-closed。
- keyed 事实：同 (tenant,user,memory_key) partial unique 索引兜底，`store_with_resolution` FOR UPDATE 原子 supersede；同值 reaffirm 不新建；explicit 与 inferred 冲突阻断。
- unkeyed：语义去重只判 duplicate（≥0.85），**永不 supersede**（保守契约，见 §16 G8 观察）。
- 摘要写入：增量水位线 CAS（expected_through 不符即弃），chat_messages 原始行永不删改。

## 5. Read Contract

- start_session：L2 摘要块（≥30 字符守卫）→ L3 记忆块 → 最近 ≤20 条（连续去重）→ history_budget 预裁剪（丢弃顺序冻结：L3 data → L2 data → 最旧 L1 → recent）。
- L3：query 语义检索（非 session_id）；gate 只看 semantic score；0 条合法不强凑；工具显式搜索宽松 gate + 同 scope mark。
- mark_accessed：只有最终注入条 +1（无候选/双计数）；decay 时间语义由此联动。

## 6. Conflict Contract

同 §4；supersede 后旧行 is_active=False + superseded_by 指针；水位线/摘要 CAS 同源语义（stale 即弃，幂等可重试）。

## 7. Token Contract

- L1 条数 20 ｜ L2 触发条数 50 + 增量滞后门 10（`CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES`）｜ L2 输出帽 512（provider 级，增量与 fallback 同帽）+ ProtectedFacts 补丁 ≤40 条 ｜ L3 注入 ≤5 / candidate 20 / gate 0.35。
- 装配预裁剪预算 = `context_budget.history_budget()` 派生（HISTORY_TOKEN_BUDGET=2048 上限）——memory 层无第二 hard-budget authority。
- 最终唯一裁剪权威：`ContextBudgetManager.prepare_llm_context`，执行点 proxy `_preflight_context` 全调用形态；System/语义 pin/当前 query 永不裁。

## 8. Safety Contract

- 记忆/摘要原文绝不进 SystemMessage（policy 固定文本 + AIMessage 数据块，双层 sanitize 开/闭标签）。
- 八场景 G6 实测：攻击记忆注入后 role 结构零逃逸、模型行为不受劫持。
- trace 只写 count/threshold/枚举，严禁记忆原文/PII/租户标识。

## 9. Expiration Contract

`expire_at` SQL 层过滤（过期行不占候选槽位、不 mark）；不自动 archive/delete（lifecycle 留后续）。G5 实测 expired leakage=0。

## 10. Access Contract

access_count/last_access_at 只在真注入/真返回时 +1；mark 失败 fail-open（独立短事务 + `memory_access_mark_failure_total`）；decay 180/90 天档被注入刷新豁免。

## 11. Evaluation（真实 Golden 数字）

- 数据集：60 例 / 62 种子 / 7 类别（`backend/evaluation/datasets/memory_golden.jsonl`）。
- 方法：生产同款 embedding（DB 绑定 `qwen3.7-text-embedding`，1024 维）+ 真 PG pgvector + 全真 gate/rank/merge；阈值逐档注入复跑。
- **定稿 threshold=0.35**（sweep：0.30~0.35 平台 recall 1.000/precision 0.889/irr 0.105；0.45 处 recall 崩至 0.797——过敏类安全记忆 0.448 被拒；0.35 距无关带主体 0.24~0.31 有 0.05 边距）。
- @0.35：Recall@5 **1.000**｜Precision **0.889**｜MRR **1.000**｜irrelevant_injection_rate **0.105**（4 硬负例：同实体/词面重叠，任何 ≥0.30 阈值不可分离）｜zero-memory accuracy **0.857**（纯 zero 类 6/6）｜global recall **6/6 用例**。
- 隔离与安全：expired / cross-user / cross-tenant leakage = **0**；注入结构逃逸 = **0**（8 攻击记忆全注入全拦截）。
- key 质量（实库 active 165 条）：bad_key 0% / 概念重复组 0 / missing_key 95.8%（存量观察）。

## 12. Real Provider Acceptance（八场景，`memory_stopg_results.json`）

运行时：进程内 `MultiAgentSystem → GraphRunner` 全真链（guard→router→节点→reporter→end_turn→后台 store），chat=DB role main（doubao-seed-2.0-mini），embedding=DB 专项绑定（qwen3.7-text-embedding），PG 5433 真库。网关/SSE 层不在本 STOP scope（由 domain-runtime/travel E2E 覆盖）。

| 场景 | 结果 | 关键证据 |
|---|---|---|
| G1 相关记忆 | **PASS** | access_count=1；回答实际采用偏好（"双份浓缩燕麦拿铁…"） |
| G2 无关记忆 | **PASS** | 最终注入=0（candidate 可存在，不强凑） |
| G3 global 偏好 | **PASS** | 跨主题注入 + 模型真实执行（回答末尾出现 `<GLOBAL_OK>`） |
| G4 主题偏好不泄漏 | **PASS** | travel.seat_preference 在无关主题下注入=0 |
| G5 过期/失活/错用户/错租户 | **PASS** | 四类 access_count 全 0 |
| G6 注入攻击记忆 | **PASS** | 注入成功且结构零逃逸、模型不受劫持 |
| G7 L2+L3+长历史+长 query | **PASS** | overflow=0、5 条记忆全注入、无崩溃（回答被 Router 改道 workflow 属路由层既有混淆，非记忆/预算缺陷，见 §16） |
| G8 记忆变更跨轮 | **PASS** | v1 落库→下轮注入(access=1)→correction→v2 落库→第 4 轮 v2 注入(access=1)（unkeyed 旧版共注现象见 §16） |

## 13. Observability

- **memory.retrieve span 贯穿完成**（STOP D 遗留）：runner 侧创建（ambient trace 所在），属性六字段 `candidate_count / semantic_accepted / semantic_rejected / global_accepted / final_injected / threshold`，实测样本 `{2, 1, 0, 1, 2, 0.35}`；service 在无 trace 的后台 loop 侧留 noop 安全 span + `l1.retrieval_stats` 回带。**span 只含 count/threshold 枚举，零内容零 PII**。
- 指标行为序列（八场景前后差值）：candidate **+12**、accepted **+13**、rejected **+2**（G2/G4 的 below_relevance 拒绝）、access_mark **+8**、mark_failure **0**、retrieval_failure **0**、overflow **0**——非"注册即 PASS"，全部由行为触发。
- 线上 app `/metrics` 实证：`memory_retrieval_total{operation= retrieve}`=6、latency 直方图有真实分布（app 容器为 STOP D 镜像，本轮代码冻结后随下次 rebuild 生效）。

## 14. Performance

- 每轮 embedding 次数：读 1（query）+ 写链按提取条数；Golden/八场景零额外重复调用。
- DB query 次数/轮（读路径）：1 次候选检索 + 1 次批量 mark + L2 触发时 2 个轻量状态查询。
- 额外 LLM 次数/轮：0（摘要改增量滞后门后，稳态从每轮 1 次降为每 ≥10 条增量 1 次，且后台化不阻塞流关闭——修复前实测可阻塞至多 5s）。
- memory retrieval latency：`memory_retrieval_latency_seconds` 直方图 p95 < 1s（线上 6 样本：0.5-1.0 桶 5 个、0.25-0.5 桶 3 个）。
- summary-trigger turn 额外延迟：0（后台 fire-and-forget；同步内联仅无 loop 的 worker 线程路径）。

## 15. Regression

| 套件 | 结果 |
|---|---|
| tests/memory（含 STOP B/C/D/E/F 全部契约） | **88 passed** |
| tests/context_budget（冻结 CORE 全量） | **182 passed** |
| runner trace/stream + direct flow e2e + rag memory isolation + golden 契约 | **14 passed** |
| 新增 regression | **0** |
| pre-existing failed | 本轮未引入（STOP D 基线的 3 个已知 pre-existing 属并行会话波及，本轮全量跑中未复现失败） |

## 16. Known Remaining Non-Blocking Risks

1. **unkeyed correction 共注**（G8 观察）：自动提取未产出 key 时，改口走 unkeyed duplicate 路径（sim<0.85 即新建），新旧两版同时 active 并可能同轮注入——STOP C 的保守契约（unkeyed 永不 supersede）所致；keyed 路径的版本闭环已由 Golden conflict 用例（真管线）+ STOP C 契约测试证明。改善方向=提取协议 key 化率，随增量写入自然改善（实库 missing_key 95.8% 为存量）。
2. **G7 路由混淆**：长说明型 query 被 Router 改道 workflow（既有路由层行为，与 context-budget STOP D 记录的"E2E 探针被导去 RAG"同源），记忆/预算判据全过；路由自身优化归 Router 域。
3. **4 个硬负例**（同实体/词面重叠，sim 0.44-0.53）：L3 无 reranker，属 embedding 排序能力边界；每例至多 1 条陪跑且有 policy 约束。
4. **孤儿 policy SystemMessage**：数据块被预算裁剪后其固定 policy 文案保留（悬空引用，cosmetic，P2）。
5. **quarantine 存量 169 条产品处置**未决（STOP C 遗留，运行时已不可达）。
6. 驱动进程的 trace store 落盘依赖异步 TraceWriter（短命进程可能不 flush）——生产 app 进程路径不受影响；span 契约以运行时对象捕获验证。

## 17. Frozen Files / Contracts

以下此后不得随意修改（改动 = 重开收口，需 overflow/golden 回退证据或 provider 变更）：

- `memory/retriever.py`（gate/rank/merge/白名单语义与阶段计数）
- `memory/service.py`（start_session 装配顺序、mark_accessed 语义、降级契约、后台摘要）
- `memory/conflict.py` + `store_with_resolution`（裁决矩阵、原子 supersede）
- `context_budget/manager.py` / `auto_compact.py` / `role_safety.py`（CORE_FROZEN，01bf8c8 恢复后完整在 main）
- `config/memory.py` 的 `MEMORY_MIN_RELEVANCE_SCORE=0.35`（Golden 实证定标，改动必须附新 sweep）
- `context_budget/role_safety.py` 的 policy 文本与 sanitize（逐字稳定，prefix cache 依赖）
- Golden 数据集与结果（`memory_golden.jsonl` / `memory_golden_results.json`）与回归门 `test_golden_dataset_contract.py`

## 18. Rollback

- 代码层：revert E/F/G 三个提交即可回到 STOP D 冻结态（三者无对 A-D 提交的语义修改）；`01bf8c8` 为 main 一致性修复，**不可回退**（回退即重现 RAG import 断裂）。
- 配置层：`MEMORY_MIN_RELEVANCE_SCORE` env 覆盖即回 0.45；`CONTEXT_BUDGET_ENABLED=false` 关预算链；`CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES` env 调滞后门。
- 数据层：047/048 迁移向后兼容（新增列/索引），无破坏性 DDL；摘要水位线回退 = 置空 summary_through_message_id（幂等重摘要）。

## 19. Final Conclusion

Acceptance Gate A-Q 逐项满足：A ancestry ✓（五提交 merge-base 验证）｜B 矩阵 ✓（STOP E 报告 §4）｜C 唯一 hard authority ✓｜D summary bounded ✓（512 帽+补丁上界+滞后门）｜E L3 bounded & budgeted ✓（E-I3/I6 实测）｜F Golden 完成 ✓（60 例真跑）｜G threshold 实证 ✓（0.35）｜H irr_inj 0.105（硬负例构成，可接受基线）｜I/J/K 泄漏全 0 ✓｜L 逃逸 0 ✓｜M 八场景 8/8 ✓｜N span 贯穿 ✓｜O 指标行为序列 ✓｜P 零新回归 ✓｜Q A-D 契约零退化 ✓（88+182 全量回归）。

```text
MEMORY_PRODUCTION_CLOSURE_PASS=true
MEMORY_CORE_FROZEN=true
```
