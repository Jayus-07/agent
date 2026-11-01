"""test_cs_runner_v2.py — CS 评测集 runner 离线 CI 门禁。

口径：锁版 `cs/cases.jsonl`（v2.2，324 条）是唯一事实源，经统一 loader 加载
（manifest content_hash 加载时强制校验，原地改即 fail-fast）；cs runner
offline sanity 校验 expected.target/intent/cs_route/next_action/risk_level/
should_handoff 枚举合法 + case id 唯一，不调 LLM，CI 常驻。

历史（2026-11-02 cs-evaluation-upgrade P1 定案）：本测试原直读 `cs/v2/` 生成
中间分片（304 条）——与锁版 300 条逐字段一致 + 4 条未收编候选，属双事实源
并行消费；分片已改名 `cs/v2.deprecated/` 归档（见 datasets/README.md），消费
入口收敛到锁版 + 统一 loader。P3（2026-11-02）候选 4 条经词表扩圈验收全绿
后按锁版流程收编，总 320→324（v2.2）。
"""
from backend.evaluation.dataset import load_dataset
from backend.evaluation.runners.cs import _run_cs


def test_cs_locked_total():
    cases = load_dataset("cs")
    assert len(cases) == 324, (
        f"锁版 324 条，实际 {len(cases)}——先核对 manifest case_count 与 "
        "content_hash（改用例必须走锁版升版流程）"
    )


def test_cs_locked_offline_sanity_all_pass():
    cases = load_dataset("cs")
    results = _run_cs(cases, live=False)
    assert len(results) == len(cases)
    failures = [r for r in results if r.status != "pass"]
    assert not failures, (
        f"离线校验失败 {len(failures)} 条，前 3 条: "
        f"{[(r.case_id, r.error_msg) for r in failures[:3]]}"
    )
