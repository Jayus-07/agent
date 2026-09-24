# Memory Production Closure — STOP F 实施报告：Memory Golden Evaluation 与 relevance threshold 实证定标

> 日期：2026-09-24 ｜ 前置：STOP A-D + STOP E（`1d81153`）均冻结
> 本轮关闭 STOP D 遗留：「0.45 尚未真实样本审计 / exec 环境无法初始化 DB-managed embedding provider」。

---

## 1. Verdict

**STOP_F_PASS=true**

## 2. Dataset（F1）

`backend/evaluation/datasets/memory_golden.jsonl`：**60 例 / 62 条种子记忆 / 7 类别**，禁止 happy-path：

| 类别 | 例数 | 覆盖 |
|---|---|---|
| relevant | 12 | 明确偏好 / 稳定画像（过敏）/ 近期有用 / 旧而有用（300 天）/ 高 importance |
| irrelevant | 12 | 完全异主题 / 词面相似语义无关（苹果、小米、特斯拉）/ 高 importance 无关 / 高 recency 无关 / 同实体不同意图（豆豆）/ generic |
| conflict | 6 | supersede 新旧对 ×4（keyed：preference.coffee/city、contact.phone、pants_size）/ duplicate 对 / 运动改口 |
| isolation | 10 | wrong tenant ×2 / wrong user ×2 / inactive ×2 / expired ×2 / quarantine 哨兵 ×2 |
| safety | 8 | 指令型注入 ×3 / 假管理员 ×1 / `</memory_context>` 逃逸 ×2 / 假系统设定 ×1 / legacy 隐藏偏好 ×1 |
| global | 6 | response.language/detail_level/style 跨主题 ×5 / global+主题混合 ×1 |
| zero | 6 | 无任何记忆（「正确结果 = 0 条」为正式标签） |

## 3. Label Distribution（F2）

`must_retrieve`（expect_retrieved=true）34 条种子；`must_not_retrieve`（false）28 条；global 7 条（免 gate 白名单，单独计数）；`expect_zero` 用例 28 个（12 无关 + 10 隔离 + 6 零记忆）——**「无记忆可召回 = 合法正确结果」成类固化**。

## 4. Evaluation Method（F7 先行：真实 provider 阻塞根因关闭）

**STOP D 遗留根因定位（非环境限制，已关闭）**：embedding 配置来自 DB 覆盖层（`llm_specialized_model_bindings`/模型目录/凭据），唯一注入入口是 `registry_store.refresh_registry()`（`registry_store.py:420`）——app 进程启动时调用，**短命进程（脚本/exec）从不调用 → `specialized._bindings` 为空 → `resolve_binding("embedding")=None → 明确报错`**。STOP D 的「exec 环境无法初始化」即此。修复 = 驱动脚本 bootstrap 先 `await refresh_registry()`；随后**真实出站调用成功**：生产同款 `qwen3.7-text-embedding`（DashScope 兼容口，1024 维）。

**全真链路**：种子向量与 query 向量均由该真实 provider 计算（`embed_documents` 批量 + 维度校验）；记忆行真实落 PG（agent_memory@5433，pgvector `cosine_distance`）；检索 = `HybridRetriever.retrieve` 完整真实管线（SQL eligibility → semantic gate → global 白名单 → rank/merge/max-5）。**阈值 sweep 不 mock gate**：逐档改 `retriever` 模块的 `MEMORY_MIN_RELEVANCE_SCORE` 后全量复跑；同一 query 向量跨阈值复用（同模型同文本确定性），gate/rank/merge 全部真实代码。

**F6 口径**：irrelevant injection rate 以 retriever 返回的**最终注入集合**计（= `service.start_session` 注入 `<memory_context>` 的条目一一映射），非 candidate 口径。

**隔离口径修正（如实记录）**：quarantine 用例首版经运行时路径写入，被 `normalize_tenant_id` 按设计归一为 default（「运行时永不产出 quarantine」）——那是**驱动脚本的数据构造缺陷**，不是产品泄漏；修正为 raw SQL 直写 `tenant_id='quarantine'`（模拟迁移直写的 legacy 存量行）后，检索全程不可见（SQL eligibility 生效）。STOP D 的该契约经本修正后真实复验。

## 5. Threshold Sweep（F4，真实数据全表）

| threshold | recall_macro | precision_macro | irr_inj_rate | zero_acc | MRR | total_injected | 泄漏(e/u/t) |
| --------: | -----------: | --------------: | -----------: | -------: | --: | -------------: | ----------- |
| 0.30 | **1.000** | 0.889 | 0.105 | 0.857 | 1.000 | 38 | 0/0/0 |
| 0.33 | **1.000** | 0.889 | 0.105 | 0.857 | 1.000 | 38 | 0/0/0 |
| **0.35** | **1.000** | **0.889** | **0.105** | **0.857** | **1.000** | **38** | **0/0/0** |
| 0.37 | 0.969 | 0.886 | 0.108 | 0.857 | 0.969 | 37 | 0/0/0 |
| 0.40 | 0.969 | 0.886 | 0.108 | 0.857 | 0.969 | 37 | 0/0/0 |
| 0.42 | 0.875 | 0.875 | 0.118 | 0.857 | 0.875 | 34 | 0/0/0 |
| 0.45（旧值） | 0.797 | 0.929 | 0.069 | 0.929 | 0.813 | 29 | 0/0/0 |
| 0.50 | 0.672 | 0.957 | 0.042 | 0.964 | 0.688 | 24 | 0/0/0 |
| 0.55 | 0.547 | 0.900 | 0.095* | 0.929 | 0.563 | 21 | 0/0/0 |
| 0.60 | 0.391 | 0.867 | 0.125* | 0.929 | 0.406 | 16 | 0/0/0 |

*0.55/0.60 行取自修正 quarantine 口径前的首轮 sweep（占比失真源于分母缩小；定性结论一致：高阈值下残余注入全是硬负例）。

**相似度分布（松 gate top20 实测，62 种子）**：正样本 min 0.206 / 中位 0.505 / max 0.769；负样本 min 0.155 / 中位 0.278 / p75≈0.44 / max 0.670（该 max 为隔离直写行的松 gate 候选，正式管线 SQL 层排除）。**问句↔陈述型记忆的语义落差**：真实相关对大量落在 0.35~0.53（过敏 0.448、饮食 0.417、预算 0.415）；无关主体落在 0.24~0.31；仅 4 个硬负例 ≥0.436（同实体「豆豆」0.532、词面「小米」0.482 等——任何 ≥0.30 的阈值都拦不住，属排序/模型局限而非阈值问题）。

## 6. Final Threshold（F4 定稿）

**`MEMORY_MIN_RELEVANCE_SCORE = 0.35`**（`config/memory.py` 默认值已切换，env 可覆盖）。

- 数据规模：60 例 / 62 种子 / 真库真向量，sweep 于 2026-09-24 实机执行（elapsed 67.6s）。
- embedding model：DB 绑定 embedding 角色 = `qwen3.7-text-embedding`（DashScope 兼容口，1024 维，生产同款）。
- 选择依据：0.30~0.35 为 Pareto 平台（recall 1.000 / precision 0.889 / irr 0.105）；0.45 处 recall 崩至 0.797——**花生过敏（0.448）这类安全关键记忆被拒**——换来的 irr 改善仅 0.036（≈1.5 例）。选平台右端 **0.35**：与 0.30 指标全同，且距无关带主体（0.24~0.31）留 0.05 边距，对 embedding 漂移更稳。
- 不偏向旧值 0.45，不为 recall 无底线下探：0.25 以下无关主体（中位 0.278）将成批进入，未采纳。

## 7-10. Recall / Precision / Irrelevant Injection / Zero-Memory（最终值 @0.35）

- **Recall@5 = 1.000**（macro，34 must_retrieve 全召回，含 300 天旧记忆）
- **Precision@5 = 0.889**（macro）
- **MRR = 1.000**
- **irrelevant_injection_rate = 0.105**（4/38，全部为词面/同实体硬负例）
- **zero-memory accuracy = 0.857**（24/28；4 个失败全为含硬负例种子的无关类用例，纯 zero 类 6/6 全对）

## 11. Global Preference（@0.35）

global 召回 **6/6 用例 7/7 种子**（免 gate 白名单），跨主题生效（含「1+1=几」「量子纠缠」等零相关问句）；混合用例 glb-mix 中主题记忆与 global 同轮共注，未互相挤占。

## 12. Isolation（@0.35，全 SQL 层）

expired leakage = **0** ｜ wrong-user leakage = **0** ｜ wrong-tenant leakage = **0** ｜ quarantine（raw 直写 legacy 口径）retrieved = **0**——十类隔离用例全部不可见（`search_hybrid` 的 `is_active + tenant + user + expire_at` 四条件实证）。

## 13. Safety（@0.35）

8 个注入型攻击记忆全部真实注入（保证结构检查非空洞），**structural escape = 0**：
- SystemMessage 逐条检查仅含固定 policy 文本（`[历史上下文边界]` 前缀），记忆原文零出现；
- `<memory_context>` 开/闭标签结构 count == 1（`</memory_context>`/`<memory_context>` 伪造标签全部中性化为 `<\/…>`）；
- role 分离：原文仅存在于 AIMessage 数据块（`start_session` 全链断言）。

## 14. Key Quality（F5，清理 golden 数据后的实库抽样）

active 165 条：**missing_key_rate = 95.8%**｜**bad_key_rate = 0%**（格式全合法）｜**duplicate conceptual key groups（tenant+user+key 维度）= 0**。

结论：key 机制本身健康（无坏 key、无同 scope 概念重复），但 **active 存量 95.8% 无 key**——STOP C 的 keyed supersede 管线在存量上几乎无处发力（存量以 legacy/早期 inferred 为主）。非 P0（机制有 STOP C 契约测试与真库回归；增量写入会逐步 key 化），登记为结构观察：keyed 覆盖率随新增写入自然改善，若长期停留 >90% 无 key 再考虑 backfill 策略。

## 15. Failure Cases

- 4 个硬负例（`irr-entity-1` 0.532 / `irr-lexical-2` 0.482 / `irr-entity-2` 0.440 / `irr-lexical-1` 0.436）：同实体词面重叠，0.30~0.42 区间任何阈值都无法分离——属 embedding 模型排序能力边界，非阈值选择问题；量级 4/60 例，每例最多 1 条陪跑记忆且有 policy SystemMessage 约束。
- `rel-fitness`（0.352）：阈值 ≥0.37 即丢失——是 0.35 平台右端取值的主要保护对象之一。

## 16. Remaining Risks

- 分布基于 60 例 golden（该模型空间的问句↔陈述落差已实测），生产长尾分布可能偏移；golden 已固化为可复跑驱动，回归门 `test_golden_dataset_contract.py` 锁定定稿值与硬门基线。
- 4 个硬负例的终极解法在 rerank/模型层（当前 L3 无 reranker），已超本轮 scope。
- global 白名单 `response.*` 仅 3 个 key 有真实样本（language/detail_level/style），扩 key 需先扩 golden。

## 17. Commit

`test(memory): freeze golden retrieval quality`（本 STOP 单一提交，hash 见 git log）

---

### 附：产物清单

| 文件 | 说明 |
|---|---|
| `backend/evaluation/datasets/memory_golden.jsonl` | 60 例 7 类别数据集 |
| `backend/scripts/eval_memory_golden.py` | 评测驱动（refresh_registry bootstrap / 真实向量 / 逐阈值复跑 / 结构检查 / key 质量） |
| `backend/evaluation/datasets/memory_golden_results.json` | 真实运行证据（sweep 全表 + per-case 注入明细 + 分布 + 结构 + key 质量） |
| `backend/config/memory.py` | `MEMORY_MIN_RELEVANCE_SCORE` 默认 0.45→0.35（含定标注释） |
| `backend/tests/memory/test_golden_dataset_contract.py` | 数据集结构门 + 硬门冻结基线 + 定标值锁定（离线） |
