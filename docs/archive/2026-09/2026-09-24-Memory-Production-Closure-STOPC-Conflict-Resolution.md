# Memory Production Closure — STOP C 实施报告：memory_key + Tenant Scope + Conflict Resolution + Atomic Supersede

> 日期：2026-09-24 ｜ 前置：STOP A（`79d4668`）/ STOP B（`3e16e66`）均已冻结
> 报告遵循任务书 §97 返回格式。

---

## 1. Verdict

**STOP_C_PASS=true**

## 2. Root Cause / Goal

STOP A/B 确认的六个根因：①结构化事实无 memory_key，版本管理完全依赖 embedding（P0-1）；②find_similar 只取 top-1，真冲突记录可能不进裁决（P0-2）；③0.85≤sim<0.92 静默丢弃改口事实（P0-3）；④origin 有 provenance 但未进冲突优先级（P0-4）；⑤无 tenant_id，隔离只靠 user_id（P0-5）；⑥supersede 无事务/并发契约（P0-6）；外加 decay 死代码必须关闭（P1）。

## 3. Frozen Baseline

STOP A：`79d4668`（审计，G1-G8 逐条证实）｜STOP B：`3e16e66`（provenance，15/15 新测试 + 组合 53 passed/3 failed）。

## 4. Changed Files

| 类别 | 文件 |
|---|---|
| 迁移 | `sql/migrations/048_memory_scope_and_versioning.sql`（新） |
| 新模块 | `memory/keying.py`（normalize/validate + StoreOutcome/StoreResult + origin priority）、`memory/conflict.py`（裁决矩阵 + 原子 supersede 集中实现） |
| 记忆核心 | `memory/models/memory.py`（+tenant_id/memory_key/structured_value）、`memory/long_term.py`（MemoryFact 扩展、6 段协议解析、store_with_resolution 核心 + store_single 兼容 wrapper）、`memory/repository/memory_repo.py`（全方法 tenant scope、find_active_by_key FOR UPDATE、find_similar_candidates top-N、apply_decay 互斥区间、supersede 条件修正）、`memory/service.py`（tenant 全链透传、per-fact 事务 + IntegrityError 有界重试、outcome metrics）、`memory/manager.py`/`memory/retriever.py`（tenant 透传） |
| 接线 | `orchestration/graph/runner.py`、`rag/chain.py`（start_session/end_turn 传 tenant）、`tools/memory.py`（tenant 从 ContextVar；memory_key/structured_value 可选参数；blocked 语义化提示） |
| decay 接线 | `tasks/memory_maintenance_tasks.py`（新，薄壳）、`tasks/celery_app.py`（include + beat 04:30 UTC）、`tasks/queue_router.py`（maintenance 路由登记）、`memory/decay.py`（explicit 豁免契约 + 互斥区间修正） |
| 观测 | `observability/metrics.py`（memory_store_outcome_total{outcome}/memory_supersede_total/memory_conflict_total/memory_decay_records_total{action}） |
| prompt | `prompts/defaults/memory_long_term_fact_extraction.yaml`（6 段协议 + key 规范 + 反例），**已发布 DB v4**（active_version=4，逐步 transition 留痕） |
| 测试 | `tests/memory/conftest.py`（共享 require_pg/ScriptedEmbedding/清理）、`test_memory_conflict_resolution.py`（18 用例）、`test_memory_supersede_atomicity.py`（3）、`test_memory_decay_runtime.py`（2）、存量跟进：`test_memory_provenance.py`（b6/b8 口径适配新契约）、`tests/rag/test_memory_isolation.py` + `test_memory_write_ordering.py` + `tests/orchestration/graph/test_runner_trace_finalize.py` + `test_stream_events_flow.py`（stub 签名补 tenant_id 形参） |

## 5. Before / 6. After

**Before**：写入口径 = embedding top-1 相似度彩票（0.85~0.92 改口静默丢弃、≥0.92 盲区 supersede）；无 tenant；decay 从未运行；同 key 概念不存在；reaffirm 无从谈起。
**After**：keyed 事实（memory_key 非空）按 `(tenant, user, key)` 精确定位唯一 active 版本，FOR UPDATE 行锁 + 同事务原子 supersede + partial unique index 兜底；unkeyed 事实保留语义去重但只判 duplicate（sim≥0.92），中等相似一律 INSERT；decay 每日运行（explicit 豁免）。

## 7. Tenant Scope

- **来源**：网关验签身份（`resolve_identity` → Identity.tenant_id → runner/rag 链 → memory 全链）；工具侧 `get_tool_tenant_id()` ContextVar。禁止 body/query/LLM 提供 tenant（工具签名不含 tenant_id/user_id，§20）。
- **backfill（权威实查）**：`auth.users`（同库，migration 008 起）为权威映射。实库核验：memory user `42`（122 条）↔ `auth.users.id=42`（tenant_id='default'，status=1）→ `UPDATE...FROM auth.users` 权威 backfill 122 条；`default`（156 条，未认证兜底桶）与 `p4-eval`（13 条，评测桶）无身份表行 → **无法权威映射 → 归入保留哨兵 `'quarantine'`**（169 条，确定性隔离下线，不做猜测归属，§7）。任何真实租户查询永不命中 quarantine 行。
- **fail-closed**：`normalize_tenant_id()` 把未声明租户归一为 `'default'`（本部署唯一真实租户，与 auth.users 现值一致）——语义是「查询永远携带具体 tenant 值」，不存在"缺失 → 查全表"；tenant=X 永远查不到 default 桶，反之亦然。quarantine 为迁移保留值，运行时归一永不产出（有测试锁定）。

## 8. memory_key / structured_value Contract

- key = 属性身份（`project.main_llm`），dot 分隔 snake_case，≤4 段、≤128 字符、段内 `[a-z0-9_]`、**纯数字段拒绝**（防手机号类 PII 形态）；值编入 key 的写法（`project.main_llm.deepseek`）由 prompt 反例 + 格式白名单共同阻止。
- value = 规范化属性值（NFKC + trim + lower，≤256），`DeepSeek/deepseek` 归一、`Qwen3-8B/14B` 保持可区分；**落库前强制过 PII filter**（§75：`structured_value` 与 content 同防线，测试锁定 `alice@example.com → [邮箱]`）。
- key/value **成对**：任一非法 → 双双 NULL（走 unkeyed 路径）；`normalize_memory_key()/normalize_memory_value()/normalize_tenant_id()` 单一实现集中在 `memory/keying.py`，parser（自动通道）与 store_with_resolution 入口（工具通道）双重执行。
- **不新增 LLM 调用**（C6）：提取协议扩展为 6 段 `类型|内容|置信度|用户原话片段[|属性key[|属性值]]`，复用现有一次提取调用；prompt 明示「泛知识/经历描述不要编 key」（§13：错误 key 比 NULL 更危险）。
- legacy 291 条 **memory_key=NULL**，不做 LLM 批量回填（§21/C22，实库 `key_null_legacy=0` 违规数验证）。

## 9. Conflict Resolution Matrix（§92，集中实现于 memory/conflict.py）

| existing | incoming | same value | action |
|---|---|---|---|
| inferred | inferred | yes | DUPLICATE（无变更）/ REAFFIRMED（confidence 可升） |
| explicit | inferred | yes | REAFFIRMED（origin/confidence 只升不降） |
| inferred | explicit | yes | REAFFIRMED + origin 升格 explicit |
| explicit | explicit | yes | REAFFIRMED |
| inferred | inferred | no | **SUPERSEDED**（不再看 cosine ≥0.92，§29） |
| explicit | inferred | no | **CONFLICT_BLOCKED_EXPLICIT**（不插入第二条 active，§30） |
| inferred | explicit | no | SUPERSEDED（§27） |
| explicit | explicit | no | SUPERSEDED（§28：用户明确新决定优先） |
| legacy | explicit | no | SUPERSEDED |
| legacy | inferred | no | SUPERSEDED（legacy 旧数据让位于新推断） |
| non-legacy | legacy | no | CONFLICT_BLOCKED_EXPLICIT（防御分支，reason 区分） |

Unkeyed：sim≥0.92 → DUPLICATE；0.85~0.92 → **INSERT**（C14，旧行为静默丢弃）；<0.85 → INSERT。**unkeyed 永不 supersede**（C15："喜欢 Python" vs "喜欢 Go" 高相似也不互斥，测试锁定）。

## 10. Atomic Supersede

事务顺序（同一 AsyncSession 事务，service 层 per-fact commit）：
```
1. find_active_by_key(tenant,user,key, for_update=True)   行锁
2. deactivate(old)   is_active=false（不写 superseded_by）
3. insert(new)       flush 取 id
4. supersede(old,new) 回填 superseded_by（条件已修正为仅按 id：
   旧实现带 is_active=True 条件，原子化顺序下永远 0 行——接线时发现的隐性 bug）
COMMIT（任一步失败整体 ROLLBACK，旧行恢复 active，无中间态泄漏）
```
DB invariant：partial unique index `uq_memory_active_key(tenant_id,user_id,memory_key) WHERE is_active AND memory_key IS NOT NULL`（§37）；另有 `idx_memory_scope(tenant_id,user_id,is_active)`。不建向量索引（留后续）。

## 11. Concurrency Safety

- **existing row**：FOR UPDATE 悲观锁，双 writer 串行化（§39）。
- **首建竞态**：双事务同时判 NONE 后双插 → loser 撞 partial unique → `IntegrityError` → rollback 当前事务 → **独立事务重读重裁决一次**（有界 1 次，§40），在 `service.store` 与并发测试中同路径实现。
- **supersede 竞态**：A 锁 old → B 的 FOR UPDATE 等待 → A 提交后 B 拿到锁但行已 inactive（查询过滤 active）→ B 走 insert → 撞 A 新行的 unique → B 重试裁决。不变量：**并发同 key 最终恰好 1 个 active**（§68 测试以 asyncio.gather 双事务实证）。

## 12. Semantic Compatibility

- **keyed path**：`find_active_by_key` 取代 embedding 裁决——top-1 blind spot 对 keyed 记忆彻底消失（C13 测试：新旧 content embedding 完全正交仍正确 supersede）。
- **unkeyed path**：`find_similar_candidates`（top-5，阈值 0.85 召回）替代 top-1 bool，只服务 duplicate 判定（§46）。
- **0.85~0.92 修复**：sim=0.88 的改口式新事实现在 INSERT（C14 测试，ScriptedEmbedding 精确构造 0.88/0.95 相似度）。
- **禁 semantic supersede**：sim=0.95 的 unkeyed 新事实 → DUPLICATE 且旧行保持 active（C15 测试）。

## 13. Decay Decision：**CONNECTED**（正式接线）

- STOP A 死代码结论复核属实后，按 §53「基础设施已成熟则接线」选择 A：`memory.daily_decay` beat 任务（每日 04:30 UTC，maintenance 队列，薄壳模式核心逻辑在 `MemoryDecayService`），QueueRouter 显式登记（fail-closed 面闭合）。
- **explicit 完全豁免**：不衰减、不自动归档（§55 简单契约——"以后一直用中文"不因时间消失）；只操作 active 行（§54）；衰减 SQL 修正为**互斥区间**（>180 天 ×0.9、90~180 天 ×0.95——旧实现两档级联双重衰减，接线时发现并修复，测试锁定）。
- 实机：agent-beat `SCHEDULED: True`、agent-maintenance-worker `REGISTERED: True`（两容器已用新镜像重建；beat 有独立镜像 agent-beat，首次 build 只建 agent-app 未生效，已补）。首次自动执行为次日 04:30 UTC——未手动触发以避免无谓改动存量记忆（衰减逻辑已由真库测试证明）。
- 已知限制（§56）：主 L3 注入路径暂不 mark_accessed（STOP D），recency 偏保守 → 参数从宽。

## 14. Database Changes

- Migration `048_memory_scope_and_versioning.sql`（幂等，已实库执行）：3 列 + 2 索引 + 权威 backfill（122 条 UPDATE...FROM auth.users）。
- 实库终验（§80）：**duplicate active keyed group = 0 rows** ✓；`tenant_null=0` ✓；`origin='legacy' AND memory_key IS NOT NULL = 0`（legacy 未被回填 key）✓；分布 = quarantine/legacy 169 + default/legacy 122。

## 15. Metrics / Trace

新增：`memory_store_outcome_total{outcome=INSERTED|DUPLICATE|REAFFIRMED|SUPERSEDED|CONFLICT_BLOCKED_EXPLICIT|REJECTED}`（outcome 全集，§31 禁止 bool）、`memory_supersede_total`、`memory_conflict_total`、`memory_decay_records_total{action}`；复用 STOP B 的 explicit/inferred/extraction 系列。label 全部固定枚举（§49 禁高基数）。Trace：裁决结果已入结构化日志（origin/outcome/key 有无，不记 content/PII），span 留 STOP G 与 store 链 trace 贯穿统一评估。

## 16. Tests

全部 `PGPORT=5433`（权威库）+ `--no-cov`：

| 轮次 | 命令 | 结果 |
|---|---|---|
| 新增（3 文件） | conflict_resolution + supersede_atomicity + decay_runtime | **24 passed** |
| 第二轮 | `tests/memory -q --no-cov` | **57 passed / 0 failed**（含 STOP B 的 15 用例全绿——C19：provenance 防线不退化） |
| 第三轮（组合） | memory + api/test_memory_routes + rag/isolation + rag/write_ordering | **77 passed / 3 failed**（与 STOP A/B 基线完全一致的 3 个 pre-existing：503 存量失败 + write_ordering 合跑污染；failed 未扩大，C25 ✓；passed 53→77） |
| 补充 | runner_trace_finalize + stream_events_flow + write_ordering | 32 passed |

**存量测试跟进**（非掩盖回归，口径适配新契约）：`test_memory_provenance` b6（FakeLLM 固定 content 在新契约下合法判 DUPLICATE——改为按 evidence 生成不同 content，保住"message id 不串轮"的原始断言意图）、b8（全表 legacy 断言在 048 后不再成立——改为断言迁移不变式本质 origin/tenant 无 NULL）；4 个测试文件的 memory stub 签名补 `tenant_id` 形参（接口演进波及）。

## 17. Real DB / Runtime Validation

- §80 invariant SQL 全绿（见 §14）。
- 实机 rebuild：`agent-app`（聊天链）、`agent-beat` + `agent-maintenance-worker`（decay 生命周期）三镜像重建并健康；prompt v4 发布后容器内快照验证含 key 协议。
- 真实 provider（豆包）仍处 FreeTierOnly 配额耗尽（STOP B 已登记的外部阻塞）——按任务书 §84 分开报告：**代码/DB/mock-E2E PASS，真实 provider 链路留 STOP G 复验**（§81-83 的真实 LLM 改口场景需配额恢复）。

## 18. Compatibility

- 旧记录：291 条 legacy 全部可读（tenant 已权威 backfill/quarantine，key=NULL 走语义路径），content/embedding/origin/confidence/is_active/superseded_by 零改动（§70 ✓）。
- 旧 API：`memory_store_tool(content, type)` 原签名可用（key/value 为可选新增，C21 测试）；`store_single() -> bool` 保留为 wrapper；`MemoryService.search`/`start_session`/`end_turn` 新增带默认值的 tenant 参数。
- 主聊天/SSE/路由不变；runner/rag 链仅增加 tenant 透传实参。
- **多会话纪律**：`celery_app.py`/`queue_router.py` 混有并行会话（Phase3 任务运行时）的 `tasks.pending_recovery` 未提交改动——本次提交采用 **hunk 级部分暂存**，只提交 STOP C 的 decay 接线行，对方行保留在其工作区。

## 19. Remaining Risks

- **STOP D**：L3 SystemMessage 安全注入（G4）、MEMORY_MIN_RELEVANCE_SCORE、candidate→gate→rank 0~5、expire_at 检索过滤、mark_accessed 主路径（decay 的 recency 限制来源）。
- **STOP E**：L2 token watermark / summary 触发。
- **STOP F**：Memory Golden Evaluation（key 生成质量、提取 key 覆盖率需 Golden 度量）。
- **STOP G**：真实 provider（FreeTierOnly 解除后）跑 §81-83 实机改口场景 + 八场景冻结。
- 本轮遗留：①quarantine 169 条的去向待产品决策（保留隔离或人工迁移）；②unkeyed duplicate 阈值 0.92 复用现有配置常量，若 Golden 评测显示过松可在 STOP F 调整；③`user.contact` 类 key 依赖模型自觉不编 PII 进 key（字符集白名单已挡 email/手机号形态，中文名等文本型 PII 编 key 的残余风险由 prompt 约束 + STOP F 评测兜底）。

## 20. Commit Chain

- `fix(memory): add conflict-aware memory versioning`（本 STOP 单一提交，含 decay 接线——薄壳复用现有基础设施，未达拆分阈值；见 §90）——hash 见 git log。
