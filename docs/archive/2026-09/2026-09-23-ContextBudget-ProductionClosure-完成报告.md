# Context Budget Production Closure · 完成报告（STOP A→E）

> 2026-09-23。上轮「生产加固」（STOP A→D，`780891d`/`050cce5`/`310b4bf`/`89fc8f2`）完成架构与测试；本轮完成**生产接线、migration 落地、真机验收、真实模型 Golden、基线冻结**。

## 1. Verdict

```
CONTEXT_BUDGET_PRODUCTION_CLOSURE_PASS=true
CONTEXT_BUDGET_CORE_FROZEN=true
```

## 2. Commit

| STOP | Commit | 内容 |
|---|---|---|
| A 审计 | `1edc6e0` | 生产接线审计：044 未落库实证（L5 静默失效）、三 hook 接线点定案、主链豆包差异记录 |
| B 接线 | `69b2f4e` | 044 落地 + RAG 证据 RAGBudgeter 接线 + PinnedContext 内容锚定/请求级通道 + L5 extra_facts 贯通 + 27 新测试 |
| C 实机 | `819776d` | 双 Worker 真 Redis/PG 验收：单飞锁竞争、CAS race、fail-open、SIGKILL TTL 释放、红线指纹、L2-L5 真实指标 |
| D Golden | `9c9df87` | 真实模型（豆包）Golden 28 例零回退 + 标定估算系统性低估的数据驱动修正 |
| E 冻结 | 本提交 | 配置收口（.env.example 7 项补齐）+ 告警接入（6 条规则）+ 生产基线 + 冻结声明 |

## 3. Migration

- **044 已在目标库实际执行**（正式入口 `docker compose run --rm db-migrate`，非手工改 schema）。
- 证据：`schema_migrations` 记录 `044...applied`；`information_schema` 列存在（integer NOT NULL DEFAULT 0）；存量 434 行全部回填 0；**二次执行幂等**（skipped—记录一致，rc=0）；运行容器探针从 `UndefinedColumn`（STOP A 实证的生产 L5 静默失效）变为 `PROBE_OK`，app 无需重启。
- **新踩坑（已记录）**：`db-migrate` 镜像滞后于 044 提交导致首次 run 静默跳过——「脚本在 git 里」≠「镜像里有」≠「库已执行」，三步都要验。重建 `agent-db-migrate` 镜像后成功。
- rollback：不必要（纯增量列；如需 `DROP COLUMN summary_version`）。

## 4. Hook 接线（最终落点）

| Hook | 落点 | 说明 |
|---|---|---|
| rag_scores / rag_sources | `rag/chain.py` 证据 token 预算裁剪处（rerank→Gate→版本过滤后的最终 docs）→ `RAGBudgeter.budget_rag_indices` | 真实 `rerank_score`/`source_file` metadata 驱动；无分整体回退原序（=旧行为）；kept 非前缀用下标回映。proxy 层 rag_context 形参保持不接（证据已 stuff 进 messages，避免双算） |
| predicted_extra_tokens | **不做生产接线**：`HOOK_SUPPORTED_BUT_NO_RELIABLE_PRODUCTION_SOURCE` | tools schema/response_format 已由 preflight 自动计入（防双算），其余实质内容均在 messages 内；回归锚测试已加（test_rag_hook_wiring） |
| PinnedContext | `manager.prepare_llm_context(pins=)` 新形参 + `_trim_semantic` 并集消费 + `pin.py` 内容锚定（`mark_content_match`，≥4 字符、每值最近 K=3 条）+ 请求级 ContextVar 通道；`cs_state_loader_node` 置位（pending_action.target_id → PIN_CONFIRMATION、ContextResolver last_order_id → PIN_ENTITY）；`run_expert_safely` 补 `copy_context` 传播 | 生命周期=请求级（order A→B 下轮重注册自然 supersede）；L5 侧 `extra_facts` 从同一注册进入 ProtectedFactRegistry（manager._run_l5 / run_auto_compact_async 已贯通） |

## 5. Multi Worker（真机）

- 环境：2 个独立 FastAPI worker 容器（同镜像、宿主 8002/8003）+ 真 Redis（agent-redis-1）+ 真 PG（agent-postgres-1，044 已应用）。生产容器 agent-app-1 全程未动。
- **C2 单飞锁**：同 session 双 worker 并发 L5 → A success（真 LLM 4349+257 tok、10.1s、through=3117、version 0→1）/ B `lock_conflict` 0.21s 携裁剪结果返回，**聊天零阻断**。
- **C3 CAS race**（人为绕锁 + 慢方延迟 20s）：B 先提交 → A 后提交 `stale_waterline` 丢弃 → **DB 终态保持 B 的新摘要（through=3117、version 仅 +1），旧结果绝不回写**。
- **C4 Redis 故障**：死端点注入 → 告警日志 + 降级放行，摘要照常落库（CAS 兜底）；换正确地址即刻恢复，**无需重启**。
- **C5 摘要中途 SIGKILL**：锁键 TTL 52s 自然过期（无永久锁）、DB 无非法水位线、幸存 worker 立即正常 L5。
- **C6 红线**：动测前后 chat_messages 24 条聚合指纹**逐字节一致**（新增 4 条为聊天链路正常落库，与预算系统无关）。
- **C7 指标**：success/lock_conflict/stale_waterline/L2/L4/protected_facts/autocompact_llm_tokens 全部真实非零。

## 6. Golden（真实模型）

- 载体：现有 golden 双轨评测（真实建库 → baseline 原始历史问答 → 真实 run_incremental_summary（qwen3.8-flash 真 API）→ after-L5 投影问答），主链回答与基线问答 = **doubao-seed-2.0-mini**。
- 结果：**28/28 例，fact_retention_rate=1.0、protected_fact_recall=1.0、patched_ratio=0.0（54 事实全部 LLM 原生保留）、constraint_violation=0；after-L5 与 baseline 零回退**（2 例双轨同「未过」为答案措辞差异，非压缩损失）。
- 延迟/成本：摘要 avg 2.32s / p95 3.08s；455+104 token/例；投影 avg 86 token；全量 205.9s。
- 覆盖口径：9 场景中 tool_protocol/dependency/rag_evidence/injection/model_switch 为确定性判定，以单测+STOP B/C 为权威（不耗 API）；长对话 10/20/35/50 轮：E2E 全图观测到主链自然 usage≈1.0-1.8k（L4/L5 天然不触发=设计行为，如实记录）；上下文层直测真实 L2/L4（35 轮 6592→1025 省 83%；50 轮 9697→1025 省 83%，零 LLM）+ STOP C 真 L5。

## 7. Token Estimator Calibration（豆包真实 usage 回测）

- 修正前（ASCII 0.33×1.10）：cjk +25.5% / ascii **−36.5%** / mixed **−39.7%** / cjk-long +16.5%，MAPE 29.6%，**系统性低估**（任务定义的危险方向）。
- **数据驱动修正**：`_CALIBRATION_DEFAULT` ASCII 0.33→0.75（custom-* 自建 provider=生产豆包命中处；deepseek/qwen/ollama 条目无新数据不动）。
- 修正后复测（真实 API 同批样本）：+30.0% / +42.6% / **+1.9%** / +22.7%——**低估计数=0**，全部落在安全高估方向；MAPE 24.3% 为利用率换正确性（符合「正确性>压缩率」）。
- provider 返回 usage：**有**（response_metadata.token_usage，本轮全部对照数据即来源于此）；当前配置（预算 7168 ≪ 豆包窗口 ≥128k）无真实溢出风险，修正为窗口放大前的隐患收口。
- VL 1024 token/图：维持观察，未改。

## 8. Metrics

- 生产路径真实数据：STOP C 全部指标非零（§5/C7）；golden 双轨产生 protected_facts/autocompact_llm_tokens/L2 等真实序列。
- **告警已接入现有 Prometheus 规则体系**（docker/prometheus-alert-rules.yml，第 5 组 `agent-platform-context-budget`，YAML 校验通过）：`ContextBudgetOverflow`(critical) / `ContextL5FailureRateHigh` / `ContextL5StaleWaterlineSpike` / `ContextL5LockConflictSpike` / `ContextCompactionLatencyHigh` / `ContextTokenCounterFallback`。指标名/类型已逐一与代码核对（Counter/Histogram+bucket）。

## 9. Regression

| 套件 | 结果 |
|---|---|
| tests/context_budget + tests/memory | **200 passed**（含 STOP B 新增 27） |
| tests/orchestration | **631 passed** |
| tests/customer_service | 986 passed + 1 顺序性 flaky（单文件 15/15 过） |
| tests/rag | 765 passed；16 失败经**干净 HEAD worktree 复现**证实为存量基线红（embedding fixture 缺字段/subprocess 环境/PG 状态依赖），与本轮 diff 无关 |
| 契约门四件套 | 36 passed |
| golden 全量 | 28/28 零回退（真实 LLM） |

## 10. 生产基线（Context Budget Production Baseline，冻结）

| 项 | 冻结值 |
|---|---|
| 主链模型 / 窗口 | doubao-seed-2.0-mini（custom provider，driver=openai）；预算窗口 = min(LLM_CONTEXT_LENGTH=8192, llm_models.context_length=未填) = **8192** |
| input budget | 8192 − 768(output) − 256(safety) = **7168** |
| Token counter | calibrated 默认 **(0.75, 0.75) × 1.10 margin**（2026-09-23 豆包回测修正）；openai→tiktoken；deepseek/qwen/siliconflow (0.70,0.30)、ollama (0.75,0.33)；native 钩子预留 |
| L1 | 768 / 256（tool inline/preview） |
| L2 | memory 侧 HISTORY_TOKEN_BUDGET=2048；preflight 语义 pin（system/最后 Human/活跃 tool 对）+ 内容锚定业务 pin（≥4 字符、每值 K=3、请求级生命周期） |
| L3 | PREVIOUS_OUTPUTS_MAX_TOKENS=1024，dependency-aware（终局可达/下游数/能力加权） |
| L4 | trigger 0.80 / target 0.65 / keep 4（滞回 4→1）；实测收缩 83%，零 LLM 可回滚 |
| L5 | trigger 0.90 / target 0.70 / lock TTL 60s / timeout 30s / min-delta 2 / max-delta 200 / facts 上限 40 / 模型 qwen3.8-flash（role=context_compactor）/ temp 0.2 / max 512 |
| RAG 证据 | EVIDENCE_TOKEN_BUDGET=3000 → RAGBudgeter（score 优先 + 每 source ≤3 条多样性 + 无分回退原序） |
| 并发 | Redis 单飞（cache 实例 allkeys-lru，键 `context:l5:{tenant}:{session}`）→ PG CAS（summary_through_message_id + summary_version，044）→ fail-open |
| 可用性实测 | 锁竞争/绕锁 CAS/fail-open/SIGKILL-TTL 全过（STOP C）；golden 28 零回退（STOP D） |

**冻结含义**：后续不新增 L6、不主动重构 L1-L5、不凭感觉调 threshold、不因 edge case 增加新 LLM 压缩层。仅以下之一可重开核心设计：①生产 overflow 有证据（`ContextBudgetOverflow` 告警）②Golden quality regression 有证据 ③provider context/tokenizer 重大变化 ④出现新消息协议（ToolMessage/tool-call 入历史）⑤新模型窗口行为无法被 Registry 表达。

## 11. 剩余风险（如实）

1. **标定估算仍非官方口径**：修正后高估方向 MAPE 24.3%（ASCII-heavy 利用率损失 30-40%）；`tokenizer_id` 原生钩子已预留未启用。
2. **`llm_models.context_length` 全表 NULL**：模型注册窗口从未生效，预算恒等于配置窗口 8192（对豆包保守安全）；若未来放大 LLM_CONTEXT_LENGTH 依赖注册窗口保护，需先补填。
3. **主链自然流量 L4/L5 触发率≈0**：memory 侧 2048 + 回答调用近窗注入使防线只对超大注入/工具输出生效——按设计，但意味着这两层的生产样本稀少，依赖单测与直测持续护航。
4. **豆包上游别名 usage 拆分**（STOP A §0，模型治理域，未修）：llm_usage 存在上游真名记录且 provider 误标 ollama，观测聚合受影响，不影响预算正确性。
5. **E2E 事实探针路由混淆**：事实型问题全量路由进 RAG，会话记忆型问答需路由域支持（golden 双轨已覆盖压缩层质量）。
6. **验收容器镜像滞后于 STOP B 提交**（CAS/锁/preflight 代码一致，B4 pins 不在镜像内）：B4 的生产端生效以代码+单测+契约门为准，随下次镜像构建进入运行态。

## 12. 严禁事项核对

未新增 L6；未重写 ContextBudgetManager/LLMProxy；未修改 canonical chat_messages（指纹实证）；未把用户历史包装成动态 SystemMessage；未去掉 Redis fail-open / CAS；未把 semantic pin / dependency ranking 改成 LLM；未重写 RAG/Memory；未混入其他会话文件（工作区 Phase2 会话的 metrics/tasks/task_executor 改动全程未触碰）。
