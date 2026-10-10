"""travel/evaluation/ — 评测与指标（travel/evaluation/ 冻结）

Phase 1 仅建包。Phase 2 落 slot 评测集与 runner（槽位 ≥95% / 人数 ≥99% /
否定 ≥98%）；Phase 4 落 research freshness/coverage；Phase 6 落
assistant_qa（citation correctness / hallucination=0）；Phase 8 全指标
基线冻结 + LLM 预算断言进 CI。
"""
