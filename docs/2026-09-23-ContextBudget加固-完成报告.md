# Context Budget 生产加固 · 完成报告（STOP A→D）

> 2026-09-23。Verdict：**CONTEXT_BUDGET_HARDENING_PASS=true**（含 §十 剩余风险，如实列明）。
> 提交：STOP A `780891d`（审计）/ STOP B `050cce5`（P0）/ STOP C `310b4bf`（P1）/ STOP D `89fc8f2`（P2）。

## 1. Verdict

```
CONTEXT_BUDGET_HARDENING_PASS=true
```

## 2. 修改文件（逐文件）

| 文件 | 修改 | 为什么 |
|---|---|---|
| `context_budget/token_counter.py`（新） | TokenCounterRegistry：openai→tiktoken(compatible/精确)，deepseek/qwen/siliconflow/ollama→CJK 标定估算(estimated×1.10 margin)，无模型→fallback；`resolve_model_context_window`=min(配置, llm_models.context_length)；tools/response_format/多模态计数；token 级截断 | P0-1：tiktoken 不是所有模型的真实计数器；tools schema/图片此前不计 |
| `context_budget/role_safety.py`（新） | 固定 policy 文本 + `<historical_context>` 数据块构造/识别 + 闭合标签消毒 | P0-2：用户历史摘要不得以 SystemMessage 权重回 prompt |
| `infra/llm/proxy.py` | `_preflight_context` 挂进 invoke/ainvoke/stream/astream 四包装 + `_BoundLLMProxy`（bind_tools schema token 懒缓存计入预算）；`get_request_model_name()` | 审计 #1：preflight 原只挂 `__call__`（业务层 0 使用）→ L4/L5 生产死码 |
| `context_budget/manager.py` | 预算=min(配置窗口,注册窗口)-预留-extra_reserved；L2 语义 pin 裁剪；L4/L5 predicted usage 触发 + target 滞回循环（keep 轮数 4→1 逐档下探）；RAGBudgeter 接入 hard trim；分项用量 gauge；ContextVar 守卫替代 threading.local | P1-2/P2-1/P2-2/P2-4 |
| `context_budget/auto_compact.py` | `save_summary_state` CAS（expected_through + version+1）；`run_incremental_summary`：L5_ACTIVE ContextVar 守卫 + Redis 单飞锁 + CAS 冲突丢弃（stale_waterline）+ extra_facts 钩子（ProtectedFactRegistry）；fold_rebuild 用 role_safety + 溯源 meta | P1-1：多 worker 旧摘要覆盖新摘要 |
| `context_budget/pin.py`（新） | atomic groups + 语义 pin（System/最后 Human/活跃 tool 对自动 pin；PinnedContext 显式确认态/实体 pin） | P1-2/§十一：位置假设 + 拆散 tool 对 |
| `memory/token_budget.py` | 计数委托 token_counter；trim 重写为原子组+pin（签名向后兼容） | 单一计数接缝；协议完整性 |
| `memory/service.py` | L2 摘要注入改二元组（policy + historical_context AIMessage） | P0-2 同根因 |
| `context_budget/micro_compactor.py` | priorities 参数 + `compute_dependency_ranks`（终局可达/下游数/能力加权，零 LLM）+ SQL/RAG/业务分型降级（白名单 key picking）+ L1 preview 标记保留（context_compacted/type） | P1-3/§十三 |
| `context_budget/rag_budgeter.py`（新） | score 优先贪心 + source diversity 上限 + 无分数回退原序 | P2-1：机械尾删不安全 |
| `context_budget/fact_registry.py`（新） | ProtectedFactRegistry：多来源（regex/business/tool/domain/constraint/confirmation）+ critical 优先 + validate_and_patch | P2-3：事实库不依赖 LLM 记忆 |
| `context_budget/collapse.py` / `role_safety` | L4 投影 = policy + 数据 AIMessage（fold_id/source_range 入 additional_kwargs） | P0-2/§十九 |
| `orchestration/supervisor/scheduler.py` | Send 注入前按 `compute_dependency_ranks` 传 priorities | P1-3 接线 |
| `sql/migrations/044_chat_sessions_summary_version.sql`（新）+ `scripts/init_db.py` | chat_sessions.summary_version（DEFAULT 0 回填）+ MIGRATION_TARGETS 登记 | CAS 乐观并发 |
| `config/memory.py` / `config/__init__.py` / `observability/metrics.py` | 新增 CONTEXT_TOKEN_ESTIMATION_MARGIN/IMAGE_ESTIMATE/MESSAGE_OVERHEAD/L4_TARGET_RATIO/L5_LOCK_TTL；context_token_counter_total / context_tokens_by_component | §二十/§二十七 |
| `tests/context_budget/` | 新增 test_stop_b_p0(20)/test_stop_c_p1(21)/test_stop_d_p2(12)/test_golden_cases(10)；适配 test_collapse/test_auto_compact/test_micro_compactor | §二十二 |

未动：GraphRunner、RAG 链、SQL Agent、SSE、原始 chat_messages 写路径。禁止事项全部遵守（未新增 L6、未把压缩改 LLM、未重写 proxy）。

## 3. 架构变化

```
修改前：
Canonical chat_messages → [preflight 仅挂在无人使用的 __call__] → llm.invoke 直通 LLM
L5: UPDATE ... WHERE session_id（后写者胜）   投影/摘要 = SystemMessage(动态内容)
修改后：
Canonical chat_messages（永不改写）
  → 每次消息形态 LLM 调用前 preflight（invoke 族 + bind_tools 全覆盖）
      budget = min(配置窗口, llm_models.context_length) − 输出预留 − 安全余量 − tools/response_format
  → L2 语义 pin 裁剪（tool 对原子） → L3 依赖感知分型压缩
  → L4 折叠（policy SystemMessage + <historical_context> AIMessage，target 滞回）
  → L5 Redis 单飞 + CAS 水位线 + ProtectedFactRegistry（失败安全回退）
```

## 4. Token Budget（当前 .env 主链 DeepSeek）

| 项 | 值 |
|---|---|
| 目标模型 | deepseek-chat（请求覆盖/角色绑定随请求解析） |
| context window | min(LLM_CONTEXT_LENGTH=8192, llm_models.context_length)；未登记→8192 |
| token counter | deepseek→calibrated(CJK 0.70/ASCII 0.30 token/字符) ×1.10 margin，estimated=true；openai→tiktoken 精确 |
| tool schema cost | bind_tools 构造期折算并缓存，计入预算 |
| output reserve / safety | 768 / 256（复用原配置） |
| usable input budget | 8192−768−256=7168（DeepSeek 注册窗口更小时自动收缩） |

## 5. Message Role 安全

- L4 projection role：policy SystemMessage（进程内常量，逐字稳定）+ **AIMessage**（`<historical_context>` 纯数据）；L5 summary role：同构（AIMessage 数据块）。
- 不发生 user→system 提权的原因：动态内容（用户历史/LLM 摘要）只出现在 AIMessage 标签内；SystemMessage 仅承载固定 policy 声明（untrusted data 边界，一次性声明不重复计费）；闭合标签经 `sanitize_historical_content` 转义防逃逸。测试 D（注入 10 轮触发 L4）验证注入文本不在任何 SystemMessage。

## 6. 并发安全

- Redis 锁 `context:l5:{tenant_id}:{session_id}`，TTL=CONTEXT_L5_LOCK_TTL(60s)>摘要超时 30s，finally 释放（过期静默），获取失败→`l5_total{reason="lock_conflict"}` 放弃本轮；Redis 不可用→降级放行不阻断。
- watermark CAS：`WHERE session_id=%s AND COALESCE(summary_through_message_id,0)=%s` + `summary_version+1`；CAS 失败丢弃当前旧结果（stale_waterline）。
- worker race：进程内单飞 + 跨进程锁 + CAS 三层；递归守卫 ContextVar 随 `copy_context` 传播进 executor 线程。测试 E/F 覆盖锁冲突、降级、CAS race、守卫置位。

## 7. 压缩策略（现职责）

L1 工具 preview（768/256）｜L2 语义 pin 历史裁剪（动态预算，tool 对原子）｜L3 previous_outputs 依赖感知分型压缩（1024）｜L4 折叠（≥80% predicted，target 65% 滞回，零 LLM 可回滚）｜hard trim（history→RAG(RAGBudgeter)→po）｜L5 增量摘要（≥90%，锁+CAS+事实保护，失败回退）。

## 8. 测试结果

```
cd backend && .venv/Scripts/python.exe -m pytest tests/context_budget/ tests/memory -q --no-cov
→ 173 passed（context_budget 155 + memory 18），0 failed
契约门：test_registry_consistency / test_layer_consistency / test_adr0001 → 通过
tests/orchestration（638）STOP C 时点通过
```
关键测试：注入 role 安全（D）、35 轮预算事实恢复（J/H）、CAS race（F）、锁冲突/降级（E）、依赖保留（G）、tool 协议完整性（C）、模型切换窗口（K）、predicted usage、L4 keep 4→1 滞回、L5 逐档重建。

## 9. Golden Evaluation（确定性子集，全绿）

long_history_recall ✅（35 轮后预算事实经 Registry+补丁可恢复）/ protected_fact_recall ✅（6 类最坏摘要全恢复）/ current_query_retention ✅（非尾条形态）/ tool_protocol_integrity ✅（超预算不拆对）/ prompt_injection_role_safety ✅ / dependency_output_recall ✅ / rag_evidence_recall ✅ / model_switch_budget ✅ / l5_concurrency ✅（注入式）/ overflow_rate → metric 已观测。
真实 LLM 双轨质量评测（evaluation/context_budget/golden_eval.py）本轮未重跑，需 API key 与真实模型。

## 10. 剩余风险（如实）

1. **无原生 tokenizer**：DeepSeek/Qwen 为保守标定估算（×1.10），非官方口径；`tokenizer_id` 钩子已留（`_try_native` 暂返回 None）。
2. **VL 图片 token**：固定估算 CONTEXT_IMAGE_TOKEN_ESTIMATE=1024，非 provider 计费口径。
3. **新钩子未接线**：`rag_scores/rag_sources`（RAG 链 rerank 分）、`predicted_extra_tokens`（调用方可传）、`PinnedContext`（域图确认态/实体）机制已生效但生产调用方尚未传值；migration 044 需在目标库执行（幂等脚本已登记）。
4. **真实跨进程演练未做**：锁/CAS 为注入式单测覆盖，未在多 FastAPI worker+真 Redis+真 PG 下联测；`test_llm_bind_tools.py::test_valid_model_with_key_accepted` 为存量环境性红（HEAD 干净树同样失败，与本次改动无关）。
