# 上下文预算管理 Phase 3 实施报告 —— L5 AutoCompact（2026-09-22）

> Commit：`932fe59`（基于路由入口重构 `5eba10f` 之后）
> 本阶段范围：仅 L5 AutoCompact + Phase 2 遗留接线收口，未扩展任何业务功能。

## 〇、Phase 2 遗留：runner.py L2 event flush 接线

实施开始时 `runner.py` 仍混有其他会话未提交的 `set_pending_question` /
`routing_context` / `general_chat` 改动（依赖三个当时 untracked 的新文件），
按红线未单独提交、未覆盖、未偷带。

**收口方式**：并发会话随 `5eba10f`（路由入口重构）把 runner.py 全量落库，
本任务的 `drain_pending_events` flush 接线随该提交一并进入 HEAD
（`runner.py:457-459` 已核实），工作区 runner.py 现已干净。**遗留项闭环。**

## 一、L5 触发链路（最后一道防线）

```
prepare_llm_context()
  → L2 history trim → L3 previous_outputs compact → L4 context collapse
  → calculate_usage()
  → usage_ratio < 0.90 → 正常返回（普通请求永不碰 L5）
  → usage_ratio >= CONTEXT_L5_TRIGGER_RATIO(0.90)
      → 守卫链：L5 开关 / 无会话上下文跳过 / 同会话单飞 / 重入守卫
      → auto_compact：增量摘要（LLM，带超时）
          ├─ 成功 → fold_rebuild 重建 active projection（目标 ~70%）
          └─ 失败/超时/无收益 → 安全回退：沿用确定性裁剪结果，绝不阻断
```

- 触发判定有两个执行点：预算内早退路径（覆盖 0.90~1.0 区间）与 hard trim
  之后的收尾路径（覆盖 >100% 裁剪后仍达阈值）。
- async 事件循环上下文内不做内联 LLM 调用（防阻塞 loop）：降级为
  fire-and-forget 后台摘要，本轮先用裁剪结果、下轮生效（SSE 发
  `action=deferred` 事件）。
- **接线升级**：`proxy._preflight_context` 由自带的简单裁剪切换到统一
  `prepare_llm_context`——L2→L4→L5 全链路从此在生产 LLM 调用路径真实生效
  （Phase 2 时 L4 仅测试可达，本阶段顺带修正）。

## 二、红线遵守情况

- **原始 chat_messages 永远保留**：L5 只写 `chat_sessions.summary*` 水位线
  字段 + 重建本轮发送的 projection，零 DELETE/UPDATE 消息行；
  fold_rebuild 不修改调用方持有的消息列表（有测试断言）。
- **前端历史完整可查**：`get_session_messages` 读原始行，链路未动。
- **不重复总结整个会话**：`summary_through_message_id` 记录摘要覆盖游标，
  每次只把 `(through, boundary)` 区间的新消息合并进旧摘要。

## 三、增量摘要水位线（migration 040，agent_memory）

```sql
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS summary_through_message_id INTEGER;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS summary_token_count INTEGER;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS summary_updated_at TIMESTAMPTZ;
```

- 已在**宿主 5432（测试用原生 PG）与 docker 5433（权威库）双库应用**并核实。
- `scripts/init_db.py::MIGRATION_TARGETS` 已登记（fail-fast 兼容）。
- ORM `ChatSession` 同步加列；`SessionRepository` 新增
  `get_summary_state / summarizable_before_id / load_messages_since /
  update_summary_state`；旧 `update_summary` 保持兼容。
- 保留边界 = 最近 `CONTEXT_L4_KEEP_RECENT_TURNS`(4) 轮（与 L4 语义一致，
  复用同一配置，不为配置而配置）：由「倒数第 N 条 user 消息 id」确定。

## 四、结构化摘要 Prompt 与 ProtectedFacts

- 新 prompt `memory.session.auto_compact`（registry 第 41 个注册，
  `test_total_count` 已更新）：固定小节 `[用户目标]/[已确认事实]/[关键约束]/
  [关键实体]/[已完成]/[待处理]`，10 条维护规则（不推测/不补充/删寒暄）。
- **抽取（deterministic，摘要前）**：规则正则抽取 ProtectedFact(type, value,
  source_message_id)——订单号（含「订单号是 ORD…」「裸 ORD 编号」）、SKU、
  业务 ID、货币金额、百分比、日期、时间、URL、版本号、错误码、数量+单位；
  去重保序、上限 `CONTEXT_L5_MAX_PROTECTED_FACTS`(40)。
- **注入（摘要中）**：facts 清单以「必须原样保留」段落进 prompt。
- **校验（deterministic，摘要后）**：逐条 substring 校验，遗漏的**追加**到
  摘要尾部 `[关键实体]` 小节——零额外 API 调用（有测试
  `test_missing_facts_patched_after_llm`）。

## 五、安全回退（全部有测试锁定）

| 场景 | 行为 |
|---|---|
| LLM 超时（默认 20s，线程池 future） | 算失败：返回 None，旧摘要+旧水位线不动 |
| LLM 异常/空返回/落库失败 | 同上，`degradation_alerts_total(code=context_autocompact_failed)` |
| delta < `CONTEXT_L5_MIN_DELTA_MESSAGES`(2) | 不值得一次 LLM 调用，跳过 |
| 无新增可摘要区间（boundary<=through） | 跳过，返回现有摘要 |
| 无会话上下文（default/测试/后台） | 不触发（水位线挂在 session 上） |
| async 上下文 | 后台摘要，本轮降级继续 |
| 同会话并发触发 | 单飞锁，第二个请求先用裁剪结果 |
| 摘要 LLM 自身的 preflight | 线程本地重入守卫，禁止递归 L5 |

## 六、观测（成本/延迟/token 节省）

- `context_compactions_total{level="L5",action="auto_compact"}` +
  `context_tokens_saved_total{level="L5"}`（复用既有低基数指标）
- 新增 `context_compaction_latency_seconds{level}` Histogram（L5 含 LLM 调用，
  buckets 到 20s）
- 新增 `context_autocompact_llm_tokens_total{kind=prompt|completion}`
  （摘要 API 成本，读自 response_metadata.token_usage）
- 结构化日志：`context_compacted level=L5 ...`（through_id/summary_tokens/
  protected_facts/patched/llm_tokens/latency_ms；session_id 只进日志不进 label）
- SSE context 事件：`{level:"L5", action:"auto_compact"|"deferred", ...}`，
  前端既有 notice 组件可直接消费。

## 七、配置（全部在 backend/config/memory.py，业务代码零 os.getenv）

```text
CONTEXT_L5_ENABLED                = true    总开关
CONTEXT_L5_TRIGGER_RATIO          = 0.90    触发阈值（既有）
CONTEXT_L5_TARGET_RATIO           = 0.70    压缩目标比例
CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS= 20      摘要 LLM 超时
CONTEXT_L5_MIN_DELTA_MESSAGES     = 2       增量最小消息数
CONTEXT_L5_MAX_DELTA_MESSAGES     = 200     单次摘要最大 delta
CONTEXT_L5_MAX_PROTECTED_FACTS    = 40      实体保护上限
（保留轮数复用 CONTEXT_L4_KEEP_RECENT_TURNS = 4）
```

## 八、测试

新增 `backend/tests/context_budget/test_auto_compact.py`（22 用例）：
实体抽取/去重封顶、遗漏补丁、fold_rebuild（System/最近轮保留、旧摘要与
L4 投影整体替换、原列表不可变）、增量区间/水位线推进/失败回退、
prepare 触发链路（阈值上触发+重建/阈值下不触发/开关关闭/无会话跳过/
失败回退）、单飞与重入守卫。

```
pytest backend/tests/context_budget/ backend/tests/memory/ backend/tests/prompts/
       backend/tests/test_registry_consistency.py backend/tests/test_layer_consistency.py
       backend/tests/test_adr0001_dual_registry_merge.py
       backend/tests/orchestration/graph/test_router_prefilter_order.py
       -q -p no:randomly --no-cov
→ 244 passed
```

其余：infra 套件 139 passed / 4 failed——4 个失败均为并发会话的
provider 空字段回退语义问题（`test_llm_provider_passthrough` /
`test_model_config_runtime`，api_key/base_url 空值回退断言），与本次改动无关
（本次在 infra/llm 只动 `proxy._preflight_context`）。

## 九、Git

- 本阶段提交：`932fe59`，20 文件 +1277/-57，路径限定 add+commit。
- runner.py 未在本阶段提交（开始时混有其他会话改动）；其 L2 flush 接线最终
  随 `5eba10f` 落库，现工作区 runner.py 干净。
- 兼并修正两个旧断言：`test_l5_not_implemented` →
  `test_auto_compact_explicit_entry`；`test_auto_compact_still_not_implemented`
  → `test_auto_compact_safe_fallback`；prompt registry 总数 39→41
  （baseline 已 40 为并发会话遗留既败断言，41 为现值）。

## 十、遗留与建议（不阻塞）

1. **真实压力验证**：L5 触发需 usage≥90%（7168 预算下即 ~6450 tokens），
   建议用 Phase 2 的 Case C（长对话+多工具+RAG）实机跑一轮，确认摘要质量与
   SSE 事件展示；本阶段未动容器、未做 rebuild（遵守活跃会话约定）。
2. `SessionMemory.needs_summarization` 仍是条数阈值（≥50）触发 end_turn
   摘要；现在该路径已增量化（走同一水位线），如需 token 维度触发可在下阶段评估。
3. ProtectedFacts 第一版为规则正则，误抽/漏抽可在观察 `patched` 计数后调
   `_ENTITY_PATTERNS`。
