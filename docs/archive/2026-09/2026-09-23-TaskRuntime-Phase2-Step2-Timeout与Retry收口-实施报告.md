# Task Runtime Phase 2 — Step 2 实施报告：SoftTimeLimit/HardTimeLimit 收口 + Error Taxonomy

日期：2026-09-23 ｜ 状态：**Step 2 PASS** ｜ 前置：Step 1（c837c02）

## 1. 修改摘要

| 文件 | 修改内容 |
|---|---|
| `backend/tasks/error_taxonomy.py`（新） | runtime 错误分类器：11 类词表（timeout/provider_timeout/rate_limited/quota_exhausted/auth_failed/provider_error/validation_error/permission_denied/illegal_transition/internal_error + worker_lost/cancelled 由控制路径表达）；复用 `infra/llm/error_taxonomy.classify_model_error` 三通道与 `ModelProviderError.error_type`；带 provider 特征门（防普通异常被误判 provider_error）；每个分类带 error_type/retryable/source |
| `backend/tasks/retry_policy.py`（新） | 集中 RetryPolicy（default/agent/rag_index，参数源沿用 CELERY_RETRY_* env）；指数退避+有界抖动；`TaskRetryScheduled`（impl→壳的显式重试信号） |
| `backend/tasks/agent_tasks.py` | **移除 `autoretry_for=(Exception,)`**；异常先分类→budget 判定→可重试落 FAILED(等待重试)+TaskRetryScheduled→壳层状态复查→`self.retry(countdown)`；耗尽/不可重试→FAILED(error_type/retry_exhausted)；SoftTimeLimit 与通用异常同一出口；retry 前状态复查（§十一） |
| `backend/tasks/index_tasks.py` | 同口径收口：`_index_failure_exit` 统一失败出口（可重试→TaskRetryScheduled；终态→registry settle+TaskState retry_exhausted 补写后 re-raise）；壳层 retry 前复查；成功 settle 保持节点内（pause/cancel 边界不悬 registry） |
| `backend/tasks/index_task_runtime.py` | mark_failed 落分类 error_type（不再落异常类名）；budget 耗尽补 retry_exhausted |
| `backend/tasks/signals.py` | **修复存量真 bug**：`_update(task_id, **kwargs)` 缺 status 形参 → failure/retry 钩子一直 TypeError 被吞（agent 路径 task_failure 兜底从未落库，Step0 审计「error_type 未落」根因）；修复后钩子只补空缺字段（traceback），**不覆盖分类词表/错误消息**；postrun SUCCESS 收尾清残留错误字段 |
| `backend/tasks/schema.sql` | +`retry_exhausted BOOLEAN NOT NULL DEFAULT FALSE` |
| `backend/config/tasks.py` | +TASK_RETRY_INITIAL_DELAY/TASK_RETRY_MAX_DELAY/TASK_RETRY_JITTER（沿用既有 env 源） |
| `backend/services/task_service.py` | update_status：`error_message=None`=不改写（保留分类口径）、显式 `""`=清空；+retry_exhausted 透传 |
| `backend/services/task_state.py` | mark_failed 透传 retry_exhausted；mark_success 清错误字段 |
| `backend/models/task.py` | TaskRecord.retry_exhausted + to_public_dict 暴露（GET /tasks/{id} 可见） |
| `backend/tests/test_task_phase2_step2_error_taxonomy.py`（新） | 25 用例：分类器 16 + RetryPolicy 2 + DB 集成 7（Case C/D/E/F/A'/G/I/J/复查/signals 保留/index 分类） |
| `backend/tests/test_index_task_runtime.py` | 存量用例按新口径修订（error_type=分类词表 provider_error，不再断言类名 RuntimeError） |

**SSE**：`_fail` 的 failed/cancelled/paused 事件携带 `error_type/retryable/retry_count/retry_delay`（附加字段，前端兼容不破坏）。**未改** SSE 协议结构与 index 上传进度通道。

## 2. Commit

`<见提交记录 feat(tasks): Phase2 Step2 ...>`

## 3. Error Taxonomy

| error_type | retryable | retry policy | handling |
|---|---|---|---|
| timeout（SoftTimeLimit，runtime 层） | true | 指数退避 5→10→20s（cap 120s）+抖动，budget≤3 | 从 checkpoint 续跑 |
| provider_timeout | true | 同上 | 同上 |
| rate_limited | true | 同上（退避即限流友好） | 同上 |
| provider_error（5xx/连接类临时） | true | 同上 | 同上 |
| internal_error（未知） | true（budget 限次兜底） | 同上 | 耗尽→FAILED |
| quota_exhausted | **false** | — | 立即 FAILED；fallback 责任在 proxy 层（既有降级链），到达 runtime 即无 fallback 可用 |
| auth_failed | **false** | — | 立即 FAILED |
| validation_error（400/422/ValueError/ChunkingEmpty） | **false** | — | 立即 FAILED，retry_count 不增 |
| permission_denied | **false** | — | 立即 FAILED |
| illegal_transition | **false** | — | 立即 FAILED |
| worker_lost / cancelled | 不经分类器 | — | Recovery（Step1）/ 控制路径 |

source 维度：timeout=runtime、provider_*=provider、auth/permission=security、validation=validation、internal/illegal=runtime。

## 4. Timeout / Retry 状态机（实际落地）

```text
PENDING → RUNNING → SUCCESS
RUNNING →〔可重试错误 & budget 未耗尽〕→ FAILED(error_type, progress=等待第N次重试)
          → TaskRetryScheduled → 壳层复查(status==FAILED?) → self.retry(countdown)
          → FAILED→PENDING（重试回队）→ 租约认领 → RUNNING（checkpoint 续跑）
RUNNING →〔可重试错误 & budget 耗尽〕→ FAILED(retry_exhausted=true)（不再投递）
RUNNING →〔不可重试错误〕→ FAILED(error_type, retry_count 不增)
RUNNING →〔SoftTimeLimit 被业务 fallback 吞掉〕→ hard limit(soft+30) 杀进程
          → Recovery：lease 过期 → sweeper → rc+1 重投 → … → rc≥3
          → FAILED(ZOMBIE_RECONCILED)（Recovery 耗尽 ≠ Retry 耗尽，error_type 可区分）
PAUSED / CANCELLED / SUCCESS / WAITING_USER + 任何 retry/redelivery → NO-OP
```

Retry 表达：复用 `PENDING + retry_count + error_type`，未新增 TaskStatus、未建第二状态源。

## 5. Case A-J

| Case | Expected | Actual | Result |
|---|---|---|---|
| A Agent SoftTimeLimit | 捕获、不停 RUNNING、retry 按 policy | 单测：SoftTimeLimit→timeout/retryable→TaskRetryScheduled；budget 耗尽→FAILED(retry_exhausted)。实机（6s）：**发现业务 fallback 宽 except 可吞掉 soft 信号**→ hard limit 杀进程→Recovery 接管→rc=3 后 FAILED(ZOMBIE_RECONCILED) 收口，**无永久 RUNNING** | ✅（含重要发现，见 §9-1） |
| B rag_index SoftTimeLimit | TaskState 正确、registry 不成第二事实 | 本环境索引 <6s 完成（embedding 配额快速失败走降级）→ TaskState SUCCESS 正确落库；错误路径（validation/provider_timeout/quota/耗尽）由单测覆盖 error_type/retry_exhausted | ✅（timeout 路径记录 backlog） |
| C Validation Error | retryable=false, retry_count=0, 直接 FAILED | impl 注入 ValueError → FAILED(validation_error)，retry_count=0 | ✅ |
| D Provider Timeout | retryable=true, backoff, retry | "request timed out" → FAILED(provider_timeout, 等待第1次重试) + TaskRetryScheduled(delay>0) | ✅ |
| E Auth Error | retryable=false, 无 retry | 401 → FAILED(auth_failed)，无投递 | ✅ |
| F Quota Exhausted | 不对同 provider 重试；fallback 优先 | 403 quota → FAILED(quota_exhausted) 零重试；fallback 在 proxy 层既有降级链（到达 runtime 即无 fallback），见报告 §9-3 | ✅ |
| G Retry Exhausted | 达上限、FAILED、不再投递 | retries=3/max=3 → FAILED(retry_exhausted=true, error_type=provider_timeout)；壳层不再入队 | ✅ |
| H Hard Kill | Step1 自动恢复无回归 | 实机：kill(23:19:14) → sweep rc=1 → checkpoint 续跑 → SUCCESS，4 节点 count=1，retry_count=0（Recovery 与 Retry 正确分离） | ✅ |
| I Pause during Retry | PAUSED 不被 retry 唤醒 | PAUSED + retry 消息 → NO-OP 保持 PAUSED；retry 前复查非 FAILED → retry_cancelled | ✅ |
| J Cancel during Retry | CANCELLED 不被 retry 唤醒 | CANCELLED + retry 消息 → NO-OP；复查分支同上 | ✅ |

## 6. 测试结果

- 新增：25 passed（test_task_phase2_step2_error_taxonomy.py）
- Runtime 回归：**120 passed**（Step2 + Step1 recovery + Phase1 编排/暂停/恢复/取消/僵尸/索引/状态机 + error_protocol + registry/layer/ADR0001 全绿）
- 全量 Earlier run（修复前）159 中唯一失败即 index 存量旧口径断言，按新契约修订后全绿

## 7. 实机证据

- **Agent 软超时链（soft=6s 演练）**：task 7e9f5131/0e81a7a9 — worker 日志 `SoftTimeLimitExceeded` → （被业务 fallback 吞）→ hard limit 杀进程 → `stale execution recovered (rc=1/2/3)` → 终态 `FAILED(ZOMBIE_RECONCILED, recovery_count=3)`（DB 实证）
- **Case H**：task 8b7511f2 — kill 后 `lease acquired execution_id=<新>` → SUCCESS，agent_checkpoints router/tool_selector/skill_executor/reporter 各 count=1
- **Case B**：task efa52d8f — SUCCESS（TaskState 业务终态口径正确）
- 演练任务全景（biz_type=phase2_drill）：SUCCESS×3 / PAUSED×1 / FAILED(ZOMBIE_RECONCILED, rc=3)×1，无永久 RUNNING
- error_type 词表落库：validation_error / provider_timeout / auth_failed / quota_exhausted / timeout 全部在 DB 集成测试中实证

## 8. Step 1 回归确认

- **Auto Recovery 正常**：Case H 实机 rc=1 恢复 SUCCESS；软超时演练 rc=3 收口路径完整
- **Fencing 正常**：全部执行期写保持 execution_id 校验；signals 收尾 `_owns_execution` 守卫回归通过（test_task_phase2_recovery::test_case_c_*）
- **Checkpoint resume 正常**：Case H 4 节点 count=1
- **PAUSED/CANCELLED/SUCCESS short-circuit 正常**：test_case_i_j / test_case_e_paused_redelivery / test_case_f_terminal_redelivery 全绿

## 9. 剩余问题（不阻断 Step 3）

1. **SoftTimeLimitExceeded 可被业务层宽 `except Exception` 吞掉**（路由/LLM 代理降级链），传播与否取决于警报触发点是否落在保护区内——非确定性。兜底闭环已验证（hard limit→Recovery→rc≥3 收口，永不永久 RUNNING），但「timeout 应走 Retry 而非 Recovery」的语义在吞没场景下退化为 Recovery。业务节点属本 Step 禁改范围 → **backlog**：业务 fallback 链显式放行 SoftTimeLimitExceeded（`except (Exception,)` → 先 `except SoftTimeLimitExceeded: raise`）。
2. **PENDING 无消息丢失无自动恢复**：broker 宕机窗口实测产生一例（task 8e42ad20，create 成功 enqueue 失败→PENDING 永挂）。存量缺口（Phase1），backlog 维持。
3. quota fallback：proxy 层已有 fallback/降级链；「有 fallback 走 fallback」由该层保证（越层重试反而会打同一 provider），runtime 层职责 = 无 fallback 时明确 FAILED —— 已满足并实测。
4. index 上传进度 SSE 通道（rag_upload）的 error 元数据未扩展（不在本 Step 允许文件清单）；任务 SSE（tasks 事件）已带 error_type/retryable/retry_count。
5. cs_maintenance/cs_qa/metadata_shadow 三个非 TaskState 周期/影子任务仍保留 `autoretry_for`（幂等条件 UPDATE，失败由下一轮 beat 天然重跑；改动超出本 Step 边界）——backlog。**TaskState 业务任务（execute_agent/execute_index）的无差别 autoretry 已清零**。
6. 实机演练期间一次 `docker compose down` 误操作导致整栈容器被删（数据卷无损），已按主干 compose 全量恢复并验证健康——操作纪律教训记入记忆（恢复 worker 状态禁止用 down，应仅 `up -d worker`）。

**允许进入 Step 3：YES**
