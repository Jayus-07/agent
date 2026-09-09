"""guard.py — Input Guard 编排层（分层检测调度 + 短路话术 + Audit）

决策流（短路优先，安全项先于范围项）：
  L0 格式检查（纯规则）
  L1 注入/有害（句式规则；strong 拍板，weak 进入边界队列）
  L3 LLM Guard（仅边界项，配置开关默认关闭；关闭时保守降级：
     边界注入 → CLARIFY，边界有害 → DEGRADE，绝不静默放行）
  L1 敏感域预判（domain/sensitivity/needs_permission，不做权限判定）
  L1 范围识别（问候/垃圾/超范围/模糊）
  默认放行（未命中任何规则 = 正常业务问题，低置信 ALLOW）

失败策略：Guard 自身异常 → fail-open + ERROR 日志 + 计数
（Guard 不是唯一安全边界，SQL 校验/认证等真实防线在下游；
但 L0/L1 为纯函数几乎不可能异常，此分支仅为最后保险）。
"""
from __future__ import annotations

import hashlib
import threading
import time

from backend.config import guard as cfg
from backend.security.input_guard.format_checker import check_format
from backend.security.input_guard.llm_guard import evaluate_with_llm_guard
from backend.security.input_guard.normalize import normalize_query
from backend.security.input_guard.rule_guard import RuleFinding, RuleGuard
from backend.security.input_guard.types import (
    GuardAction,
    GuardCategory,
    GuardResult,
    RiskLevel,
)
from backend.shared.logger import logger

# ── 面向用户的短路话术（不泄露内部实现细节）─────────────
MSG_BLOCK_EMPTY = "## 提示\n\n请输入有效问题。"
MSG_BLOCK_FORMAT = (
    "## 无法处理该输入\n\n输入过长或包含异常字符，"
    "请精简后重新提问（单次提问建议不超过 2000 字）。"
)
MSG_BLOCK_INJECTION = (
    "## 无法处理该请求\n\n检测到可能影响系统安全的指令性内容，已拒绝处理。"
    "如有业务问题，请直接描述您的数据查询或知识咨询需求。"
)
MSG_BLOCK_HARMFUL = (
    "## 无法处理该请求\n\n该请求涉及高风险或恶意行为，已拒绝处理。"
    "正常的安全知识咨询请改用疑问句式（如\"什么是 XX？\"）。"
)
MSG_CLARIFY_BORDER = (
    "## 需要您澄清一下 🙋\n\n我暂时无法确定这个请求的意图。"
    "请具体描述您想查询的业务数据或咨询的知识点，例如：\n"
    "- 查询今天库存不足的商品\n- 退货政策是什么？"
)
MSG_CLARIFY_GARBAGE = (
    "## 没有看懂您的输入 🙋\n\n请输入完整的业务问题，例如：\n"
    "- 分析本月销售额\n- 查询商品 SKU 信息"
)
MSG_CLARIFY_VAGUE = (
    "## 请补充一些信息 🙋\n\n您的问题比较笼统，请补充具体对象或指标，例如：\n"
    "- 库存怎么样 → 查询今天库存不足的商品\n"
    "- 帮我分析一下 → 分析本月销售额环比变化"
)


class InputGuard:
    """用户问题输入门禁。线程安全（无可变状态，规则对象只读）。"""

    def __init__(
        self,
        *,
        enable_guard: bool | None = None,
        enable_injection: bool | None = None,
        enable_scope: bool | None = None,
        enable_llm: bool | None = None,
        llm_threshold: float | None = None,
        llm_timeout: int | None = None,
        policy_version: str | None = None,
        fmt_overrides: dict | None = None,
    ):
        self.enable_guard = cfg.ENABLE_INPUT_GUARD if enable_guard is None else enable_guard
        self.enable_injection = (
            cfg.ENABLE_INJECTION_GUARD if enable_injection is None else enable_injection
        )
        self.enable_scope = cfg.ENABLE_SCOPE_GUARD if enable_scope is None else enable_scope
        self.enable_llm = cfg.ENABLE_LLM_GUARD if enable_llm is None else enable_llm
        self.llm_threshold = (
            cfg.LLM_GUARD_THRESHOLD if llm_threshold is None else llm_threshold
        )
        self.llm_timeout = cfg.LLM_GUARD_TIMEOUT if llm_timeout is None else llm_timeout
        self.policy_version = (
            cfg.GUARD_POLICY_VERSION if policy_version is None else policy_version
        )
        # 格式阈值覆盖（测试注入用；None 表示读配置）
        self._fmt_overrides = fmt_overrides or {}
        self.rules = RuleGuard()

    # =====================================================
    # 入口
    # =====================================================

    def guard(self, query: str, session_id: str = "") -> GuardResult:
        """对用户问题做准入判定。永不抛异常（内部异常 → fail-open）。"""
        t0 = time.monotonic()
        normalized = normalize_query(query or "")

        if not self.enable_guard:
            result = self._make(
                GuardAction.ALLOW, GuardCategory.BUSINESS_QUERY, RiskLevel.LOW,
                1.0, "Input Guard 已禁用", normalized,
            )
        else:
            try:
                result = self._evaluate(query or "", normalized)
            except Exception as e:
                # fail-open：Guard 内部故障不能变成全站拒绝服务；
                # 显式 ERROR + 计数，便于告警发现（绝不静默）。
                logger.error(f"[InputGuard] 内部异常，fail-open 放行: {e}", exc_info=True)
                self._count("total", action="allow", category="fallback_error")
                result = self._make(
                    GuardAction.ALLOW, GuardCategory.BUSINESS_QUERY, RiskLevel.LOW,
                    0.0, f"Guard 内部异常（已放行）: {type(e).__name__}", normalized,
                    layer="fallback",
                )

        self._record_metrics(result, time.monotonic() - t0)
        self._audit(result, session_id, normalized)
        return result

    # =====================================================
    # 决策主流程
    # =====================================================

    def _evaluate(self, raw: str, normalized: str) -> GuardResult:
        # ── L0 格式检查 ──
        fv = check_format(raw, normalized, **self._fmt_overrides)
        if fv.category is not None:
            risk = RiskLevel.LOW if fv.category == GuardCategory.INVALID else RiskLevel.MEDIUM
            message = MSG_BLOCK_EMPTY if fv.category == GuardCategory.INVALID else MSG_BLOCK_FORMAT
            return self._make(
                GuardAction.BLOCK, fv.category, risk, 0.99,
                f"格式检查: {fv.reason}", normalized, message=message,
            )

        q = normalized.casefold()
        llm_consulted = False

        # ── L1 注入 / 有害（句式规则）──
        borderline: RuleFinding | None = None
        if self.enable_injection:
            inj = self.rules.detect_injection(q)
            if inj is not None:
                if inj.strength == "strong":
                    return self._make(
                        GuardAction.BLOCK, inj.category, inj.risk, inj.confidence,
                        inj.reason, normalized, message=MSG_BLOCK_INJECTION,
                    )
                borderline = inj
            harm = self.rules.detect_harmful(q)
            if harm is not None:
                if harm.strength == "strong":
                    return self._make(
                        GuardAction.BLOCK, harm.category, harm.risk, harm.confidence,
                        harm.reason, normalized, message=MSG_BLOCK_HARMFUL,
                    )
                # 边界有害优先于边界注入（风险更高）
                if borderline is None or harm.risk.value != "low":
                    borderline = harm

        if borderline is not None:
            # LLM Guard 只处理边界项；结果可升级或降级，但解析失败不静默放行
            if self.enable_llm and borderline.confidence < self.llm_threshold:
                llm_consulted = True
                verdict = evaluate_with_llm_guard(normalized, self.llm_timeout)
                if verdict.action is not None:
                    result = self._from_llm(verdict, borderline, normalized)
                    result.llm_consulted = True
                    return result
                logger.info(
                    f"[InputGuard] LLM Guard 未决（{verdict.fallback_reason}），"
                    "按规则层保守降级"
                )
            return self._borderline_default(borderline, normalized, llm_consulted)

        # ── L1 敏感域预判（始终执行：只标注不拦截，供 Permission Guard 使用）──
        sens = self.rules.detect_sensitive(q)
        if sens is not None:
            return self._make(
                GuardAction.DEGRADE, GuardCategory.SENSITIVE_DATA, sens.risk,
                sens.confidence, sens.reason, normalized,
                domain=sens.domain, sensitivity=sens.sensitivity,
                needs_permission=True,
            )

        # ── L1 业务范围识别 ──
        if self.enable_scope:
            greet = self.rules.detect_greeting(q)
            if greet is not None:
                return self._make(
                    GuardAction.ALLOW, GuardCategory.GREETING, RiskLevel.LOW,
                    greet.confidence, greet.reason, normalized,
                )

            garbage = self.rules.detect_garbage(q)
            if garbage is not None:
                return self._make(
                    GuardAction.CLARIFY, GuardCategory.GARBAGE, RiskLevel.LOW,
                    garbage.confidence, garbage.reason, normalized,
                    message=MSG_CLARIFY_GARBAGE,
                )

            oos = self.rules.detect_out_of_scope(q)
            if oos is not None:
                # 超范围不粗暴 BLOCK：放行进入链路，由 RAG Evidence Gate
                # 的 out_of_scope 拒答机制给出礼貌回复（降低误杀）
                return self._make(
                    GuardAction.DEGRADE, GuardCategory.OUT_OF_SCOPE, RiskLevel.LOW,
                    oos.confidence, oos.reason, normalized,
                )

            vague = self.rules.detect_vague(q)
            if vague is not None:
                return self._make(
                    GuardAction.CLARIFY, GuardCategory.AMBIGUOUS, RiskLevel.LOW,
                    vague.confidence, vague.reason, normalized,
                    message=MSG_CLARIFY_VAGUE,
                )

        # ── 业务正向信号 / 默认放行 ──
        if self.rules.has_business_signal(q):
            return self._make(
                GuardAction.ALLOW, GuardCategory.BUSINESS_QUERY, RiskLevel.LOW,
                0.8, "命中业务正向信号", normalized,
            )
        return self._make(
            GuardAction.ALLOW, GuardCategory.BUSINESS_QUERY, RiskLevel.LOW,
            0.5, "无风险信号，默认放行", normalized,
        )

    # =====================================================
    # 辅助
    # =====================================================

    def _borderline_default(
        self, finding: RuleFinding, normalized: str, llm_consulted: bool
    ) -> GuardResult:
        """LLM Guard 关闭/未决时的保守降级（不粗暴 BLOCK，也不静默放行）。"""
        if finding.category == GuardCategory.PROMPT_INJECTION:
            return self._make(
                GuardAction.CLARIFY, finding.category, RiskLevel.MEDIUM,
                finding.confidence, finding.reason, normalized,
                message=MSG_CLARIFY_BORDER, llm_consulted=llm_consulted,
            )
        return self._make(
            GuardAction.DEGRADE, finding.category, RiskLevel.MEDIUM,
            finding.confidence, finding.reason, normalized,
            llm_consulted=llm_consulted,
        )

    def _from_llm(self, verdict, finding: RuleFinding, normalized: str) -> GuardResult:
        """把 LLM Guard 结论映射为 GuardResult（layer=llm）。"""
        action = {
            "allow": GuardAction.ALLOW,
            "clarify": GuardAction.CLARIFY,
            "block": GuardAction.BLOCK,
        }[verdict.action]
        risk = RiskLevel(verdict.risk_level)
        message = ""
        if action == GuardAction.BLOCK:
            message = MSG_BLOCK_INJECTION if finding.category == GuardCategory.PROMPT_INJECTION \
                else MSG_BLOCK_HARMFUL
        elif action == GuardAction.CLARIFY:
            message = MSG_CLARIFY_BORDER
        return self._make(
            action, finding.category, risk, 0.8,
            f"LLM Guard: {verdict.reason or verdict.category}", normalized,
            message=message, layer="llm",
        )

    def _make(
        self,
        action: GuardAction,
        category: GuardCategory,
        risk: RiskLevel,
        confidence: float,
        reason: str,
        normalized: str,
        *,
        message: str = "",
        layer: str = "rule",
        domain: str | None = None,
        sensitivity: RiskLevel | None = None,
        needs_permission: bool = False,
        llm_consulted: bool = False,
    ) -> GuardResult:
        return GuardResult(
            action=action,
            category=category,
            risk_level=risk,
            confidence=round(confidence, 3),
            reason=reason,
            normalized_query=normalized,
            message=message,
            layer=layer,
            domain=domain,
            sensitivity=sensitivity,
            needs_permission=needs_permission,
            policy_version=self.policy_version,
            llm_consulted=llm_consulted,
        )

    # =====================================================
    # 指标 / 审计（软失败：埋点异常不影响决策）
    # =====================================================

    def _count(self, name: str, **labels: str) -> None:
        try:
            from backend.observability.metrics import (
                input_guard_total,
            )
            input_guard_total.labels(**labels).inc()
        except Exception:
            logger.debug("[InputGuard] 指标上报失败", exc_info=True)

    def _record_metrics(self, result: GuardResult, elapsed: float) -> None:
        try:
            from backend.observability.metrics import (
                input_guard_total,
                input_guard_duration_seconds,
            )
            input_guard_total.labels(
                action=result.action.value, category=result.category.value
            ).inc()
            input_guard_duration_seconds.labels(layer=result.layer).observe(elapsed)
        except Exception:
            logger.debug("[InputGuard] 指标上报失败", exc_info=True)

    def _audit(self, result: GuardResult, session_id: str, normalized: str) -> None:
        """结构化审计日志（复用现有结构化 logger，自动携带 trace_id）。

        敏感输入保护：HIGH/CRITICAL 风险不落原文，仅记录长度 + SHA256 摘要
        前缀（可关联取证，又不会把恶意/敏感内容扩散到日志）。
        """
        base = (
            f"[GuardAudit] session={session_id or '-'} "
            f"action={result.action.value} category={result.category.value} "
            f"risk={result.risk_level.value} conf={result.confidence:.2f} "
            f"layer={result.layer} policy={result.policy_version} "
            f"needs_permission={result.needs_permission} "
            f"domain={result.domain or '-'} len={len(normalized)}"
        )
        if result.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
            logger.warning(f"{base} query_sha256={digest}")
        else:
            logger.info(f"{base} query_preview={normalized[:40]!r}")


# ── 模块级单例 ─────────────────────────────────────────
_guard_instance: InputGuard | None = None
_guard_lock = threading.Lock()


def get_input_guard() -> InputGuard:
    """获取 InputGuard 单例（首次调用时初始化）。"""
    global _guard_instance
    if _guard_instance is None:
        with _guard_lock:
            if _guard_instance is None:
                _guard_instance = InputGuard()
    return _guard_instance


def guard_query(query: str, session_id: str = "") -> GuardResult:
    """模块级便捷入口。"""
    return get_input_guard().guard(query, session_id=session_id)
