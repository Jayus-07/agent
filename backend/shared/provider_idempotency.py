"""shared/provider_idempotency.py — Provider 幂等契约层（Phase3 STOP C）。

定位（任务书 §49）：**provider semantics layer**，不是第二套 idempotency。
claim/lease/complete/retry/IN_DOUBT 全部由 Phase2 PG durable ledger
（backend/shared/idempotency.py）承担；本模块只回答三件事：

1. 这个 provider 的幂等**能力**是什么（集中 registry，唯一事实源）；
2. logical effect key 如何**确定性派生**出 provider key（与 execution/retry/
   delivery 无关）；
3. UNKNOWN outcome 在不同能力下走哪条**安全路径**（same-key retry /
   reconcile first / IN_DOUBT）。

执行决策矩阵（冻结语义）：
    | native | lookup | outcome   | 行为                    |
    | yes    | any    | UNKNOWN   | same-key retry（SAFE_RETRY）|
    | no     | yes    | UNKNOWN   | reconcile first          |
    | no     | no     | UNKNOWN   | IN_DOUBT（SideEffectOutcomeUnknown）|
    | unknown| any    | any write | fail closed（拒绝执行）  |

证据纪律（§28）：能力必须有来源（协议文档/adapter 代码/沙箱实测），
无证据一律 UNKNOWN，UNKNOWN 一律 fail-closed。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum

from backend.shared.idempotency import (
    SideEffectOutcomeUnknown,
    canonical_fingerprint,
)
from backend.shared.logger import logger


class ProviderContractMissing(RuntimeError):
    """provider 未在集中 registry 登记契约（fail-closed，拒绝执行外部写）。"""


class NativeIdempotencySupport(str, Enum):
    """provider 原生幂等支持程度；UNKNOWN 默认 fail-closed。"""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class UnknownResultPolicy(str, Enum):
    """UNKNOWN outcome 的安全路径（冻结三选一）。"""

    SAFE_RETRY = "safe_retry"              # native 幂等：同 key 重放安全
    RECONCILE_FIRST = "reconcile_first"    # 可查询：先对账再决定
    IN_DOUBT = "in_doubt"                  # 不可查询：保守阻断+人工


class IdempotencyKeyTransport(str, Enum):
    """provider key 的传递载体。"""

    HEADER = "header"
    BODY = "body"
    QUERY = "query"
    MESSAGE_ATTRIBUTE = "message_attribute"
    NONE = "none"


class ProviderEffectOutcome(str, Enum):
    """单次 provider 调用的副作用边界判定（任务书 §14/§15）。

    NOT_SENT  明确未越过 provider 边界（连接/认证/明确拒收）→ 可安全 retry
    REJECTED  provider 明确拒绝且无副作用 → 按错误分类处理（不自动 retry）
    SUCCEEDED 确认生效 → complete ledger
    UNKNOWN   可能已生效但结果不确定 → 按 unknown_result_policy 决策
    """

    NOT_SENT = "not_sent"
    REJECTED = "rejected"
    SUCCEEDED = "succeeded"
    UNKNOWN = "unknown"


class ReconciliationResult(str, Enum):
    """status lookup 的结构化结论（primitive 层，不做 admin/状态机决策）。"""

    KNOWN_SUCCESS = "known_success"
    KNOWN_FAILURE = "known_failure"
    NOT_FOUND_SAFE_TO_RETRY = "not_found_safe_to_retry"
    IN_DOUBT = "in_doubt"


@dataclass(frozen=True)
class ProviderIdempotencyCapabilities:
    """provider 幂等能力声明（集中 registry 的唯一事实源条目）。"""

    provider_name: str
    native_support: NativeIdempotencySupport
    key_transport: IdempotencyKeyTransport
    key_header_name: str = ""
    scope: str = "operation"                 # account/endpoint/tenant/operation
    retention_seconds: int | None = None     # None = 未知（不声称窗口）
    same_key_same_payload_required: bool = True
    status_lookup_supported: bool = False
    status_lookup_identity: str = ""         # merchant_reference/operation_id...
    duplicate_response_recognizable: bool = False
    unknown_result_policy: UnknownResultPolicy = UnknownResultPolicy.IN_DOUBT
    evidence: str = ""                       # 能力证据来源（§28 纪律）
    activation_gate: bool = False            # True = 契约冻结但生产写未激活

    def __post_init__(self) -> None:
        # §5 红线：UNKNOWN 能力不允许声明 SAFE_RETRY（unknown≠supported-but-retry）
        if (self.native_support is NativeIdempotencySupport.UNKNOWN
                and self.unknown_result_policy is UnknownResultPolicy.SAFE_RETRY):
            raise ValueError(
                f"provider {self.provider_name!r}: UNKNOWN 能力禁止 SAFE_RETRY 策略"
                "（fail-closed 红线）")
        # UNKNOWN/UNSUPPORTED 的载体语义区分：
        #   UNSUPPORTED + 载体 = 矛盾声明（协议没有键，禁止声明传输）
        #   UNKNOWN + 载体 = 契约冻结的目标载体（activation gate 未解除），
        #                    合法——business_service_http 即此形态
        if (self.native_support is NativeIdempotencySupport.UNSUPPORTED
                and self.key_transport is not IdempotencyKeyTransport.NONE):
            raise ValueError(
                f"provider {self.provider_name!r}: UNSUPPORTED 能力禁止声明 "
                "key 传输载体（协议无键）")


# ═══════════════════════════════════════════════════
# Provider Registry（集中、唯一事实源；禁止 adapter 各自声明）
# ═══════════════════════════════════════════════════

PROVIDER_CONTRACTS: dict[str, ProviderIdempotencyCapabilities] = {
    # SMTP（email.send smtp 引擎）：RFC 5321 无幂等键/无 status lookup；
    # DATA 阶段中断 = 结果未知 → IN_DOUBT（E1 收口的契约基础）。
    "smtp": ProviderIdempotencyCapabilities(
        provider_name="smtp",
        native_support=NativeIdempotencySupport.UNSUPPORTED,
        key_transport=IdempotencyKeyTransport.NONE,
        status_lookup_supported=False,
        unknown_result_policy=UnknownResultPolicy.IN_DOUBT,
        evidence="RFC 5321 SMTP 协议：无 idempotency key 载体、无按 key 状态查询",
    ),
    # Agently Mail（email.send agently 引擎，QQ 邮箱 CLI 子进程）：
    # CLI 无幂等键；exit code 仅表达命令成败，无服务器侧状态查询。
    "agently_mail": ProviderIdempotencyCapabilities(
        provider_name="agently_mail",
        native_support=NativeIdempotencySupport.UNSUPPORTED,
        key_transport=IdempotencyKeyTransport.NONE,
        status_lookup_supported=False,
        unknown_result_policy=UnknownResultPolicy.IN_DOUBT,
        evidence="agently-cli 子进程接口：仅 exit code，无幂等键与查询端点",
    ),
    # business-service HTTP（CS_WRITE_SOURCE=java 的 /internal/* POST）：
    # 现契约无幂等键字段（Java 侧未实现）→ UNKNOWN → fail-closed 声明。
    # 契约已冻结（docs/contracts/provider-idempotency-protocol.md），
    # activation_gate=True：Java 侧实现 Idempotency-Key 支持并出证据后，
    # 方可升级 SUPPORTED 并解除 gate。
    "business_service_http": ProviderIdempotencyCapabilities(
        provider_name="business_service_http",
        native_support=NativeIdempotencySupport.UNKNOWN,
        key_transport=IdempotencyKeyTransport.HEADER,
        key_header_name="Idempotency-Key",
        status_lookup_supported=False,
        unknown_result_policy=UnknownResultPolicy.IN_DOUBT,
        evidence="business_client.py 现契约无幂等键字段；Java 侧支持未证实（UNKNOWN）",
        activation_gate=True,
    ),
    # ── STOP L（Travel Booking）：三类 fake profile 与任务书 §十七 三模型
    #    一一对应；能力证据 = fake 适配器行为（测试钉死）。真实供应商落地时
    #    按实测证据登记新条目，UNKNOWN 一律 fail-closed（§五.E）。
    "fake_booking_native": ProviderIdempotencyCapabilities(
        provider_name="fake_booking_native",
        native_support=NativeIdempotencySupport.SUPPORTED,
        key_transport=IdempotencyKeyTransport.BODY,
        key_header_name="idempotency_key",
        status_lookup_supported=True,
        duplicate_response_recognizable=True,
        unknown_result_policy=UnknownResultPolicy.SAFE_RETRY,
        evidence="fake 适配器实现：同 idempotency key 同 payload 返回同一 "
                 "provider 订单（Model A；tests/travel/booking 钉死）",
    ),
    "fake_booking_clientref": ProviderIdempotencyCapabilities(
        provider_name="fake_booking_clientref",
        native_support=NativeIdempotencySupport.UNSUPPORTED,
        key_transport=IdempotencyKeyTransport.NONE,
        status_lookup_supported=True,
        unknown_result_policy=UnknownResultPolicy.RECONCILE_FIRST,
        evidence="fake 适配器实现：merchant_order_id 落库可按 ref 查询，"
                 "无原生幂等键（Model B）",
    ),
    "fake_booking_bare": ProviderIdempotencyCapabilities(
        provider_name="fake_booking_bare",
        native_support=NativeIdempotencySupport.UNSUPPORTED,
        key_transport=IdempotencyKeyTransport.NONE,
        status_lookup_supported=False,
        unknown_result_policy=UnknownResultPolicy.IN_DOUBT,
        evidence="fake 适配器实现：无幂等键、无 lookup（Model C）",
    ),
}


def get_provider_contract(provider_name: str) -> ProviderIdempotencyCapabilities:
    """集中 registry 查询；未登记 → ProviderContractMissing（fail-closed）。"""
    contract = PROVIDER_CONTRACTS.get(provider_name)
    if contract is None:
        logger.warning("event=provider_contract_missing provider=%s", provider_name)
        raise ProviderContractMissing(
            f"provider {provider_name!r} 未登记幂等契约（fail-closed："
            f"请在 backend/shared/provider_idempotency.py PROVIDER_CONTRACTS "
            f"登记能力声明并附证据后再执行外部写）")
    return contract


def ensure_execution_allowed(provider_name: str) -> ProviderIdempotencyCapabilities:
    """外部写前置闸：未登记/UNKNOWN 能力一律拒绝执行（Gate C11/C12）。"""
    contract = get_provider_contract(provider_name)
    if contract.native_support is NativeIdempotencySupport.UNKNOWN:
        logger.warning("event=provider_execution_denied_unknown provider=%s",
                       provider_name)
        raise ProviderContractMissing(
            f"provider {provider_name!r} 幂等能力 UNKNOWN（fail-closed）："
            "必须先取证并登记契约，才允许执行外部写")
    return contract


# ═══════════════════════════════════════════════════
# Stable Identity（logical key → provider key 确定性派生）
# ═══════════════════════════════════════════════════

def derive_provider_key(*, tenant_id: str, actor_id: str, operation: str,
                        client_key: str, provider_name: str,
                        provider_operation: str) -> str:
    """logical side effect → opaque 确定性 provider key。

    保证（Gate C2）：same logical effect × 不同 execution/delivery/attempt
    = same provider key。禁止 execution_id/attempt/timestamp/uuid 进入输入。
    tenant 前置保证租户隔离（§46）；输出为 opaque hash（§10 不泄露内部标识）。
    """
    raw = "|".join((
        "pv1",                       # 派生版本前缀（规则变更时轮换）
        str(tenant_id), str(actor_id), str(operation), str(client_key),
        str(provider_name), str(provider_operation),
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def provider_effect_fingerprint(payload: dict) -> str:
    """canonical payload 指纹（复用 ledger 同源规范化：sorted-keys JSON+SHA256）。

    same key + 不同指纹 = IDEMPOTENCY_CONFLICT → fail closed（Gate C3）。
    时间戳/trace/execution 等非业务字段由调用方不放入 payload 保证排除。
    """
    return canonical_fingerprint(payload)


# ═══════════════════════════════════════════════════
# Outcome → Ledger Action 决策（三模型执行语义）
# ═══════════════════════════════════════════════════

@dataclass(frozen=True)
class OutcomeDecision:
    """provider outcome 的结构化处置（executor 边界消费）。"""

    action: str            # complete | fail_retryable | fail_terminal | in_doubt
    outcome: ProviderEffectOutcome
    policy: UnknownResultPolicy
    detail: str = ""


def decide_outcome_action(outcome: ProviderEffectOutcome,
                          contract: ProviderIdempotencyCapabilities) -> OutcomeDecision:
    """按 provider 能力把调用结果映射为 ledger 处置（§15 决策矩阵）。"""
    logger.info("event=provider_outcome_decided provider=%s outcome=%s "
                "native=%s policy=%s", contract.provider_name, outcome.value,
                contract.native_support.value, contract.unknown_result_policy.value)
    if outcome is ProviderEffectOutcome.SUCCEEDED:
        return OutcomeDecision("complete", outcome, contract.unknown_result_policy)
    if outcome is ProviderEffectOutcome.NOT_SENT:
        # 明确未越过边界：ledger FAILED（可接管重试）
        return OutcomeDecision("fail_retryable", outcome,
                               contract.unknown_result_policy,
                               "明确未越过副作用边界，可安全重试")
    if outcome is ProviderEffectOutcome.REJECTED:
        return OutcomeDecision("fail_terminal", outcome,
                               contract.unknown_result_policy,
                               "provider 明确拒绝且无副作用")
    # outcome == UNKNOWN：按能力分流
    policy = contract.unknown_result_policy
    if (contract.native_support is NativeIdempotencySupport.SUPPORTED
            and policy is UnknownResultPolicy.SAFE_RETRY):
        return OutcomeDecision("fail_retryable", outcome, policy,
                               "native 幂等：同 key 重放安全，允许重试")
    if policy is UnknownResultPolicy.RECONCILE_FIRST:
        return OutcomeDecision("fail_retryable", outcome, policy,
                               "provider 可查询：重试前必须先 reconcile")
    # IN_DOUBT（含一切未证明安全的组合）：保守阻断
    logger.warning(
        "event=side_effect_in_doubt provider=%s outcome=%s policy=%s",
        contract.provider_name, outcome.value, policy.value)
    return OutcomeDecision("in_doubt", outcome, policy,
                           "结果未知且不可证明重放安全：IN_DOUBT 保守阻断")


def raise_for_decision(decision: OutcomeDecision) -> None:
    """fail_retryable/fail_terminal 以普通异常上抛（executor 落 FAILED 可重试）；
    in_doubt 以 SideEffectOutcomeUnknown 上抛（executor 落 UNCERTAIN 保守阻断）。"""
    if decision.action == "in_doubt":
        raise SideEffectOutcomeUnknown(decision.detail)
    if decision.action in ("fail_retryable", "fail_terminal"):
        raise ProviderEffectError(decision.detail)


class ProviderEffectError(RuntimeError):
    """provider 调用失败（NOT_SENT/REJECTED），ledger 落 FAILED；不阻断重试。"""


# ═══════════════════════════════════════════════════
# Reconciliation Primitive（§31：结构化，不做 admin/状态机决策）
# ═══════════════════════════════════════════════════

def reconcile_provider_effect(*, contract: ProviderIdempotencyCapabilities,
                              lookup) -> ReconciliationResult:
    """对支持 status lookup 的 provider 做结果对账。

    lookup: 无参 callable → 返回三值语义（"succeeded"/"failed"/"not_found"）；
            lookup 自身异常 = 查询不可用 → IN_DOUBT（不猜）。
    输出结构化 ReconciliationResult；确认 NOT_FOUND 才允许 SAFE_TO_RETRY。
    """
    if not contract.status_lookup_supported:
        return ReconciliationResult.IN_DOUBT
    try:
        verdict = lookup()
    except Exception:  # noqa: BLE001 — 对账不可用不得猜测结果
        return ReconciliationResult.IN_DOUBT
    if verdict == "succeeded":
        return ReconciliationResult.KNOWN_SUCCESS
    if verdict == "failed":
        return ReconciliationResult.KNOWN_FAILURE
    if verdict == "not_found":
        return ReconciliationResult.NOT_FOUND_SAFE_TO_RETRY
    return ReconciliationResult.IN_DOUBT


__all__ = [
    "IdempotencyKeyTransport",
    "NativeIdempotencySupport",
    "OutcomeDecision",
    "PROVIDER_CONTRACTS",
    "ProviderContractMissing",
    "ProviderEffectError",
    "ProviderEffectOutcome",
    "ProviderIdempotencyCapabilities",
    "ReconciliationResult",
    "UnknownResultPolicy",
    "decide_outcome_action",
    "derive_provider_key",
    "ensure_execution_allowed",
    "get_provider_contract",
    "provider_effect_fingerprint",
    "raise_for_decision",
    "reconcile_provider_effect",
]
