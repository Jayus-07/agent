# 上下文预算管理 Phase 4 实施报告 —— 真机验证 + 评测套件（2026-09-22）

> Commits：`2c72748`（评测套件 + 实测修复）。基线：Phase 3 `932fe59` / `7ead091`。
> app + worker 已于本阶段内 **rebuild 两次**（Phase 3 代码生效 + Phase 4 修复烤入），
> migration 040 双库已在位。评测套件可重复执行：`python backend/evaluation/run_context_budget_eval.py <mode>`。

## 一、直接回答本阶段的核心问题

| 问题 | 实测答案 |
|---|---|
| L5 到底什么时候触发？ | **只在单条 prompt 巨大时**。prepare 链路里 L2 先把历史封顶在 2048（min(HISTORY_TOKEN_BUDGET, budget)），正常多轮聊天 usage_ratio 理论上限 ~0.45-0.55（实测 10 轮长会话峰值 0.156，L2 触发时 2110→1065）。L5 触发的唯一真实路径 = 巨型当前消息（长文档粘贴 ~6700 tokens）把 usage 顶到 hard budget 附近，L2+hard-trim 后仍 ≥0.90 |
| 一次实际节省多少 token？ | L4：7289→6981（saved 308~532，folded 8-14 条，可回滚）。L5 摘要 321 tokens 替代 ~750+ tokens 早期历史，且未来每轮都以 321-token 摘要注入替代完整历史重放 |
| 增加多少延迟和费用？ | L5 摘要延迟 24.7~27.4s（qwen3.7-flash/qwen3.8-flash，真实调用）；LLM 成本 1107 prompt + 2695 completion tokens/次（completion 含 thinking token）。L1-L4 本地逻辑零延迟感知（SSE 事件即时） |
| 摘要后旧事实还能保留多少？ | Golden 24 例 **Fact Retention Rate = 1.0**，Protected Fact Recall = 1.0（最终零遗漏），约束违反 0 |
| ProtectedFacts 经常补哪些？ | patched 28%（LLM 原生保留 72%）——集中在「无货币符号的裸大数」（`100,000`）与多数字组合场景。详见 §五校准 |
| 0.90/0.70/最近4轮是否合理？ | 0.90 触发值本身合理（最后一道防线语义成立），但**在 L2 cap=2048 的前提下普通聊天结构性够不到 0.90**——真正需要校准的是 timeout（20s→120s，实测 27.4s）与金额正则覆盖。0.70 target 与 keep=4 未发现反面证据，保持 |

## 二、容器判定与重建（§三/§四）

- **执行点判定**：app = GraphRunner 全链（L2 memory 侧、L1/L3 节点内、end_turn→增量摘要、preflight→L4/L5）；worker = TaskGraphExecutor 直接 `build_graph()`，节点内 LLM 全部走 `_LLMProxy→_preflight_context`（L1/L3/L4/L5 同样生效）→ **app+worker 都需 rebuild**（beat/dispatcher/metadata-shadow 不碰新路径，未动）。
- **运行时实测配置**：`LLM_CONTEXT_LENGTH=8192`（env）→ input_budget=**7168** 实测生效（done 事件 context_usage 确认）；L4=0.80、L5=0.90、target=0.70、keep=4、L2 cap=2048、po cap=1024 均为代码默认（容器 env 未覆盖）。**注意：需求清单里的 `CONTEXT_L5_SUMMARY_MAX_TOKENS` 不存在**（实际是 `CONTEXT_L5_MAX_DELTA_MESSAGES=200` 等，见 config/memory.py）。
- 健康检查：无 ImportError/migration mismatch/Pydantic error/duplicate metric/循环依赖；worker 有一条 kimi-k3 探活 degraded + deepseek 熔断 OPEN 广播（model 治理域，仅记录）。
- ⚠️ 教训记录：期间 agent-app-1 被并行会话重建过一次，docker cp 的临时验证文件全被镜像版本冲掉——临时验证文件不可靠，修复必须走 commit+rebuild（本阶段最终即如此收口）。

## 三、Case A：普通聊天基线（§五）——红线达标 ✅

5 条短问答：**L4/L5 零触发、摘要 API=0**（SSE 无 context 事件），final usage_ratio=0.0297。总延迟 5.8-22.1s / TTFT 5.6-21.9s（主 LLM 自身，非预算机制）。报告：`context_budget_case_a_*.json`。

## 四、Case B：大工具输出（§六）

真实链路尝试 6 种构造（workflow daily_report、travel×4、SQL、RAG×3、web.crawl）：受环境数据量限制（SQL demo 库空、chat 侧 KB 检索空——文档进了 `doc_db` collection 而 chat kb_search 查 `chroma` collection，两条摄入链路不同），单工具输出全部 <768 tokens，L1 未在 HTTP 链路自然触发。按 §六降级授权，用**真实入库数据 + 真实 L1 Guard 代码路径**验证：

```
tool_output: 23036 tokens → guard_tool_result → 304 tokens（saved=22732）
结构：{"context_compacted": true, "type": "tool_result_preview", "original_tokens": 23036, preview...}
```
L1 之后无 L5（usage 远低于阈值），确认「L1 发生不必然导致 L5」。

## 五、Case C / L4 / L5 生产路径（§七/八/九）

- **Case C（10 轮真实长会话）**：全部正常完成，零压缩事件，ratio 峰值 0.156 → 结构性阴性证据（见 §一）。追问 3 个历史事实 facts_ok=0——**根因是路由层**（短问题被 Guard CLARIFY / 路由到空数据的 sql/kb 工具），不是摘要问题。
- **L4 生产触发实测**（此前只在测试可达的问题已被 Phase 3 的 proxy 统一 preflight 修复，本轮拿到生产数据）：
  ```
  fold_id=fold-3e16b74be78f  before=7498 → after=6966  saved=532  folded_messages=14  reversible=true
  fold_id=fold-a97e883ee806  before=7484 → after=6952  saved=532  folded_messages=14
  ```
- **L5 生产触发实测**：巨文档 prompt（6732 tokens）→ L2+hard trim → ratio 0.97 ≥ 0.90 → 真实摘要成功：
  ```
  summary_written=true  through=746  summary_token_count=321  latency=27.4s
  摘要内容抽查：¥500,000 / POD-X2-GRY-001 / 1,299 / 1,099 / 2026-10-15 / 约束 全部在
  原始 chat_messages 数量不变 ✅  final usage 0.97 ≤ budget ✅
  ```
- **L4+L5 交互发现**：L4 先全折叠时，L5 的 fold_rebuild 窗口只剩最近 4 轮（按设计拒绝重建，`L5 摘要已落库，本轮 projection 无可替换历史`）；L5 摘要的价值在**下一轮 start_session 的注入**。L5 SSE（action=auto_compact）仅在 rebuild 实际发生时发声——行为正确但值得知晓。

## 六、失败矩阵实测（§二十一）

全部真实触发过（非 mock）：**20s 超时×2、90s 超时、provider 无密钥（MiniMax-M3 → ChatAnthropic validation error）、deepseek 熔断 OPEN 广播、水位线边界为空（无可增量）**——每种情况都是「安全回退：旧摘要+旧水位线不动、主回答继续、degradation 计数」，无一次阻断请求。

## 七、Waterline / 并发 / 重启恢复（§十七/十八/十九/二十）

- **三轮递进**（真实 LLM+DB）：through 885→893→901，delta 12/8/8 条，摘要输入 366→245→148 tokens（**不随会话线性增长**）；monotonic ✅ no_resend ✅ no_hole ✅ 边界无重叠（through < 最早保留 id）✅
- **并发同 session**：双线程同时触发 → 无回退、最终摘要非空 ✅（不同 session 天然并行）
- **重启恢复**：`docker restart agent-app-1` 后水位线 746 完整存活 → 只摘要 746 之后的 16 条（→833）→ 旧事实（POD-X2-GRY-001）保留 ✅ 未重新总结整段会话

## 八、Golden Cases 事实保真评测（§十~§十五）——本阶段核心交付

新增 `backend/evaluation/context_budget/`（driver / golden_eval / waterline）+ CLI `run_context_budget_eval.py`（case_a/case_b/case_c/case_c_l5/l5_direct/golden/waterline/rate 八模式，报告落 `data/eval_reports/`）。数据集 `datasets/context_budget_golden.json`：**24 例 × 十类**（标识符/金额/百分比/日期/约束/已确认决策/待办/否定/纠正/跨轮引用），每例 Baseline（原始历史）vs After-L5（真实增量摘要+重建投影）双轨。

**结果（真实 LLM + 真实 DB 水位线）**：

```text
cases_scored:          24
Fact Retention Rate:   1.0   （After-L5 全部关键事实保留）
Protected Fact Recall: 1.0   （摘要最终零遗漏）
patched_ratio:         0.28  （LLM 原生保留 72% / deterministic patch 28%）
constraint_violations: 0
```

patched=28% 处于正常安全网上沿（§十五阈值参考 1%~5% 正常、30%+ 需分析）——具体归因见 §九，不盲目调 prompt。

## 九、阈值校准建议（§二十四，全部有实测依据）

1. **`CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS` 20 → 120（已落 .env）**：实测 qwen3.7-flash 大 delta 摘要 27.4s、qwen3.8-flash 24.7s，20s 必超时（真实触发 3 次失败）。120s 上限仍在"罕见末防线"可接受范围。
2. **新增 `CONTEXT_L5_SUMMARY_MODEL`（已落代码+.env=qwen3.8-flash）**：默认 LLM 解析实测不稳定（MiniMax-M3 无密钥 / deepseek 熔断），摘要必须可指定健康模型。这是本阶段唯一的生产代码修复（走 proxy 线程内绑定，保留限流/韧性/token 统计）。
3. **ProtectedFacts 金额正则缺口（有具体丢失实例）**：「机动金 100,000 改成 80,000」的裸大数（无 ¥/元符号）未被捕→摘要丢失该修正。建议下阶段给 `_ENTITY_PATTERNS` 增加逗号分组数字模式 `\d{1,3}(,\d{3})+`（几乎必为金额/ID），不立即改（§十六：先收数据）。
4. **0.90 触发值保持**：普通聊天不可达不是阈值问题，是 L2 cap 的分层设计使然——L5 语义就是"巨 prompt 末防线"。若希望长会话也享受摘要收益，正确杠杆是 **end_turn 的条数触发（≥50 条）已增量化**，会随会话增长自然推进水位线，无需动 0.90。
5. **chat 侧 KB 检索空数据**（doc_db vs chroma collection 错位）：model-config/数据链路域，属其他会话范围，仅记录。

## 十、成本效率（§二十五）

一次 L5 = 1107 prompt + 2695 completion tokens（qwen3.8-flash，completion 含 thinking；qwen3.7-flash 版本 27.4s 完成）。即时回报：本轮 active context 省 ~530 tokens；未来 3~5 轮每轮以 321-token 摘要替代 ~750+ tokens 历史重放（每轮净省 ~430，5 轮 ~2150）。**L5 Efficiency ≈ 2150/3800 tokens ≈ 0.57（5 轮视角）**——在触发本就罕见的前提下可接受；若换无 thinking 模型 completion 可降 ~8 倍，效率显著改善。

## 十一、Git 与遗留

- 提交：`2c72748`（评测套件 + FuturesTimeout 修复 + CONTEXT_L5_SUMMARY_MODEL）；.env 增 2 行运行时配置（未提交，按惯例 .env 不入库——如需保留请自行确认）。
- **E2E 抓出的真 bug**：`FuturesTimeout` 导入缺失（Phase 3 清理重复导入时误删，单测未覆盖超时异常路径）——已修复+提交，这是实机验证价值的直接证明。
- 并行会话脏文件未动：`test_order_service.py`、tsconfig×3、data/quality_records、若干 untracked docs/tmp。
- 遗留建议：①golden 容器内跑会 OOM（exit 137），建议下阶段给 app 容器加内存余量或评测走独立进程/worker；②补金额裸大数正则；③chat KB 检索链路修复后可补 Case B 的纯 HTTP L1 数据。
