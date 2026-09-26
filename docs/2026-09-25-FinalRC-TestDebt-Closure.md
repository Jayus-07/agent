# FINAL RC — Test Debt Closure 与 Global Regression Closure

日期：2026-09-26
范围：Final RC 测试债、全量回归和项目内部闭环；不扩展产品功能，不处理外部发布条件。

## 1. Verdict

```text
FINAL_RC_TEST_DEBT_CLOSURE_PASS=false
GLOBAL_REGRESSION_PASS=true
PROJECT_INTERNAL_CLOSURE_PASS=false
NEW_REGRESSION_COUNT=0
TEST_DEBT_UNCLASSIFIED=0
KNOWN_FLAKY_COUNT=0
PRODUCTION_RELEASE_GATE_PASS=false
```

测试债与全量回归已经归零；最终闭环暂不签发，唯一的项目内阻断是工作树仍有
此前会话生成的 `data/**` 变更，尚未得到归属确认，因此不能把工作树宣称为 clean。
外部发布条件（API Key Rotation、Canary Observation Window、Booking Provider）
仍按原计划保持阻断，不计为测试债。

## 2. Baseline 与最终结果

原始计划中的上一轮数字：

```text
7030 passed / 105 failed / 3 errors
```

本轮锁定的最终收集快照为 7398 个节点（历史 STOP A 快照为 7397，期间已有
单项测试覆盖补充）。最终全量使用容器内网 PostgreSQL/Redis、独占
`agent_memory_rc_full10`、单进程、`--no-cov`：

```text
FINAL_COLLECTED=7398
7276 passed / 0 failed / 0 errors / 122 skipped / 20 warnings
616.72s (0:10:16)
```

证据：

- 收集：[artifacts/final_rc/collect-final.log](../artifacts/final_rc/collect-final.log)
- 全量：[artifacts/final_rc/full-local-serial-final-hermetic.log](../artifacts/final_rc/full-local-serial-final-hermetic.log)
- 锁定口径：[artifacts/final_rc/BASELINE_RUNBOOK.md](../artifacts/final_rc/BASELINE_RUNBOOK.md)

`122 skipped` 是既有质量门禁的跳过项；本轮没有新增 skip/xfail，也没有降低
collection 范围。

## 3. Failure Family Matrix

| Family | 原始数量 | Root cause | 解决方式 | 生产代码是否改变 | 最终结果 |
|---|---:|---|---|---|---|
| Persistent census | 89 | 混合的 RAG/SQL/幂等/LLM/任务等契约缺口，以及少量真实生产缺陷 | 按 35 个文件隔离复跑并逐族修复；详见基线 §10.1–§10.4 | 是，见 §4 | `432 passed / 0 failed`，89 个节点均有闭合证据 |
| Environment-flaky census | 85 | vpnkit→PG 半开连接、共享 beat/worker、Redis/PG 连接风暴、测试依赖镜像边界 | 独占数据库/队列、容器内网、串行跑法和稳定性重复跑 | 否（不修改生产状态机） | 任务 `227 passed`，API `445 passed`，travel `585 passed` |
| 最终串行残余 | 30 | 全量顺序污染：请求 ContextVar/单例 11 个；DB/fixture 生命周期 19 个 | 增加每用例请求态复位、PG fixture 依赖、schema/数据库配置回滚、asyncpg pool 清理；提交 `2f91063` | 否，本提交只改测试 | 独立进程/隔离库 `30/30 passed`，随后全量 0F/0E |

原始 census 总数为 `89 + 85 = 174`。原始失败清单仍保留在
`artifacts/final_rc/baseline-failures-raw.txt`、
`baseline-failures-persistent.txt` 和 `baseline-envflaky-recovered.txt`；没有把
环境污染失败静默删除，而是逐条给出复绿证据。

## 4. Production Code Changes

本轮从 STOP A 收官快照至最终复判共有 19 个生产文件出现变更（15 个含生产
hunk 的提交）；这些变更都有对应的历史失败节点或契约证据，不能把本轮写成
“生产代码零改动”。本次 STOP D/E 新提交 `2f91063` 没有生产代码改动。

| Commit | 生产文件/修复事实 |
|---|---|
| `649a010` | `backend/shared/idempotency.py`：耐久幂等 ledger 连接边界 |
| `767da77` | `backend/tasks/error_taxonomy.py`：空 RAG chunk 终态分类 |
| `fc63aaf` | `backend/rag/retrieval/{enhanced_hybrid_retrieval,multi_path_retrieval,rule_retriever}.py`：检索契约对齐 |
| `455277b` | `backend/rag/preprocessing/metadata.py`：MinHash 标点边界 |
| `322bae9`、`7eb6175` | `backend/infra/llm/proxy.py`：LLM proxy 元数据探测无副作用 |
| `71e14c1` | `backend/customer_service/confirmation_store.py`：数据库故障时阻断 L1 confirmation claim |
| `03bf162` | `backend/evaluation/runners/e2e.py`：离线 fault probe 隔离 |
| `f87b01e` | `backend/rag/vectorstore/pgvector_store.py`：PG vector DDL cache 按表隔离 |
| `da7949b`、`816198f` | `backend/customer_service/experts/complaint.py`、`backend/rag/client.py`、`backend/rag/reranker.py`：remote RAG/投诉/身份契约 |
| `ced12d4` | `backend/app/api/routes/auth_local.py`：路由扫描递归 `_IncludedRouter` |
| `faecf60` | `backend/infra/timeout.py`：保留 fractional request timeout，使用 `setitimer` |
| `78a6251` | `backend/sql/migrations/053_model_price_immutable_trigger.sql`、`scripts/init_db.py`：补齐 model price append-only trigger |
| `2520caf` | `backend/rag/preprocessing/metadata_taxonomy.yaml`：恢复售后 FAQ 分类信号 |

前述提交的定向回归已在基线 §10.1–§10.6 记录；最终全量和核心 smoke 再次覆盖
这些路径。

## 5. Test Integrity

```text
BASELINE_COLLECTED=7397
FINAL_COLLECTED=7398
NEW_SKIP_COUNT=0
NEW_XFAIL_COUNT=0
REMOVED_TEST_COUNT=0
```

审计结论：

- 没有 mass skip、mass xfail 或 `pytest_collection_modifyitems` 式排除；
- 没有删除测试文件；
- 全量仍使用 `PYTHONPATH=backend` 的完整 testpaths，collection-only 实测 7398；
- `--continue-on-collection-errors` 只是保留既有看护参数，最终 collection error 为 0；
- `2f91063` 的全局 fixture 是显式清理 ContextVar/单例/fixture 生命周期，不吞掉
  业务断言，也不改变生产行为；
- `20 warnings` 已保留在日志，没有通过过滤器伪装成零告警。

## 6. Core Frozen Regression

STOP F 核心组合覆盖 registry/layer/ADR、Authorization、SQL、RAG tracer、Memory、
Context Budget、Async/Idempotency、SSE Resume、CS、Travel、Model Governance 和
Domain Runtime：

```text
564 passed / 5 skipped in 93.91s
```

日志：[artifacts/final_rc/core-smoke-final.log](../artifacts/final_rc/core-smoke-final.log)。

P4 handoff asyncpg 池隔离定向复验另为：

```text
5 passed in 11.84s
```

因此冻结核心契约没有被本轮测试隔离修复破坏。

## 7. Remaining External Blockers

下列项目不属于本轮测试债，继续保持原计划状态：

```text
API Key Rotation
Canary Observation Window
Booking Provider
```

因此 `PRODUCTION_RELEASE_GATE_PASS=false`，即使测试 Gate 已经归零，也不能据此
宣称对外发布可行。

## 8. Final Git State

本次代码/测试/文档路径已按 pathspec 提交；工作树当前剩余的是此前会话生成的
`data/**`：

```text
data status entries = 442
tracked modified    = 227
untracked           = 215
```

这些文件没有被本轮提交或清理。严格按原计划的“工作区 clean”门槛，当前：

```text
WORKTREE_CLEAN=false
PROJECT_INTERNAL_CLOSURE_PASS=false
FINAL_RC_TEST_DEBT_CLOSURE_PASS=false
```

待 data 变更的所有者确认“提交、归档或删除”后，只需重新核对
`git status --short`；在不改变代码的前提下即可重新签发项目内部闭环。未经确认，
本报告不擅自执行破坏性清理。

## 9. Next Action

唯一未完成的内部动作是处理上述 `data/**` 归属并重新验证工作树 clean；测试债、
失败分类、flaky/挂死结案、全量归零和核心冻结复判均已完成。外部发布阻断按 §7
继续由对应 owner 处理。
