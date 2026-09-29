# Context Budget 生产收口 · STOP D 真实模型 Golden Evaluation 报告

> 2026-09-23。真实模型 = **豆包 doubao-seed-2.0-mini**（DB `llm_model_role_bindings` role=main）
> + 摘要模型 **qwen3.8-flash**（role=context_compactor），全部真实 API 调用。
> 复用现有评测框架（`evaluation/context_budget/golden_eval.py` + `run_context_budget_eval.py` +
> `datasets/context_budget_golden.json`），未新造第二套评测。

---

## D1 模型

| 角色 | 模型 | 解析来源 |
|---|---|---|
| 主链（问答/基线/after-L5 问答） | doubao-seed-2.0-mini（上游回显 doubao-seed-2-0-mini-260428） | DB role=main（2026-09-22 绑定） |
| L5 增量摘要 | qwen3.8-flash | DB role=context_compactor + `.env CONTEXT_L5_SUMMARY_MODEL` |

上一轮报告的「主链 DeepSeek」口径已过时（本轮 STOP A §0 记录差异）。DeepSeek 条目 enabled=false，未重跑（不为凑数浪费 API）。

## D2 场景覆盖

| 场景 | 载体 | 结果 |
|---|---|---|
| long_history_recall | golden 双轨（真实建库 + 真实 L5 + 前后对照问答） | **28/28 例 fact retention 双轨一致，0 回退** |
| protected_fact_recall | 同上 + ProtectedFact 计数 | **1.0**（54 个事实全部 LLM 原生保留，patched_ratio=0） |
| current_query_retention | 确定性单测（非尾条形态 last-Human pin）+ golden 每例当前问 | ✅ |
| real_llm_answer_quality | golden 双轨真实回答（豆包） | 见 D4 |
| tool_protocol_integrity | 确定性单测（超预算不拆 tool 对，test_stop_c_p1/test_golden_cases） | ✅ |
| prompt_injection_role_safety | 确定性单测（注入 10 轮触发 L4，注入文本不入 SystemMessage，test_stop_b_p0/test_stop_d_p2） | ✅ |
| dependency_output_recall | 确定性单测（compute_dependency_ranks + Send 注入，test_stop_c_p1 G 组） | ✅ |
| rag_evidence_recall | 确定性单测（RAGBudgeter score/diversity/fallback，test_rag_hook_wiring + STOP B） | ✅ |
| model_switch_budget | 确定性单测（min(配置, 注册窗口) 重算，test_stop_d_p2 K 组） | ✅ |
| l5_concurrency | **STOP C 实机**（双 worker 真 Redis/CAS）+ 注入式单测 | ✅ |

说明：tool_protocol/dependency/rag_evidence/injection/model_switch 五项的判定是**确定性的**（协议完整性/消息角色/下标保留），单测即权威口径，不消耗 API。

## D3 长对话（10/20/35/50 轮）

**E2E 全图观测**（真实 /chat/stream，35~50 轮）：每轮回答调用 payload 稳定在 ~1.0-1.8k token——主链回答调用自身只注入近期窗口 + memory 侧历史预算 2048，**远达不到 7168 预算**，L4/L5 在自然主链流量中不会触发（与 case_c 10 轮 usage_ratio=0.142 一致）。这是**设计使然**（防线只在超预算时介入），如实记录为生产行为基线，非缺陷。另记录：事实型追问经三层 Router 全量路由进 RAG（知识库无内容 → 拒答），E2E 级事实探针存在路由混淆，压缩层事实召回以 golden 双轨为准。

**上下文层直测**（真实 prepare_llm_context 链路 + 真实会话上下文，零 LLM 层即时完成）：

| 轮数 | 估算输入 token | L2 trim | L4 次数 | 最终 token | 延迟 | overflow |
|---|---:|---|---:|---:|---:|---|
| 10 | 1,744 | 0 | 0 | 1,744 | 0.01s | 无 |
| 20 | 3,598 | 0 | 0 | 3,598 | 0.01s | 无 |
| 35 | 6,592 | −583 | **1**（fold-63f8b039082c，58 条折叠） | 1,025 | 0.02s | 无 |
| 50 | 9,697 | −3,682 | **1**（fold-91b7580878cd，56 条折叠） | 1,025 | 0.03s | 无 |

L4 真实收缩率：6009→1025（省 4,984 / 83%）、6015→1025（省 4,990 / 83%），零 LLM、可回滚（fold_id 溯源）。L5 未在同轮触发属设计级联（L4 后 ratio≈0.14 < 0.90）；L5 真实触发证据见 STOP C（真摘要落库 through=3117、ProtectedFacts 20 条保真、豆包会话摘要含「订单 ID: 20260922001」）。

## D4 质量判断（ON vs baseline 同题对照）

golden 双轨即本对照：baseline = 原始全历史直接问答（足够窗口、不经 L5）；after-L5 = 真实增量摘要 + 最近 4 轮投影后同题问答。

| 指标 | baseline | after-L5 |
|---|---|---|
| 关键事实答题通过 | 26/28 | **26/28（与 baseline 完全一致，零回退）** |
| 不该出现的内容（negation/correction） | 全过 | 全过 |
| 约束违反（constraint_violation） | — | **0** |

2 例双轨同「失败」为答案措辞变化（must_contain 串未逐字出现），非压缩损失——摘要层 ProtectedFact 计数与确定校验对同 2 例均无缺失。结论：**Context Budget ON 未造成可测质量回退**。

## D5 性能指标

| Metric | 值（本轮实测） |
|---|---|
| golden 单例摘要延迟 avg / p95 | 2.32s / 3.08s（qwen3.8-flash 真实 API） |
| 摘要 LLM token avg（prompt+completion） | 455 + 104 / 例 |
| 摘要投影 token avg / p95 | 86 / 120 |
| delta_messages avg | 5.9 |
| L4 收缩率（直测 35/50 轮） | 83% / 83% |
| overflow rate | **0**（全部实测路径无一溢出） |
| 全量 golden 总耗时 / 内存 | 205.9s / 178MB 峰值 |
| 主链回答延迟（E2E 轮次） | 8-15s/轮（豆包 + RAG 链路端到端，含网络） |

## D6 重点判断

### 1. L4 80%/65% 是否激进（只看数据）
- 数据：35/50 轮直测中，L4 单次折叠后 ratio 从 0.84 降至 0.14，**无需滞回下探即达 target**；真正消费预算的 po/rag/工具 schema 不受 L4 管辖。
- 主链自然流量（case_c/E2E）usage_ratio ≈ 0.14-0.26，L4 触发率≈0——**不激进也不频繁**；触发即收缩 83%，无震荡。维持 0.80/0.65，不改。

### 2. L5 ≥90% 是否频繁触发（先查原因，不先调参）
- 数据：自然主链（memory 侧预算 2048 + 回答调用近窗注入）使 L5 触发前置条件（单次 payload > 7168×0.90）在常规对话中**不可达**；STOP C 中人为驱动触发的摘要（qwen3.8-flash，4.3k+0.2k token/次）正常。
- 原因即结论：**L5 是深防线**，触发频率由超大 system/RAG 证据/previous_outputs 驱动，前面各层工作正常，threshold 无需调整。

### 3. calibrated estimator vs 豆包真实 usage（本轮最重要发现）
修正前（默认 ASCII 0.33×1.10）四类真实样本回测（doubao-seed-2-0-mini-260428 实际 prompt_tokens）：

| 样本 | 估算 | 实际 | 误差 |
|---|---:|---:|---:|
| cjk-heavy | 556 | 443 | +25.5%（安全） |
| ascii-heavy | 577 | 908 | **−36.5%（低估）** |
| mixed | 590 | 979 | **−39.7%（低估）** |
| cjk-long | 991 | 851 | +16.5% |

MAPE 29.6%，**最大低估 −39.7%，且 ASCII/混合内容系统性低估**（实测豆包非 CJK 比率 0.585-1.0+ token/字符，远高于旧默认 0.363）。低估正是任务定义的危险方向。

**数据驱动的最小修正**（`context_budget/token_counter.py`）：仅 `_CALIBRATION_DEFAULT` ASCII 0.33→0.75（未知 provider 即生产豆包的 custom-* 命中处；deepseek/qwen/siliconflow/ollama 条目无新数据不动）。修正后复测（同批样本真实 API）：

| 样本 | 估算 | 实际 | 误差 |
|---|---:|---:|---:|
| cjk-heavy | 576 | 443 | +30.0% |
| ascii-heavy | 1,295 | 908 | +42.6% |
| mixed | 998 | 979 | +1.9% |
| cjk-long | 1,044 | 851 | +22.7% |

**MAPE 24.3%，低估计数 = 0**——系统性低估消除，全部误差落在安全的高估方向（利用率损失可接受，符合「正确性 > 压缩率」）。当前配置下（预算 7168 ≪ 豆包真实窗口 ≥128k）即使旧系数也不会真实溢出，但窗口配置一旦放大，旧系数即成隐患；本轮按数据收口。

### 4. VL 1024 token/图
维持固定估算仅观察，本轮无 VL 流量，不重新设计（任务口径）。

---

## 回归

- `tests/context_budget tests/memory`：**200 passed**（含系数修正后）
- `test_collapse.py` 两处断言跟进（注明原因）：L4 折叠次数断言 `==1`→`>=1`（滞回深度由 po/rag 占比决定，逐档行为有专项测试）；首折消息数边界 `>=16`→`>=15`（轮边界随夹具字数移动 1 条）
- 其余域回归见 STOP B（orchestration 631 / customer_service 986 / 契约门 36 全绿）

## 结论

```
STOP_D_PASS=true
STOP_E_ALLOWED=true
```

剩余风险（如实）：
1. 高估方向 MAPE 24.3% 意味着 ASCII-heavy 场景预算利用率损失约 30-40%——换取零系统性低估，符合设计优先级；后续若有 provider 官方 tokenizer 可经 `tokenizer_id` 钩子接入（`_try_native` 预留）。
2. E2E 全图事实探针受 Router 路由混淆（事实型问题全量进 RAG），压缩层质量度量依赖 golden 双轨——若未来要 E2E 级探针，需 Router 提供会话记忆型路由，属路由域工作。
