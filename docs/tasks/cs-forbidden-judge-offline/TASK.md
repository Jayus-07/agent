# 任务四：客服事实判据与离线语义裁判落地 (CS Fact Criteria & Offline Semantic Judge)

## 背景与痛点
1. **测试断崖与全量跳过（Skip）**：
   - 客服评测（`backend/evaluation/runners/cs.py`）中有 85/324 条关键用例声明了 `forbidden_facts`（包括编造物流、虚构政策金额、谎称已到账、跳过身份核验直接执行等严重违规）；
   - 现有的 `backend/evaluation/judge_cs_forbidden.py` 强依赖云端 `eval_gen` 大模型调用。当在离线环境、本地回归或 CI 流水线（无云端模型/密钥）运行时，裁判直接报不可用（`violated=None`），导致用例只能被标为 `unverified` 并全部 skip，无法形成确定性的生产质量门禁；
2. **字面匹配误判率高**：
   - 现有的降级方案仅是简单的 `must_not_contain` 纯字面匹配；
   - 面对否定句（如“抱歉，生鲜商品**不支持退款**”由于包含“退款”而被误判违规）以及同义改写时，字面匹配存在大量误报与漏报；
3. **目标**：
   - 在 `judge_cs_forbidden.py` 实现**高精度的确定性离线语义裁判引擎（Offline Fact/Rule Semantic Judge）**；
   - 当大模型不可用或显式配置离线模式时，无缝切换到离线确定性裁判，结合上下文事实（Context/Fact-IDs）与否定句/求证免责语法，精准裁决 `violated`，绝不因无云端 LLM 而返回 `None` 导致 skip；
   - 通过 `judge_cs_forbidden_golden.py` 的 25 条 Golden 基准（覆盖 10 大核心客服禁说违规概念），达成 100% 满分判准通过；
   - 在 `runners/cs.py` 中将离线裁判无缝接入，确保离线与本地评测时，85 条禁说事实用例能够产出确定性 `forbidden_facts_respected` 质量指标。

## 验收标准
- [ ] `judge_cs_forbidden.py` 实现确定性离线语义裁判逻辑，支持否定句免疫、求证免责与证据冲突判定；
- [ ] 25 条客服 Golden 样本（`datasets/cs/forbidden_judge_golden.jsonl`）在离线裁判下裁决一致率达到 100%（25/25 PASS，0 翻转，0 不可用）；
- [ ] `runners/cs.py` 接入离线裁判回退，当 `eval_gen` 大模型不可用时能够平滑裁决，不再全部退回到 `unverified`；
- [ ] 编写并执行针对性的单元测试，覆盖各类违规与合规典型句式。
