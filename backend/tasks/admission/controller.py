"""tasks/admission/controller.py — AdmissionController（Phase2 Step4 唯一准入入口）。

职责边界：
- AdmissionController：现在是否允许这个 execution 进入执行系统（容量裁决）
- QueueRouter：去哪里执行（workflow→workload_class→物理队列）
  —— workload_class 一律消费 QueueRouter.resolve 结果，本模块不建第二份
     workload 映射（G2 / 规格 §二十四）
- execution lease（task_service.try_acquire_lease）：同一任务的执行权
  互斥 + fencing 写权。admission token 只证明"占用一个容量槽位"，
  不提供任何业务状态写权（概念分离，规格 §五/§二十一）

调用时序（Worker 执行起点，规格 §十六"RUNNING limit"语义）：
  try_acquire_lease 成功 → acquire_for_execution（本模块）
    → allowed=False（容量满 / fail-closed）→ 调用方 defer：释放租约回
      PENDING + 消息 countdown 重投（bounded+jitter+budget）
    → allowed=True → 执行（lease heartbeat 顺带 renew token）
  终态落库后 → release_for_execution（owner CAS，幂等）

Fail 行为（规格 §十二 fail-closed）：
  store 不可用/异常 → fail_mode=closed（默认）按拒绝处理，任务走 defer
  重投（Redis 恢复后自动放行）；fail_mode=open 为显式 break-glass，
  放行 + ERROR 级观测（fallback=True），绝不静默。
"""
from __future__ import annotations

import random
import time
from typing import Any

from backend.shared.logger import logger
from backend.tasks.admission.models import (
    REASON_ADMITTED,
    REASON_DISABLED,
    REASON_INTERNAL_ERROR,
    REASON_REDIS_UNAVAILABLE,
    AdmissionDecision,
)
from backend.tasks.admission.policy import FAIL_MODE_OPEN, AdmissionPolicy, get_policy
from backend.tasks.admission.store import AdmissionStore
from backend.tasks.queue_router import QueueRoute

# scope → 拒绝原因（models 词表映射）
_SCOPE_REASONS = {
    "global": "global_limit",
    "tenant": "tenant_limit",
    "user": "user_limit",
    "workflow": "workflow_limit",
}

_store: AdmissionStore | None = None


def get_store() -> AdmissionStore:
    """进程级 store 单例（前缀来自 config；测试可 monkeypatch _store）。"""
    global _store
    if _store is None:
        from backend.config.tasks import TASK_ADMISSION_KEY_PREFIX

        _store = AdmissionStore(TASK_ADMISSION_KEY_PREFIX)
    return _store


def _identity(record: Any) -> tuple[str, str, str]:
    """(tenant_id, user_id, workflow) —— 身份链唯一权威 = tasks 行。

    tasks 行 tenant_id 缺省 'default'、user_id 由创建路径强制写入
    （resolve_identity / rag_upload actor），此处不做静默兜底改写。
    """
    tenant_id = getattr(record, "tenant_id", "") or "default"
    user_id = getattr(record, "user_id", "") or "-"
    workflow = getattr(record, "workflow", None) or getattr(
        record, "graph_name", "") or "main"
    return tenant_id, user_id, workflow


def _workload_class(workflow: str) -> str:
    """workload 事实来自 QueueRouter（消费而非重建映射）。"""
    try:
        from backend.tasks.queue_router import resolve_for_workflow

        route: QueueRoute = resolve_for_workflow(workflow)
        return route.workload_class
    except Exception:
        # 未知 workflow 在 dispatch 侧由 QueueRouter fail-closed 拦截，
        # admission 不吞不转（规格 Case R）；此处仅在日志口径上兜底。
        return "unknown"


def _metrics():
    try:
        from backend.observability import metrics as m

        return m
    except Exception:
        return None


class AdmissionController:
    """四层并发准入裁决器（进程内无状态，事实源在 Redis store）。"""

    def __init__(self, policy: AdmissionPolicy | None = None,
                 store: AdmissionStore | None = None):
        self._policy = policy
        self._store = store

    @property
    def policy(self) -> AdmissionPolicy:
        return self._policy or get_policy()

    @property
    def store(self) -> AdmissionStore:
        return self._store or get_store()

    # ── acquire ────────────────────────────────────────────────
    def acquire_for_execution(
        self,
        task_id: str,
        *,
        owner_execution_id: str,
        record: Any,
        dispatch_stage: str = "execute",
    ) -> AdmissionDecision:
        """执行起点准入（lease 认领成功后调用；owner = 本次 execution_id）。"""
        policy = self.policy
        tenant_id, user_id, workflow = _identity(record)
        workload_class = _workload_class(workflow)
        m = _metrics()

        if not policy.enabled:
            self._log(task_id, workflow, tenant_id, user_id, dispatch_stage,
                      decision="allowed", reason=REASON_DISABLED)
            return AdmissionDecision(allowed=True, reason=REASON_DISABLED)

        t0 = time.monotonic()
        try:
            result = self.store.acquire(
                task_id=task_id, owner_execution_id=owner_execution_id,
                tenant_id=tenant_id, user_id=user_id, workflow=workflow,
                workload_class=workload_class,
                limits=policy.limits_for(workflow),
                ttl_seconds=policy.token_ttl_seconds,
                dispatch_stage=dispatch_stage)
        except Exception as e:  # store 不可达 / Lua 异常：fail-mode 裁决
            latency_ms = (time.monotonic() - t0) * 1000
            if policy.fail_mode == FAIL_MODE_OPEN:
                logger.error(
                    "[Admission] event=task_admission task_id=%s workflow=%s "
                    "tenant_id=%s user_id=%s stage=%s decision=allowed "
                    "reason=fail_open fallback=true error=%s duration_ms=%.1f",
                    task_id, workflow, tenant_id, user_id, dispatch_stage,
                    e, latency_ms)
                if m:
                    m.task_admission_requests_total.labels(
                        workflow=workflow, stage=dispatch_stage).inc()
                    m.task_admission_allowed_total.labels(
                        workflow=workflow, kind="fallback").inc()
                return AdmissionDecision(
                    allowed=True, reason=REASON_INTERNAL_ERROR,
                    fallback=True, error=str(e))
            logger.error(
                "[Admission] event=task_admission task_id=%s workflow=%s "
                "tenant_id=%s user_id=%s stage=%s decision=rejected "
                "reason=%s error=%s duration_ms=%.1f",
                task_id, workflow, tenant_id, user_id, dispatch_stage,
                REASON_INTERNAL_ERROR, e, latency_ms)
            if m:
                m.task_admission_requests_total.labels(
                    workflow=workflow, stage=dispatch_stage).inc()
                m.task_admission_rejected_total.labels(
                    workflow=workflow, scope="global",
                    reason=REASON_INTERNAL_ERROR).inc()
            return AdmissionDecision(
                allowed=False, reason=REASON_INTERNAL_ERROR,
                error=str(e))

        latency_ms = (time.monotonic() - t0) * 1000
        if m:
            m.task_admission_acquire_latency_seconds.observe(latency_ms / 1000)
            m.task_admission_requests_total.labels(
                workflow=workflow, stage=dispatch_stage).inc()

        if result["allowed"]:
            if result["kind"] in ("new", "takeover"):
                # 准入成功（含接管）：清掉历史 defer 计数（退避周期结束）
                try:
                    self.store.clear_deferred(task_id)
                except Exception:
                    pass
            self._log(task_id, workflow, tenant_id, user_id, dispatch_stage,
                      decision="allowed", reason=REASON_ADMITTED,
                      token_id=result["token_id"], kind=result["kind"],
                      current=0, limit=0)
            if m:
                kind = result["kind"] if result["kind"] in ("new", "takeover") \
                    else "new"
                m.task_admission_allowed_total.labels(
                    workflow=workflow, kind=kind).inc()
                try:
                    m.record_admission_active_global(
                        self.store.scope_active("global"))
                except Exception:
                    pass
            return AdmissionDecision(allowed=True, reason=REASON_ADMITTED,
                                     token_id=result["token_id"],
                                     takeover=result["kind"] == "takeover")

        scope = result["scope"]
        reason = _SCOPE_REASONS.get(scope, REASON_INTERNAL_ERROR)
        self._log(task_id, workflow, tenant_id, user_id, dispatch_stage,
                  decision="rejected", reason=reason, scope=scope,
                  current=result["current"], limit=result["limit"])
        if m:
            m.task_admission_rejected_total.labels(
                workflow=workflow, scope=scope, reason=reason).inc()
        return AdmissionDecision(allowed=False, reason=reason, scope=scope,
                                 current=result["current"],
                                 limit=result["limit"])

    # ── release / renew（owner CAS）────────────────────────────
    def release_for_execution(self, task_id: str, owner_execution_id: str, *,
                              reason: str = "terminal") -> bool:
        """终态释放（幂等；token 已被接管/过期时零副作用返回 False）。"""
        token = self.store.get_token(task_id)
        if not token:
            if not self.policy.enabled:
                return False
            # 无 token：enabled 部署下正常终态都应先 acquire 过——
            # missing 属于 TTL 已自愈或降级路径，记 debug 即可
            logger.debug("[Admission] release missing token task_id=%s "
                         "(reason=%s)", task_id, reason)
            _m = _metrics()
            if _m:
                _m.task_admission_release_total.labels(result="missing").inc()
            return False
        try:
            result = self.store.release(
                task_id=task_id, owner_execution_id=owner_execution_id,
                tenant_id=token.get("tenant_id", ""),
                user_id=token.get("user_id", ""),
                workflow=token.get("workflow", ""))
        except Exception:
            logger.warning("[Admission] release error task_id=%s reason=%s",
                           task_id, reason, exc_info=True)
            _m = _metrics()
            if _m:
                _m.task_admission_release_total.labels(result="error").inc()
            return False
        logger.info("[Admission] event=task_admission_release task_id=%s "
                    "token_id=%s reason=%s result=%s",
                    task_id, token.get("token_id", ""), reason, result)
        _m = _metrics()
        if _m:
            _m.task_admission_release_total.labels(result=result).inc()
            if result == "released":
                try:
                    _m.record_admission_active_global(
                        self.store.scope_active("global"))
                except Exception:
                    pass
        return result == "released"

    def renew_for_execution(self, task_id: str, owner_execution_id: str) -> bool:
        """heartbeat 续期（跟随 lease renew 成功后调用；失败只影响 TTL，
        不代表执行权丢失——执行权权威在 lease）。"""
        token = self.store.get_token(task_id)
        if not token:
            return False
        try:
            return self.store.renew(
                task_id=task_id, owner_execution_id=owner_execution_id,
                tenant_id=token.get("tenant_id", ""),
                user_id=token.get("user_id", ""),
                workflow=token.get("workflow", ""),
                ttl_seconds=self.policy.token_ttl_seconds) == "renewed"
        except Exception:
            logger.debug("[Admission] renew error task_id=%s", task_id,
                         exc_info=True)
            return False

    # ── defer（满载延迟准入）──────────────────────────────────
    def note_deferred(self, task_id: str, *, workflow: str = "") -> float:
        """记一次 defer 并返回本次重投延迟（bounded 指数退避 + jitter）。"""
        count = self.store.note_deferred(task_id)
        delay = float(min(
            _env_defer_initial() * (2 ** max(0, count - 1)),
            _env_defer_max()))
        if _env_defer_jitter():
            delay *= random.uniform(0.75, 1.25)
        delay = max(1.0, delay)
        _m = _metrics()
        if _m:
            _m.task_admission_defer_total.labels(
                workflow=workflow or "unknown").inc()
        self._log(task_id, workflow, "", "", "defer", decision="deferred",
                  reason="capacity_full", defer_count=count,
                  delay_s=round(delay, 1))
        return delay

    def defer_budget_exhausted(self, task_id: str) -> bool:
        """defer 重投是否已超 budget（超限 → 调用方落 FAILED 终态）。

        store 不可用时返回 0 计数 → False（defer 由 Redis 恢复后自然
        继续；budget 语义依赖 store 存活，store 长挂时 broker 兜底接管）。
        """
        return self.store.get_deferred(task_id) > _env_defer_max_count()

    # ── 对账（规格 §三十九；挂 zombie_reconcile beat，不新增调度器）──
    def reconcile_admission_state(self) -> dict:
        """识别并修正三类漂移：过期残留 / token 在但任务已终态 /
        counter 与 token 不一致（以 token 集合为重建依据）。"""
        store = self.store
        expired = store.sweep_expired()
        stale_released: list[str] = []
        inconsistencies = 0
        active = store.scan_active_task_ids()
        if active:
            from backend.services import task_service
            from backend.models.task import TaskStatus

            for task_id in active:
                record = task_service.get_task(task_id)
                if record is None:
                    # 任务行已被清理（保留期外）：残留 token 直接释放
                    inconsistencies += 1
                    token = store.get_token(task_id)
                    if self.release_for_execution(
                            task_id, token.get("owner_execution_id", "")
                            if token else "", reason="reconcile_row_missing"):
                        stale_released.append(task_id)
                    continue
                if record.status in TaskStatus.terminal() or record.status in (
                        TaskStatus.PAUSED, TaskStatus.WAITING_USER):
                    token = store.get_token(task_id)
                    released = self.release_for_execution(
                        task_id, token.get("owner_execution_id", "")
                        if token else "",
                        reason="reconcile_terminal")
                    if released:
                        stale_released.append(task_id)
        _m = _metrics()
        if _m:
            _m.task_admission_expired_total.inc(expired)
        if expired or stale_released or inconsistencies:
            logger.warning(
                "[Admission] reconcile: expired=%s stale_released=%s "
                "inconsistencies=%s active=%s", expired, stale_released,
                inconsistencies, len(active))
        return {"expired": expired, "stale_released": stale_released,
                "inconsistencies": inconsistencies, "active": len(active)}

    # ── 观测 ───────────────────────────────────────────────────
    @staticmethod
    def _log(task_id: str, workflow: str, tenant_id: str, user_id: str,
             stage: str, *, decision: str, reason: str, scope: str = "",
             current: int | None = None, limit: int | None = None,
             token_id: str = "", kind: str = "", defer_count: int = 0,
             delay_s: float = 0.0) -> None:
        logger.info(
            "[Admission] event=task_admission task_id=%s workflow=%s "
            "tenant_id=%s user_id=%s stage=%s decision=%s reason=%s "
            "scope=%s current=%s limit=%s token_id=%s kind=%s "
            "defer_count=%s delay_s=%s",
            task_id, workflow, tenant_id, user_id, stage, decision, reason,
            scope or "-", current if current is not None else "-",
            limit if limit is not None else "-", token_id or "-", kind or "-",
            defer_count or "-", delay_s or "-")


# ── defer 退避参数（config 读取；独立函数便于测试 monkeypatch）────
def _env_defer_initial() -> int:
    from backend.config.tasks import TASK_ADMISSION_DEFER_INITIAL_DELAY

    return TASK_ADMISSION_DEFER_INITIAL_DELAY


def _env_defer_max() -> int:
    from backend.config.tasks import TASK_ADMISSION_DEFER_MAX_DELAY

    return TASK_ADMISSION_DEFER_MAX_DELAY


def _env_defer_max_count() -> int:
    from backend.config.tasks import TASK_ADMISSION_DEFER_MAX_COUNT

    return TASK_ADMISSION_DEFER_MAX_COUNT


def _env_defer_jitter() -> bool:
    from backend.config.tasks import TASK_ADMISSION_DEFER_JITTER

    return TASK_ADMISSION_DEFER_JITTER


# 模块级单例（调用方 from ... import get_admission_controller）
_controller: AdmissionController | None = None


def get_admission_controller() -> AdmissionController:
    global _controller
    if _controller is None:
        _controller = AdmissionController()
    return _controller
