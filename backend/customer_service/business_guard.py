"""customer_service/business_guard.py — Business Operation Unique Guard（Phase3 STOP D）

解决跨 confirmation 的**语义等价业务操作**重复：

  两个不同 confirmation_id / task_id / execution_id 的确认请求，
  在业务语义上是同一个动作（同 tenant + 同 action + 同业务实体 +
  同语义载荷）时不允许同时存在两个有效操作。

与既有四层身份的关系（禁止互相代替）：
  - Business Operation Identity（本模块）：是不是同一个业务动作？
  - confirmation_id：是哪一次确认记录；
  - Phase2 side-effect ledger key（shared/idempotency）：这次副作用
    是否已经做过（含 actor，per-confirmation client_key）；
  - provider key：provider 是否应把重放视为同一外部请求。

硬约束在 PostgreSQL：confirmations 上的 partial unique index
（migration 051）以 (tenant_id, action_type, target_type, target_id,
semantic_fingerprint) 为键、仅覆盖 active 生命周期（pending/confirmed/
executing/verifying）。并发创建由唯一索引决胜，禁止应用层先查后插。

生命周期（§16）映射到既有 confirmation state：
  ACTIVE（占守卫）  = pending / confirmed / executing / verifying
  COMPLETED         = success（terminal duplicate rule 决定能否重发）
  RETRYABLE_RELEASED= failed（无副作用证据时）/ cancelled / expired
  IN_DOUBT_LOCKED   = verifying（STOP C SideEffectOutcomeUnknown；
                      STOP E reconciliation 裁决前持续阻止语义重复）
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from backend.customer_service.errors import CustomerServiceError

# semantic_fingerprint 列宽（SHA-256 hex）
FINGERPRINT_LENGTH = 64

# 语义载荷只取「真正改变业务效果」的字段（§6）：金额/范围/条目集/
# 目标状态/风险级。禁止进入：confirmation_id、action_id、execution_id、
# created_at/expires_at、proposal_text（LLM 措辞）、retry_count、UI 来源、
# trace/request id（§9）——否则第二个 confirmation 永远生成不同 key，
# guard 失效。
_SEMANTIC_PAYLOAD_KEYS = ("amount", "currency", "items", "scope", "quantity")

# risk_level 是 proposal 语义的一部分（STOP C 执行 payload 已认定：
# 「risk_level 是 proposal 语义的一部分：变更 = 不同请求」）
_SEMANTIC_TOP_LEVEL_KEYS = ("risk_level",)


class BusinessOperationGuardError(CustomerServiceError):
    """Business Guard 域错误基类（绝不把 IntegrityError 漏到 API，§53）。"""


class BusinessOperationIdentityMissing(BusinessOperationGuardError):
    """真实业务写缺少 tenant/action/entity/语义身份 —— fail closed（§54/§76）。"""


class BusinessOperationAlreadyActive(BusinessOperationGuardError):
    """语义等价的 active 业务操作已存在（新建/更新被守卫拦截）。

    携带既有操作定位信息（§66 可审计：为什么被挡、谁在挡、什么状态）。
    """

    def __init__(self, existing_confirmation_id: str = "", existing_state: str = "",
                 message: str = ""):
        self.existing_confirmation_id = existing_confirmation_id
        self.existing_state = existing_state
        super().__init__(
            message or "已有处理中的相同业务操作，请勿重复提交。",
            user_message="该操作已在处理中，请勿重复提交；如需修改请先取消当前操作。",
        )


class BusinessOperationConflict(BusinessOperationGuardError):
    """proposal 修改后的语义身份与另一 active 操作等价（§48）——DB 拒绝。"""


class BusinessOperationAlreadyCompleted(BusinessOperationGuardError):
    """终态不可重复动作（如 full refund 已成功）再次发起（§38/D18）。"""


@dataclass(frozen=True)
class BusinessOperationIdentity:
    """业务操作身份（§5）：tenant 必参与，禁止跨租户冲突（§29）。"""

    tenant_id: str
    action_type: str
    target_type: str
    target_id: str
    semantic_fingerprint: str

    def as_columns(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "semantic_fingerprint": self.semantic_fingerprint,
        }


def _normalize_amount(value) -> str | None:
    """金额规范化（§31）：100 / 100.0 / "100.00" → 同一 Canonical 形态。

    Decimal 定点两位（现行金额口径），拒绝 float 不稳定字符串。
    """
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return format(d.quantize(Decimal("0.01")), "f")


def _normalize_semantic_value(key: str, value):
    if value is None:
        return None
    if key in ("amount", "quantity"):
        return _normalize_amount(value)
    if key == "currency" and isinstance(value, str):
        return value.strip().upper()
    if key == "items" and isinstance(value, (list, tuple)):
        # 业务无序集合：排序后 canonical 化（§31 item 顺序无关）
        return sorted(str(v) for v in value)
    if key == "scope" and isinstance(value, str):
        return value.strip().lower()
    return value


def compute_semantic_fingerprint(pending_action: dict) -> str:
    """确定性语义指纹（§7）：same semantic request → same fingerprint。

    输入：pending_action（proposal dict）。取 action/target 身份 +
    语义载荷子集；JSON key 顺序、无关字段差异不影响结果。
    禁止包含 confirmation_id / execution_id / 时间戳（§49/§50）。
    """
    semantic: dict = {
        "action_type": str(pending_action.get("action_type", "")),
        "target_type": str(pending_action.get("target_type", "")),
        "target_id": str(pending_action.get("target_id", "")),
    }
    proposal = pending_action.get("proposal") if isinstance(
        pending_action.get("proposal"), dict) else pending_action
    for key in _SEMANTIC_PAYLOAD_KEYS:
        # 语义载荷可能在 proposal 顶层或 before/after_state 中
        candidates = (
            proposal.get(key),
            (proposal.get("before_state") or {}).get(key),
            (proposal.get("after_state") or {}).get(key),
        )
        for value in candidates:
            normalized = _normalize_semantic_value(key, value)
            if normalized is not None:
                semantic[key] = normalized
                break
    for key in _SEMANTIC_TOP_LEVEL_KEYS:
        value = proposal.get(key)
        if value:
            semantic[key] = str(value)
    target_state = (proposal.get("after_state") or {}).get("status")
    if target_state:
        semantic["target_state"] = str(target_state)
    canonical = json.dumps(semantic, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def compute_identity(pending_action: dict, tenant_id: str) -> BusinessOperationIdentity | None:
    """从 pending_action 提取业务操作身份。

    返回 None = 身份不完整（need_info 补槽占位行：target_id 为空），
    该行不参与唯一守卫（migration 051 谓词排除）；升级为正式 proposal
    时身份补全并接受约束。

    完整身份字段缺失且已是正式 proposal → fail closed（§54/§76），
    禁止以 NULL fingerprint 绕过守卫继续创建。
    """
    action_type = str(pending_action.get("action_type", "") or "")
    target_type = str(pending_action.get("target_type", "") or "")
    target_id = str(pending_action.get("target_id", "") or "")
    if not target_id.strip():
        return None  # 补槽占位：非业务写，不参与守卫
    if not action_type or not target_type:
        raise BusinessOperationIdentityMissing(
            f"业务操作身份缺失: action_type={action_type!r} "
            f"target_type={target_type!r}（fail closed）")
    fingerprint = compute_semantic_fingerprint(pending_action)
    return BusinessOperationIdentity(
        tenant_id=tenant_id, action_type=action_type,
        target_type=target_type, target_id=target_id,
        semantic_fingerprint=fingerprint)


# ── 终态重发策略（§38/§24：规则驱动，禁止 LLM 判冲突）──────────────
# SUCCESS 后绝对不允许同语义再次发起的动作（业务效果不可重复）：
#   refund_request —— 全额退款已成功，同单同语义退款不允许再发；
# 其余动作（return/exchange/address/password/order_cancel）在 simulated
# 业务下无实体状态变化，重复发起合法或由业务 eligibility 把关，不列入。
TERMINAL_BLOCKED_ACTIONS = frozenset({"refund_request"})


def terminal_blocked(action_type: str) -> bool:
    return action_type in TERMINAL_BLOCKED_ACTIONS
