"""test_versioning.py — R4 版本治理：as_of/current/all_versions 检索期裁决测试。

基准语义与 rag_100_docs 版本链标注一致（TRAVEL_VERSIONS）：
  v1 [2024-04-01, 2025-06-30] → v2 [2025-07-01, 2026-08-31] → v3 [2026-09-01, ∞)
覆盖任务书 §6 版本检索 5 条中的裁决逻辑：
  日期/版本筛选（as_of）、当前版本（current）、缺失不猜（窗口外剔除）、
  冲突留证（window_overlap / supersedes_cycle）、跨文档多候选（candidate_versions）。
"""
from __future__ import annotations

from backend.rag.versioning import (
    candidate_versions,
    is_version_visible,
    normalize_requirement,
    partition_by_version,
    version_conflict_info,
)

# 与 fixtures TRAVEL_VERSIONS 同构的 metadata 构造器
def _meta(doc_id, version_id, eff_from, eff_to, supersedes=""):
    return {
        "doc_id": doc_id,
        "version_id": version_id,
        "effective_from": eff_from,
        "effective_to": eff_to,
        "supersedes_version_id": supersedes,
    }


V1 = _meta("policy_travel_v1", "v1", "2024-04-01", "2025-06-30")
V2 = _meta("policy_travel_v2", "v2", "2025-07-01", "2026-08-31", "policy_travel_v1")
V3 = _meta("policy_travel_v3", "v3", "2026-09-01", "", "policy_travel_v2")
FAQ = {"doc_id": "faq_employee", "version_id": "", "effective_from": "",
       "effective_to": "", "supersedes_version_id": ""}  # 非版本链文档

CHAIN = [V1, V2, V3]


# ── normalize_requirement ────────────────────────────────────────────

def test_normalize_none_and_invalid_fallback_any():
    assert normalize_requirement(None)["type"] == "any"
    assert normalize_requirement({})["type"] == "any"
    assert normalize_requirement({"type": "bogus"})["type"] == "any"
    # 非法 type 保留 raw 供留痕
    assert normalize_requirement({"type": "bogus"})["raw"] == {"type": "bogus"}


def test_normalize_as_of_requires_valid_date():
    # as_of 缺日期/日期非法 → 无法约束，退化 any（防止静默空候选）
    assert normalize_requirement({"type": "as_of"})["type"] == "any"
    assert normalize_requirement({"type": "as_of", "date": "2025/08/01"})["type"] == "any"
    ok = normalize_requirement({"type": "as_of", "date": "2025-08-01"})
    assert ok["type"] == "as_of" and ok["date"] == "2025-08-01"


# ── is_version_visible ───────────────────────────────────────────────

def test_any_requirement_keeps_everything():
    for m in CHAIN + [FAQ]:
        assert is_version_visible(m, {"type": "any"})


def test_non_versioned_docs_always_visible():
    """非版本链文档在所有要求下恒可见（存量语料行为不变）。"""
    for req in (None, {"type": "current"},
                {"type": "as_of", "date": "2024-01-01"},
                {"type": "all_versions"}):
        assert is_version_visible(FAQ, req)


def test_as_of_window_adjudication():
    """as_of 按生效窗口裁决，边界含端点（RD-023/RD-024 语义）。"""
    as_of_202508 = {"type": "as_of", "date": "2025-08-01"}
    as_of_202406 = {"type": "as_of", "date": "2024-06-01"}
    # 2025-08 → 仅 v2
    assert not is_version_visible(V1, as_of_202508)
    assert is_version_visible(V2, as_of_202508)
    assert not is_version_visible(V3, as_of_202508)
    # 2024-06 → 仅 v1
    assert is_version_visible(V1, as_of_202406)
    assert not is_version_visible(V2, as_of_202406)
    # 边界：生效日当天/失效日当天均有效
    assert is_version_visible(V2, {"type": "as_of", "date": "2025-07-01"})
    assert is_version_visible(V2, {"type": "as_of", "date": "2026-08-31"})
    # 窗口空档（缺失不猜：v1 结束到 v2 生效之间无版本覆盖）
    gap = {"type": "as_of", "date": "2025-06-30"}
    assert is_version_visible(V1, gap)  # v1 失效日当天仍有效
    assert not is_version_visible(V2, {"type": "as_of", "date": "2025-06-30"})


def test_current_adjudication():
    """current 只保留现行版本（effective_to 空或未到期）。"""
    req = {"type": "current"}
    assert not is_version_visible(V1, req)
    assert not is_version_visible(V2, req)  # 已被 v3 取代（窗口已结束）
    assert is_version_visible(V3, req)      # effective_to 空 = 现行
    # 未到期（未来失效日）的版本仍算现行
    scheduled = _meta("doc_x", "v9", "2020-01-01", "2999-12-31")
    assert is_version_visible(scheduled, req)


def test_all_versions_adjudication():
    req = {"type": "all_versions"}
    for m in CHAIN:
        assert is_version_visible(m, req)


# ── partition_by_version ─────────────────────────────────────────────

def test_partition_keeps_order_and_groups():
    metas = [FAQ, V1, V2, V3]
    allowed, denied = partition_by_version(
        metas, {"type": "as_of", "date": "2025-08-01"})
    assert allowed == [0, 2]   # FAQ + V2
    assert denied == [1, 3]    # V1 / V3 窗口不匹配


def test_partition_current_with_injected_today():
    """current 依赖"今天"——注入日期可测（历史时刻裁决）。"""
    metas = [V1, V2, V3]
    # 注入 2026-07-01：v2 仍在窗口内 → 现行是 v2（不是 v3）
    allowed, _ = partition_by_version(metas, {"type": "current"}, today="2026-07-01")
    assert allowed == [1]
    # 注入 2026-10-01：v2 已到期 → 现行是 v3
    allowed, _ = partition_by_version(metas, {"type": "current"}, today="2026-10-01")
    assert allowed == [2]


# ── candidate_versions / version_conflict_info ───────────────────────

def test_candidate_versions_dedup_and_order():
    metas = [V2, FAQ, V2, V3, V1]
    cands = candidate_versions(metas)
    assert [c["doc_id"] for c in cands] == ["policy_travel_v2", "policy_travel_v3", "policy_travel_v1"]
    assert cands[0]["supersedes_version_id"] == "policy_travel_v1"
    assert cands[1]["effective_to"] == ""   # V3 无界 = 现行
    assert cands[2]["effective_to"] == "2025-06-30"


def test_conflict_detects_window_overlap_only():
    """链窗口首尾衔接（无重叠）→ 无冲突；真重叠 → 留证。"""
    # 规范链：v1/v2/v3 窗口衔接不重叠
    assert version_conflict_info(CHAIN) == []
    # 人为制造重叠：v2' 与 v3 窗口交叠
    v2_bad = _meta("policy_travel_v2", "v2", "2025-07-01", "2027-08-31", "policy_travel_v1")
    conflicts = version_conflict_info([V1, v2_bad, V3])
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "window_overlap"
    assert set(conflicts[0]["docs"]) == {"policy_travel_v2", "policy_travel_v3"}


def test_conflict_detects_supersedes_cycle():
    a = _meta("doc_a", "v1", "2024-01-01", "", "doc_b")
    b = _meta("doc_b", "v1", "2024-01-01", "", "doc_a")
    conflicts = version_conflict_info([a, b])
    assert any(c["type"] == "supersedes_cycle" for c in conflicts)


# ── RagRequestState 集成点 ───────────────────────────────────────────

def test_rag_request_state_carries_version_requirement():
    from backend.rag.context import RagRequestState
    ctx = RagRequestState(version_requirement={"type": "as_of", "date": "2025-08-01"})
    assert normalize_requirement(ctx.version_requirement)["type"] == "as_of"
    # 缺省 = any（不约束）
    assert normalize_requirement(RagRequestState().version_requirement)["type"] == "any"
