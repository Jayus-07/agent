"""规则与 taxonomy 冻结守卫。

规则词典或 taxonomy 发生有意变更时，应同步更新基线，并在代码评审中说明原因：
    RULE_FREEZE_UPDATE_BASELINE=1 python -m pytest tests/rag/test_rule_freeze.py -q --no-cov
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
            "plan": "docs/domains/rag.md#上传元数据与级联决策",
        },
        **current,
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert _load_baseline() == {**_load_baseline(), **current}
