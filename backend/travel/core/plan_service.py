"""travel/core/plan_service.py — 行程版本生命周期编排（方案 v2 §5/§7）

plan_store 之上的业务规则层，职责：
  - record_plan_result：/plan 成功后落账本并派生 change_record（确定性 diff，
    G2 禁手写）；账本故障软降级，绝不挡规划主链。
  - confirm：确认整份行程 —— 生命周期迁移经 plan_lifecycle.transition
    fail-fast 校验（waiting_confirmation → confirmed），CAS 由 store 单语句
    条件 UPDATE 保证；确认非当前版 → 冲突（附当前版本号，前端提示
    「行程已更新到 vX」）。
  - restore：恢复历史版本 —— 以目标旧版**内容**生成新版本（版本链 parent
    指向当前最新版），永不删除/倒转历史；base_version 乐观并发校验，并发
    提交只有一个胜者。checkpoint 的 time travel 不参与业务撤销（fork 会重跑
    节点，语义不同）。
  - diff：两版确定性差异（POI 分布 + brief 字段），供恢复/修改预览。

store 经构造注入：生产传 plan_store（PG），测试传内存实现 —— SQL 层不做
单元测试对象（薄封装，真实 PG 行为另有集成验证）。
"""
from __future__ import annotations

from typing import Any

from backend.shared.logger import logger
from backend.travel.core import plan_store
from backend.travel.core.plan_diff import (
    build_change_record,
    diff_poi_placements,
    poi_placements,
    rollback_record,
)
from backend.travel.core.plan_lifecycle import (
    IllegalPlanTransition,
    TravelPlanStatus,
    transition,
)
from backend.travel.models.itinerary import CHANGE_ROLLBACK, Itinerary

# 首版无父版的占位 parent 版本号（账本内口径，不出网）
_ROOT_PARENT_VERSION = 0


class PlanVersionNotFound(Exception):
    """版本不存在 / 越权 / 账本不可用（对外一律 404，不泄露存在性）。"""


class PlanVersionConflict(Exception):
    """乐观并发失败：base_version 已过期或目标态不可达（对外 409）。"""

    def __init__(self, message: str, *, current_version: int | None = None):
        super().__init__(message)
        self.current_version = current_version


class PlanVersionInvalid(Exception):
    """语义非法请求（如恢复当前版）。"""


class PlanVersionPersistenceError(Exception):
    """行程账本不可用；行程不得作为成功结果返回。"""


def _lifecycle_status(raw: str) -> TravelPlanStatus:
    return TravelPlanStatus(raw)


class PlanVersionService:
    """版本生命周期编排（无状态，store 注入）。"""

    def __init__(self, store: Any = plan_store):
        self._store = store

    # ------------------------------------------------------------
    # /plan 成功后：落账本 + 派生变更记录
    # ------------------------------------------------------------
    def record_plan_result(
        self, conversation_id: str, user_id: str, itinerary: dict,
        *, tenant_id: str = "default",
    ) -> dict:
        """落账并返回 Active/Draft 投影；无法落账就抛错，不假成功。"""
        if not conversation_id or not user_id or not tenant_id:
            raise PlanVersionPersistenceError("行程缺少持久化会话或身份范围")
        if not self._store.enabled():
            raise PlanVersionPersistenceError("行程版本账本未启用")
        try:
            prev = self._store.latest_version(
                conversation_id, user_id, tenant_id, strict=True)
            for _attempt in range(3):
                if prev and prev.get("plan_status") == "waiting_confirmation":
                    raise PlanVersionConflict(
                        "当前已有待确认草案，请先应用或放弃后再生成新行程",
                        current_version=int(prev["plan_version"]),
                    )
                if not prev:
                    plan_status = "confirmed"
                elif prev.get("plan_status") == "discarded":
                    active = self._store.active_version(
                        conversation_id, user_id, tenant_id, strict=True)
                    plan_status = (
                        "waiting_confirmation" if active else "confirmed")
                else:
                    plan_status = "waiting_confirmation"
                if prev:
                    latest_version = int(prev["plan_version"])
                    incoming_version = int(itinerary.get("plan_version") or 1)
                    if incoming_version <= latest_version:
                        # 旧 checkpoint 恢复后，版本必须在持久化边界重新接续。
                        itinerary["parent_plan_version"] = latest_version
                        itinerary["plan_version"] = latest_version + 1
                        logger.warning(
                            "[TravelPlanService] 修正过期行程版本: incoming=%s latest=%s next=%s",
                            incoming_version,
                            latest_version,
                            itinerary["plan_version"],
                        )
                if prev and prev.get("itinerary"):
                    # brief_fields 由两版 brief 确定性派生（G2），不信任新行程
                    # 自述的 changed_fields（修复/恢复链路不写该字段）
                    brief_fields = _brief_changed_fields(
                        prev["itinerary"].get("brief") or {},
                        itinerary.get("brief") or {},
                    )
                else:
                    brief_fields = list(itinerary.get("changed_fields") or [])
                candidate_change = build_change_record(
                    new_version=int(itinerary.get("plan_version") or 1),
                    parent_version=(
                        prev["plan_version"] if prev else _ROOT_PARENT_VERSION
                    ),
                    old_itinerary=prev["itinerary"] if prev else None,
                    new_itinerary=itinerary,
                    brief_changed_fields=brief_fields,
                    change_reason=str(itinerary.get("change_reason") or ""),
                    quality=str(itinerary.get("status") or ""),
                )
                saved = self._store.save_version(
                    conversation_id, user_id, itinerary, plan_status=plan_status,
                    change=candidate_change, tenant_id=tenant_id, strict=True,
                )
                if saved:
                    return {"plan_status": plan_status,
                            "change_record": candidate_change}

                # 版本主键冲突说明并发请求已经抢先写入；从持久化层重读
                # latest 后继续接号，避免败方把旧 checkpoint 的版本带回去。
                latest_after_conflict = self._store.latest_version(
                    conversation_id, user_id, tenant_id, strict=True,
                )
                if (
                    latest_after_conflict
                    and int(latest_after_conflict["plan_version"])
                    >= int(itinerary.get("plan_version") or 1)
                ):
                    prev = latest_after_conflict
                    continue
                raise PlanVersionPersistenceError("行程版本冲突且无法重新读取账本")
            raise PlanVersionPersistenceError("行程版本重试次数已耗尽")
        except (PlanVersionConflict, PlanVersionPersistenceError):
            raise
        except Exception as e:  # noqa: BLE001 — 账本故障拒绝假成功
            logger.warning("[TravelPlanService] 版本落账失败（拒绝假成功）: %s", e)
            raise PlanVersionPersistenceError("行程版本账本读写失败") from e

    # ------------------------------------------------------------
    # 确认整份行程
    # ------------------------------------------------------------
    def confirm(
        self, conversation_id: str, user_id: str, plan_version: int,
        *, tenant_id: str = "default",
    ) -> dict:
        latest = self._store.latest_version(conversation_id, user_id, tenant_id)
        if not latest:
            raise PlanVersionNotFound("无可用行程版本")
        if latest["plan_version"] != plan_version:
            raise PlanVersionConflict(
                f"行程已更新到 v{latest['plan_version']}，请确认当前版本",
                current_version=latest["plan_version"],
            )
        try:
            # waiting_confirmation → confirmed；重复确认在此 fail-fast
            transition(_lifecycle_status(latest["plan_status"]),
                       TravelPlanStatus.CONFIRMED)
        except IllegalPlanTransition as e:
            raise PlanVersionConflict(str(e),
                                      current_version=latest["plan_version"]) from e
        result = self._store.confirm_version(
            conversation_id, user_id, plan_version, tenant_id)
        if result is None:
            # latest 读取与 UPDATE 之间被并发改走 —— 按冲突处理（重查后重试）
            raise PlanVersionConflict(
                "确认请求与并发修改冲突，请刷新后重试",
                current_version=latest["plan_version"],
            )
        return {"status": "ok", "plan_version": plan_version,
                "plan_status": result}

    def discard(
        self, conversation_id: str, user_id: str, plan_version: int,
        *, tenant_id: str = "default",
    ) -> dict:
        """将当前最新草案原子标记为 discarded；重复放弃保持幂等。"""
        latest = self._store.latest_version(conversation_id, user_id, tenant_id)
        if not latest:
            raise PlanVersionNotFound("无可用行程版本")
        current = int(latest["plan_version"])
        if current != plan_version:
            raise PlanVersionConflict(
                f"行程已更新到 v{current}，请刷新后重试",
                current_version=current,
            )
        if latest["plan_status"] not in {"waiting_confirmation", "discarded"}:
            raise PlanVersionConflict(
                "当前版本不是可放弃的待确认草案", current_version=current)
        result = self._store.discard_version(
            conversation_id, user_id, plan_version, tenant_id)
        if result is None:
            latest_after = self._store.latest_version(
                conversation_id, user_id, tenant_id)
            raise PlanVersionConflict(
                "放弃请求与并发修改冲突，请刷新后重试",
                current_version=(int(latest_after["plan_version"])
                                 if latest_after else None),
            )
        return {"status": "ok", "plan_version": plan_version,
                "plan_status": result}

    # ------------------------------------------------------------
    # 恢复历史版本
    # ------------------------------------------------------------
    def restore(
        self,
        conversation_id: str,
        user_id: str,
        *,
        target_version: int,
        base_version: int,
        tenant_id: str = "default",
    ) -> dict:
        latest = self._store.latest_version(conversation_id, user_id, tenant_id)
        if not latest:
            raise PlanVersionNotFound("无可用行程版本")
        if latest["plan_version"] != base_version:
            raise PlanVersionConflict(
                f"行程已更新到 v{latest['plan_version']}，请基于当前版本重试",
                current_version=latest["plan_version"],
            )
        if target_version == base_version:
            raise PlanVersionInvalid("目标版本就是当前版本")
        target = self._store.get_version(
            conversation_id, user_id, target_version, tenant_id)
        if not target:
            raise PlanVersionNotFound(
                f"v{target_version} 不在可恢复范围内（版本历史保留最近条目）")

        target_itin = Itinerary.model_validate(target["itinerary"])
        parent_itin = Itinerary.model_validate(latest["itinerary"])
        # 内容整体还原自 target 版：数据快照与其保持一致，而非继承当前版
        target_itin.stamp_version(
            target_itin.brief,
            reason=CHANGE_ROLLBACK,
            parent=parent_itin,
            data_snapshot=target_itin.data_snapshot_version,
            changed_fields=[f"restored_v{target_version}"],
        )
        # 恢复内容的行程质量状态与目标版一致（内容相同则判定相同）
        target_itin.status = str(target["itinerary"].get("status") or "")

        # 生命周期：confirmed 后的任何出新版本都回到待确认（冻结迁移表）；
        # waiting → waiting 属同态版本更替，不构成迁移。
        try:
            current = _lifecycle_status(latest["plan_status"])
            if current is not TravelPlanStatus.WAITING_CONFIRMATION:
                transition(current, TravelPlanStatus.WAITING_CONFIRMATION)
        except IllegalPlanTransition as e:
            raise PlanVersionConflict(str(e),
                                      current_version=latest["plan_version"]) from e

        change = rollback_record(
            new_version=target_itin.plan_version,
            parent_version=base_version,
            restored_version=target_version,
        )
        dump = target_itin.model_dump(mode="json")
        saved = self._store.save_version(
            conversation_id, user_id, dump,
            plan_status="waiting_confirmation", change=change,
            tenant_id=tenant_id,
        )
        if not saved:
            # 版本号被并发胜者占用 —— 乐观锁失败
            raise PlanVersionConflict(
                "恢复请求与并发修改冲突，请刷新后重试",
                current_version=base_version,
            )
        return {"status": "ok", "itinerary": dump,
                "plan_status": "waiting_confirmation", "change_record": change}

    # ------------------------------------------------------------
    # 版本历史与差异
    # ------------------------------------------------------------
    def list_versions(
        self, conversation_id: str, user_id: str, *, tenant_id: str = "default"
    ) -> list[dict]:
        return self._store.list_versions(conversation_id, user_id, tenant_id)

    def list_conversations(
        self, user_id: str, *, limit: int = 30, tenant_id: str = "default"
    ) -> list[dict]:
        """某用户的历史规划列表（每会话最新版元数据）；账本不可用返回空列表，
        前端按「无历史」渲染，隐藏入口。"""
        if not user_id:
            return []
        return self._store.list_conversations(
            user_id, limit=limit, tenant_id=tenant_id)

    def latest_version(
        self, conversation_id: str, user_id: str, *, tenant_id: str = "default",
        strict: bool = False,
    ) -> dict | None:
        """当前最新版本（含完整 itinerary）；无账本/越权返回 None。"""
        if strict:
            return self._store.latest_version(
                conversation_id, user_id, tenant_id, strict=True)
        return self._store.latest_version(conversation_id, user_id, tenant_id)

    def active_version(
        self, conversation_id: str, user_id: str, *, tenant_id: str = "default",
        strict: bool = False,
    ) -> dict | None:
        """当前 Active 版本；waiting_confirmation 草案不覆盖已确认内容。"""
        if strict:
            return self._store.active_version(
                conversation_id, user_id, tenant_id, strict=True)
        return self._store.active_version(conversation_id, user_id, tenant_id)

    def diff(
        self, conversation_id: str, user_id: str,
        *, from_version: int, to_version: int, tenant_id: str = "default",
    ) -> dict:
        a = self._store.get_version(
            conversation_id, user_id, from_version, tenant_id)
        b = self._store.get_version(
            conversation_id, user_id, to_version, tenant_id)
        if not a or not b:
            raise PlanVersionNotFound("版本不存在或不在保留窗口内")
        poi_diff = diff_poi_placements(
            poi_placements(a["itinerary"]), poi_placements(b["itinerary"]))
        return {
            "from_version": from_version,
            "to_version": to_version,
            **poi_diff,
            "brief_fields": _brief_changed_fields(
                a["itinerary"].get("brief") or {},
                b["itinerary"].get("brief") or {},
            ),
        }


def _brief_changed_fields(old: dict, new: dict) -> list[str]:
    """两版 brief 的字段级差异键（排序保证确定性；version 是簿记字段不算差异，
    与 slot_filler 指纹 changed_fields 同语义）。"""
    return [
        key for key in sorted(set(old) | set(new))
        if key != "version" and old.get(key) != new.get(key)
    ]


# 进程内单例（路由层直接 import 使用；store 为模块级函数集，无连接常驻）
plan_version_service = PlanVersionService()
