"""test_rule_freeze.py — 规则链冻结守卫（规划阶段 0.2 / 停止条件 S8 的 CI 化）。

规划（docs/2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md）§0.2：
CI 禁止 domain_data.py 新增正则；新增需架构师审批。本测试把词表当前状态
（条目数 + 内容 sha256）固化为基线 JSON，任何未走审批的词表变更都会让
本测试变红。

审批流程（= 更新基线的唯一合法路径）：
  1. 架构师确认新词表诉求不属于"修个案"（规划 §2.2 N7：不再往
     domain_data.py 新增正则修个案；强信号走统一抽取/动态词库）；
  2. 显式更新基线并留痕（CI 里基线 diff 可见）：
       RULE_FREEZE_UPDATE_BASELINE=1 python -m pytest tests/rag/test_rule_freeze.py -q --no-cov
  3. 在规划文档 §7 登记变更台账（改了哪个词表、为什么）。

冻结对象 = domain_data.py 全部静态词表；DB 动态词库（keyword_store）不在此列
（线上可维护，走管理页流程）。
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

BASELINE_PATH = Path(__file__).parent / "baselines" / "rule_freeze_baseline.json"

# 冻结的词表名 → 规范化取值函数（编译正则取 pattern 字符串）
_FROZEN = {
    "DOC_TYPE_RULES": lambda mod: {
        t: [[p, w] for p, w in rules] for t, rules in mod.DOC_TYPE_RULES.items()},
    "DOMAIN_RULES": lambda mod: {d: dict(kw) for d, kw in mod.DOMAIN_RULES.items()},
    "FILENAME_TYPE_HINTS": lambda mod: dict(mod.FILENAME_TYPE_HINTS),
    "FOLDER_TYPE_HINTS": lambda mod: dict(mod.FOLDER_TYPE_HINTS),
    "SIGNAL_RULES": lambda mod: {s: list(kw) for s, kw in mod.SIGNAL_RULES.items()},
    "DEFAULT_KEYWORDS": lambda mod: list(mod.DEFAULT_KEYWORDS),
    "TIME_PATTERNS": lambda mod: [p.pattern for p in mod.TIME_PATTERNS],
}


def _collect_current() -> dict:
    from backend.rag.preprocessing import domain_data

    out: dict = {}
    for name, norm in _FROZEN.items():
        value = norm(domain_data)
        blob = json.dumps(value, ensure_ascii=False, sort_keys=True)
        out[name] = {
            "sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16],
            "entries": len(value),
        }
    return out


def _load_baseline() -> dict:
    if not BASELINE_PATH.exists():
        pytest.fail(
            f"冻结基线文件缺失: {BASELINE_PATH}\n"
            "首次生成（视为冻结动作，需在规划文档 §7 登记）:\n"
            "  RULE_FREEZE_UPDATE_BASELINE=1 python -m pytest "
            "tests/rag/test_rule_freeze.py -q --no-cov")
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_frozen_wordlists_unchanged():
    """词表指纹必须与基线一致；不一致 = 未审批的规则链变更（S8 前置拦截）。"""
    baseline = _load_baseline()
    current = _collect_current()
    diffs = {}
    for name in baseline:
        if name.startswith("_"):
            continue
        if name not in current:
            diffs[name] = "基线中的词表已不存在"
            continue
        for key in ("sha256", "entries"):
            if baseline[name].get(key) != current[name].get(key):
                diffs[name] = f"{key}: 基线 {baseline[name].get(key)} → 当前 {current[name].get(key)}"
    assert not diffs, (
        "domain_data.py 词表在未走审批流程的情况下发生了变更（规划 §0.2 / S8）:\n  "
        + "\n  ".join(f"{k}: {v}" for k, v in diffs.items())
        + "\n这是有意变更吗？流程: 架构师审批 → 显式更新基线 → 规划文档 §7 登记台账。\n"
          "更新基线: RULE_FREEZE_UPDATE_BASELINE=1 python -m pytest "
          "tests/rag/test_rule_freeze.py -q --no-cov"
    )


def test_baseline_covers_all_frozen_wordlists():
    """基线必须覆盖全部冻结词表（防止新增词表漏冻结）。"""
    baseline = _load_baseline()
    missing = [name for name in _FROZEN if name not in baseline]
    assert not missing, f"基线缺少词表条目: {missing}，请用 RULE_FREEZE_UPDATE_BASELINE=1 重建"


def test_update_baseline_mode_writes_and_matches():
    """生成模式：RULE_FREEZE_UPDATE_BASELINE=1 时把当前指纹写进基线（显式审批动作）。"""
    if os.getenv("RULE_FREEZE_UPDATE_BASELINE") != "1":
        pytest.skip("仅在显式更新基线时执行（RULE_FREEZE_UPDATE_BASELINE=1）")
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = _collect_current()
    payload = {
        "_meta": {
            "frozen_note": "规划阶段 0.2 冻结快照；更新 = 架构师审批 + 规划文档 §7 台账登记",
            "plan": "docs/2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md",
        },
        **current,
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert _load_baseline() == {**_load_baseline(), **current}
