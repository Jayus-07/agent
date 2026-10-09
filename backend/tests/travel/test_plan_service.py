"""tests/travel/test_plan_service.py — 行程版本生命周期编排（方案 v2 §5/§7）

用内存假 store 打 service 全逻辑（SQL 层是薄封装不属单测对象），覆盖：
1. record_plan_result：首版落账（parent=0 占位）+ 跨版确定性 POI diff
2. confirm：成功 / 版本过期 409 / 重复确认 409 / 未知会话 404 / scope 隔离
3. restore：内容还原 + 版本号新增（rollback 语义）/ base 过期 / 目标缺失 /
   恢复当前版拒绝 / 并发冲突（版本号被占）
4. diff：POI 增删移 + brief 字段差异
"""
from __future__ import annotations

import pytest

from backend.travel.core.plan_service import (
    PlanVersionConflict,
    PlanVersionInvalid,
    PlanVersionNotFound,
    PlanVersionPersistenceError,
    PlanVersionService,
)
from backend.travel.models.brief import TravelBrief
from backend.travel.models.itinerary import PLAN_STATUS_READY

from .conftest import make_day, make_item, make_itinerary, make_poi


class FakePlanStore:
    """内存版账本：与 plan_store 同签名（service 注入面）。"""

    def __init__(self):
        self._rows: dict[tuple[str, str, str, int], dict] = {}
        self._tick = 0  # 每次落账递增，模拟 PG now() 的单调时间（列表排序依赖）

    def enabled(self) -> bool:
        return True

    def save_version(self, cid, uid, itinerary, *, plan_status="waiting_confirmation",
                     change=None, tenant_id="default", strict=False) -> bool:
        v = int(itinerary.get("plan_version") or 0)
        key = (tenant_id, uid, cid, v)
        if key in self._rows:
            return False
        brief = itinerary.get("brief") or {}
        self._tick += 1
        self._rows[key] = {
            "conversation_id": cid, "plan_version": v, "user_id": uid,
            "tenant_id": tenant_id,
            "plan_status": plan_status, "destination": brief.get("destination", ""),
            "itinerary": itinerary, "change": change or {},
            "created_at": f"2026-10-01T00:00:{self._tick:02d}+00:00",
        }
        return True

    def _scoped(self, cid, uid, tenant_id="default"):
        return [r for r in self._rows.values()
                if r["conversation_id"] == cid and r["user_id"] == uid
                and r["tenant_id"] == tenant_id]

    def latest_version(self, cid, uid, tenant_id="default", *, strict=False):
        rows = self._scoped(cid, uid, tenant_id)
        return max(rows, key=lambda r: r["plan_version"]) if rows else None

    def active_version(self, cid, uid, tenant_id="default", *, strict=False):
        rows = [r for r in self._scoped(cid, uid, tenant_id)
                if r["plan_status"] == "confirmed"]
        return max(rows, key=lambda r: r["plan_version"]) if rows else None

    def get_version(self, cid, uid, v, tenant_id="default"):
        for r in self._scoped(cid, uid, tenant_id):
            if r["plan_version"] == int(v):
                return dict(r)
        return None

    def list_versions(self, cid, uid, tenant_id="default"):
        return sorted(self._scoped(cid, uid, tenant_id),
                      key=lambda r: r["plan_version"], reverse=True)

    def list_conversations(self, uid, limit=30, tenant_id="default"):
        # 与真实现同口径：每会话取最新版一行，按 created_at 新→旧
        newest: dict[str, dict] = {}
        counts: dict[str, int] = {}
        for r in self._rows.values():
            if r["user_id"] != uid or r["tenant_id"] != tenant_id:
                continue
            counts[r["conversation_id"]] = counts.get(r["conversation_id"], 0) + 1
            cur = newest.get(r["conversation_id"])
            if cur is None or r["plan_version"] > cur["plan_version"]:
                newest[r["conversation_id"]] = r
        rows = sorted(newest.values(),
                      key=lambda r: r["created_at"], reverse=True)
        return [
            {
                "conversation_id": r["conversation_id"],
                "plan_version": r["plan_version"],
                "plan_status": r["plan_status"],
                "destination": r["destination"],
                "created_at": r["created_at"],
                "versions_count": counts[r["conversation_id"]],
            }
            for r in rows[:max(1, min(int(limit), 100))]
        ]

    def confirm_version(self, cid, uid, v, tenant_id="default") -> str | None:
        latest = self.latest_version(cid, uid, tenant_id)
        if not latest or int(latest["plan_version"]) != int(v):
            return None
        for r in self._scoped(cid, uid, tenant_id):
            if r["plan_version"] == int(v):
                if r["plan_status"] == "waiting_confirmation":
                    r["plan_status"] = "confirmed"
                    return "confirmed"
                return None
        return None

    def discard_version(self, cid, uid, v, tenant_id="default") -> str | None:
        latest = self.latest_version(cid, uid, tenant_id)
        if not latest or int(latest["plan_version"]) != int(v):
            return None
        for r in self._scoped(cid, uid, tenant_id):
            if r["plan_version"] == int(v):
                if r["plan_status"] == "waiting_confirmation":
                    r["plan_status"] = "discarded"
                    return "discarded"
                if r["plan_status"] == "discarded":
                    return "discarded"
                return None
        return None


def _itin(days: int = 1, poi_ids: tuple[str, ...] = ("p1",),
          status: str = PLAN_STATUS_READY, version: int = 1) -> dict:
    brief = TravelBrief(destination="福州", days=days)
    pois = [make_poi(poi_id=pid, name=f"景点{pid}") for pid in poi_ids]
    day = make_day(items=[
        make_item(poi=p, start="09:00", end=f"{9 + 1 + i}:30")
        for i, p in enumerate(pois)
    ])
    itin = make_itinerary(brief=brief, days=[day] * days)
    itin.stamp_version(brief)
    # 账本测试关心版本链本身：直接盖目标版本号（模拟多轮规划的产物序号）
    itin.plan_version = version
    itin.parent_plan_version = version - 1 if version > 1 else None
    itin.status = status
    return itin.model_dump()


@pytest.fixture
def svc() -> PlanVersionService:
    return PlanVersionService(store=FakePlanStore())


CID, UID = "conv-1", "user-15"


class TestRecordPlanResult:
    def test_first_version_recorded(self, svc):
        out = svc.record_plan_result(CID, UID, _itin())
        assert out["plan_status"] == "confirmed"
        rec = out["change_record"]
        assert rec["parent_version"] == 0  # 首版占位：无父版
        # build_change_record 冻结语义：old=None 表示首版，无父版 diff
        assert rec["change"]["added"] == []

    def test_second_version_diffs(self, svc):
        svc.record_plan_result(CID, UID, _itin(poi_ids=("p1",)))
        out = svc.record_plan_result(CID, UID, _itin(days=2, poi_ids=("p1", "p2"), version=2))
        assert out["plan_status"] == "waiting_confirmation"
        rec = out["change_record"]
        assert rec["parent_version"] == 1
        assert rec["change"]["added"] == ["p2"]
        assert rec["change"]["brief_fields"] == ["days"]

    def test_store_failure_fails_closed(self, svc):
        class BrokenStore(FakePlanStore):
            def latest_version(self, cid, uid):
                raise RuntimeError("db down")
        broken = PlanVersionService(store=BrokenStore())
        with pytest.raises(PlanVersionPersistenceError):
            broken.record_plan_result(CID, UID, _itin())

    def test_only_one_unconfirmed_draft_can_exist(self, svc):
        svc.record_plan_result(CID, UID, _itin())
        svc.record_plan_result(CID, UID, _itin(days=2, version=2))
        with pytest.raises(PlanVersionConflict):
            svc.record_plan_result(CID, UID, _itin(days=3, version=3))

    def test_active_version_is_not_shadowed_by_draft(self, svc):
        svc.record_plan_result(CID, UID, _itin())
        svc.record_plan_result(CID, UID, _itin(days=2, version=2))
        assert svc.active_version(CID, UID)["plan_version"] == 1

    def test_unconfirmed_first_version_is_not_an_active_fallback(self, svc):
        store = FakePlanStore()
        store.save_version(CID, UID, _itin(), plan_status="waiting_confirmation")
        assert PlanVersionService(store=store).active_version(CID, UID) is None

    def test_new_plan_after_discard_is_confirmed_when_no_active_exists(self, svc):
        store = FakePlanStore()
        store.save_version(CID, UID, _itin(), plan_status="discarded")
        out = PlanVersionService(store=store).record_plan_result(
            CID, UID, _itin(version=2))
        assert out["plan_status"] == "confirmed"

    def test_stale_graph_version_is_advanced_by_persistent_latest(self, svc):
        svc.record_plan_result(CID, UID, _itin(version=9))
        stale = _itin(days=2, version=2)

        out = svc.record_plan_result(CID, UID, stale)

        assert stale["plan_version"] == 10
        assert stale["parent_plan_version"] == 9
        assert out["change_record"]["version"] == 10
        assert svc.latest_version(CID, UID)["plan_version"] == 10

    def test_version_conflict_does_not_create_a_second_pending_draft(self):
        class ConflictOnceStore(FakePlanStore):
            def __init__(self):
                super().__init__()
                self.conflict_once = False

            def save_version(self, cid, uid, itinerary, **kwargs):
                if self.conflict_once:
                    self.conflict_once = False
                    # 模拟并发胜方先写入同一候选版本，再返回主键冲突。
                    super().save_version(cid, uid, dict(itinerary), **kwargs)
                    return False
                return super().save_version(cid, uid, itinerary, **kwargs)

        store = ConflictOnceStore()
        svc = PlanVersionService(store=store)
        svc.record_plan_result(CID, UID, _itin(version=1))
        store.conflict_once = True

        stale = _itin(days=2, version=1)
        with pytest.raises(PlanVersionConflict):
            svc.record_plan_result(CID, UID, stale)
        assert svc.latest_version(CID, UID)["plan_version"] == 2
        assert svc.active_version(CID, UID)["plan_version"] == 1


class TestConfirm:
    def _seed(self, svc):
        svc.record_plan_result(CID, UID, _itin())          # v1
        svc.record_plan_result(CID, UID, _itin(days=2, version=2))       # v2

    def test_confirm_ok(self, svc):
        self._seed(svc)
        out = svc.confirm(CID, UID, 2)
        assert out == {"status": "ok", "plan_version": 2, "plan_status": "confirmed"}

    def test_confirm_stale_version_conflict(self, svc):
        self._seed(svc)
        with pytest.raises(PlanVersionConflict) as ei:
            svc.confirm(CID, UID, 1)
        assert ei.value.current_version == 2

    def test_confirm_twice_conflict(self, svc):
        self._seed(svc)
        svc.confirm(CID, UID, 2)
        with pytest.raises(PlanVersionConflict):
            svc.confirm(CID, UID, 2)

    def test_confirm_unknown_conversation_not_found(self, svc):
        with pytest.raises(PlanVersionNotFound):
            svc.confirm("conv-other", UID, 1)

    def test_confirm_is_scoped_to_tenant(self, svc):
        svc.record_plan_result(CID, UID, _itin(), tenant_id="tenant-a")
        with pytest.raises(PlanVersionNotFound):
            svc.confirm(CID, UID, 1, tenant_id="tenant-b")


class TestDiscard:
    def test_discard_latest_draft_preserves_active_and_is_idempotent(self, svc):
        svc.record_plan_result(CID, UID, _itin())
        svc.record_plan_result(CID, UID, _itin(days=2, version=2))

        assert svc.discard(CID, UID, 2) == {
            "status": "ok", "plan_version": 2, "plan_status": "discarded",
        }
        assert svc.discard(CID, UID, 2)["plan_status"] == "discarded"
        assert svc.latest_version(CID, UID)["plan_status"] == "discarded"
        assert svc.active_version(CID, UID)["plan_version"] == 1

    def test_stale_discard_conflicts(self, svc):
        svc.record_plan_result(CID, UID, _itin())
        svc.record_plan_result(CID, UID, _itin(days=2, version=2))
        with pytest.raises(PlanVersionConflict):
            svc.discard(CID, UID, 1)


class TestRestore:
    def _seed(self, svc):
        v1 = svc.record_plan_result(CID, UID, _itin(poi_ids=("p1",)))
        v2 = svc.record_plan_result(CID, UID, _itin(days=2, poi_ids=("p1", "p2"), version=2))
        return v1, v2

    def test_restore_creates_new_version_with_old_content(self, svc):
        self._seed(svc)
        out = svc.restore(CID, UID, target_version=1, base_version=2)
        itin = out["itinerary"]
        # 回滚 = 以旧版内容生成**新**版本（v3），历史 v1/v2 原样保留
        assert itin["plan_version"] == 3
        assert itin["parent_plan_version"] == 2
        assert itin["change_reason"] == "rollback"
        placements = {
            i["poi"]["poi_id"]
            for d in itin["days"] for i in d["items"] if i.get("poi")
        }
        assert placements == {"p1"}
        rec = out["change_record"]
        assert rec["change"]["type"] == "rollback"
        assert rec["change"]["restored_version"] == 1
        assert out["plan_status"] == "waiting_confirmation"

    def test_restore_keeps_target_quality_status(self, svc):
        svc.record_plan_result(CID, UID, _itin(status="degraded"))   # v1
        svc.record_plan_result(CID, UID, _itin(days=2, version=2))   # v2
        out = svc.restore(CID, UID, target_version=1, base_version=2)
        # 内容还原自 v1 → 行程质量状态与 v1 一致（内容相同判定相同）
        assert out["itinerary"]["status"] == "degraded"

    def test_restore_stale_base_conflict(self, svc):
        self._seed(svc)
        with pytest.raises(PlanVersionConflict) as ei:
            svc.restore(CID, UID, target_version=1, base_version=1)
        assert ei.value.current_version == 2

    def test_restore_current_version_invalid(self, svc):
        self._seed(svc)
        with pytest.raises(PlanVersionInvalid):
            svc.restore(CID, UID, target_version=2, base_version=2)

    def test_restore_missing_target_not_found(self, svc):
        self._seed(svc)
        with pytest.raises(PlanVersionNotFound):
            svc.restore(CID, UID, target_version=9, base_version=2)

    def test_restore_concurrent_loses_conflict(self, svc):
        self._seed(svc)
        # 并发胜者已占用 v3：乐观锁失败 → 冲突
        svc._store.save_version(CID, UID, _itin(days=3, version=3))  # type: ignore[attr-defined]
        with pytest.raises(PlanVersionConflict):
            svc.restore(CID, UID, target_version=1, base_version=2)


class TestDiff:
    def test_diff_poi_and_brief(self, svc):
        svc.record_plan_result(CID, UID, _itin(poi_ids=("p1", "p2")))          # v1
        svc.record_plan_result(CID, UID, _itin(days=2, poi_ids=("p2", "p3"), version=2))  # v2
        out = svc.diff(CID, UID, from_version=1, to_version=2)
        assert out["added"] == ["p3"]
        assert out["removed"] == ["p1"]
        assert out["brief_fields"] == ["days"]

    def test_diff_missing_version_not_found(self, svc):
        svc.record_plan_result(CID, UID, _itin())
        with pytest.raises(PlanVersionNotFound):
            svc.diff(CID, UID, from_version=1, to_version=5)


class TestScope:
    def test_other_user_sees_nothing(self, svc):
        svc.record_plan_result(CID, UID, _itin())
        with pytest.raises(PlanVersionNotFound):
            svc.confirm(CID, "user-other", 1)
        assert svc.list_versions(CID, "user-other") == []
        assert svc.latest_version(CID, "user-other") is None

    def test_same_user_and_conversation_are_isolated_by_tenant(self, svc):
        svc.record_plan_result(CID, UID, _itin(), tenant_id="tenant-a")
        assert svc.latest_version(CID, UID, tenant_id="tenant-b") is None


class TestListConversations:
    def test_lists_each_conversation_once_with_latest_meta(self, svc):
        svc.record_plan_result(CID, UID, _itin())                    # conv-1 v1
        svc.record_plan_result(CID, UID, _itin(days=2, version=2))   # conv-1 v2
        svc.record_plan_result("conv-2", UID, _itin(days=3, version=1))  # conv-2 v1
        plans = svc.list_conversations(UID)
        assert [p["conversation_id"] for p in plans] == ["conv-2", "conv-1"]
        by_cid = {p["conversation_id"]: p for p in plans}
        assert by_cid["conv-1"]["plan_version"] == 2
        assert by_cid["conv-1"]["versions_count"] == 2
        assert by_cid["conv-2"]["versions_count"] == 1

    def test_other_user_sees_nothing(self, svc):
        svc.record_plan_result(CID, UID, _itin())
        assert svc.list_conversations("user-other") == []

    def test_empty_user_returns_empty(self, svc):
        svc.record_plan_result(CID, UID, _itin())
        assert svc.list_conversations("") == []

    def test_limit_caps_rows(self, svc):
        for i in range(5):
            svc.record_plan_result(f"conv-{i}", UID, _itin())
        assert len(svc.list_conversations(UID, limit=3)) == 3
