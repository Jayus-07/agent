"""test_cs_runner_v2.py — CS 评测集 v2（300 条）离线 CI 门禁。

门禁口径（P0 评测集设计稿 §5）：六类 jsonl 任一条 schema/枚举/id 非法即失败；
经 runner offline sanity（不调 LLM，CI 常驻）。
"""
import json
from pathlib import Path

from backend.evaluation.models import TestCase
from backend.evaluation.runners.cs import _run_cs

V2_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "cs" / "v2"
FILES = ["C1_faq_60.jsonl", "C2_query_60.jsonl", "C3_action_60.jsonl",
         "C4_complaint_40.jsonl", "C5_multi_40.jsonl", "C6_safety_40.jsonl"]


def _load_cases() -> list[TestCase]:
    cases: list[TestCase] = []
    for name in FILES:
        path = V2_DIR / name
        assert path.exists(), f"缺少数据集文件: {path}"
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            cases.append(TestCase(
                id=item["id"],
                # 多轮 case 以末轮输入为执行入口（逐轮重放待 runner v2.1）
                question=item["turns"][-1]["text"],
                module="cs",
                expected=item["expected"],
                metadata={"category": item["category"],
                          "schema_version": item["schema_version"],
                          "turns": item["turns"],
                          **item.get("metadata", {})},
            ))
    return cases


def test_cs_v2_total_is_locked_300():
    cases = _load_cases()
    assert len(cases) == 300, f"锁定量 300，实际 {len(cases)}"


def test_cs_v2_offline_sanity_all_pass():
    cases = _load_cases()
    results = _run_cs(cases, live=False)
    assert len(results) == 300
    failures = [r for r in results if r.status != "pass"]
    assert not failures, (
        f"离线校验失败 {len(failures)} 条，前 3 条: "
        f"{[(r.case_id, r.error_msg) for r in failures[:3]]}"
    )
