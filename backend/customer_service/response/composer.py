"""composer.py — CS Response Composer（任务书 §十七~§二十三）。

定位：Expert 结构化结果 + 模板初稿 → 按 Expert 策略生成自然回复：
  knowledge  → 直通（RAG 链已是 LLM 组织 + 证据门禁，不二次烧模型）
  query      → LLM 转写（Tool 事实 → 自然语言，禁止改事实/新增事实）
  complaint  → LLM 安抚（政策红线：不承诺赔偿/退款/时效，OutputGuard 后置兜底）
  action     → 模板直出（确认卡/成功/失败/幂等/冲突是高风险话术，禁 LLM 改写）
  handoff    → 模板直出（固定话术无需 LLM）

降级铁律（任务书 §二十二/§二十三）：LLM timeout/provider error/非法
JSON/guard fail 一律回模板初稿，绝不 500/断流/空回复；一次调用失败立即
fallback，禁止自动多次 retry。
"""
from __future__ import annotations

import re
import time

from backend.customer_service.response.contracts import (
    EXPERT_RESPONSE_POLICY,
    CSRenderedResponse,
    ResponsePolicy,
)
from backend.customer_service.response.facts import build_fact_set, project_fact_set
from backend.shared.logger import logger

# 订单号形态（守卫用，与 validator 同款定义）：含字母的字母数字连字段
# （DEMO-1001/MO-3C052B3A）。纯数字-纯数字（10-10、2026-10-10 日期）不算
# 订单号，避免日期误杀。
from backend.customer_service.understanding.validator import ORDER_ID_LIKE

_ORDER_NO_LIKE = ORDER_ID_LIKE


def _is_order_no(token: str) -> bool:
    return bool(re.search(r"[A-Za-z]", token) and re.search(r"\d", token))


def _tag_trace(**tags: object) -> None:
    try:
        from backend.observability.tracer import trace_collector

        tracer = trace_collector.current()
        if tracer is None:
            return
        for key, value in tags.items():
            if value not in (None, ""):
                tracer.tags[key] = value
    except Exception:  # noqa: BLE001 — 观测旁路
        pass


def _metrics_inc(counter_name: str, labels: dict) -> None:
    try:
        from backend.observability import metrics as m

        getattr(m, counter_name).labels(**labels).inc()
    except Exception:  # noqa: BLE001 — 观测旁路软失败
        pass


def compose_reply(
    expert_name: str,
    draft: str,
    expert_result: dict,
    cs_route: dict,
    state: dict,
) -> tuple[str, str]:
    """生成最终回复。返回 (text, source)；source ∈ template/llm/llm_fallback。

    模板路径零 LLM 零开销；LLM 路径失败软降级回 draft。开关关闭时全部
    走 template（行为与改造前一致）。
    """
    from backend.config.customer_service import CS_RESPONSE_COMPOSER_ENABLED

    policy = EXPERT_RESPONSE_POLICY.get(expert_name, ResponsePolicy.TEMPLATE)

    # knowledge：RAG 上游已是 LLM 生成 + 证据门禁，直通（source=llm）
    if policy is ResponsePolicy.LLM_UPSTREAM:
        _metrics_inc("cs_response_total", {"source": "llm"})
        _tag_trace(cs_response_source="llm")
        return draft, "llm"

    # action / handoff / 未登记 expert / 开关关：模板直出（P0-19）
    if policy is ResponsePolicy.TEMPLATE or not CS_RESPONSE_COMPOSER_ENABLED:
        _metrics_inc("cs_response_total", {"source": "template"})
        _tag_trace(cs_response_source="template")
        return draft, "template"

    if not draft.strip():
        _metrics_inc("cs_response_total", {"source": "template"})
        _tag_trace(cs_response_source="template")
        return draft, "template"

    # query / complaint：LLM 转写（一次调用，失败回模板）
    t0 = time.monotonic()
    outcome = _compose_with_llm(expert_name, draft, expert_result, cs_route, state)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if outcome is None:
        _metrics_inc("cs_response_total", {"source": "llm_fallback"})
        _metrics_inc("cs_response_llm_fallback_total", {"reason": "llm_error"})
        _tag_trace(cs_response_source="llm_fallback",
                   cs_response_fallback_reason="llm_error",
                   cs_response_latency_ms=elapsed_ms)
        return draft, "llm_fallback"

    rendered, prompt_version = outcome

    # P0-18 守卫：输出中的订单号集合 ⊆ 输入基线中的订单号集合
    fact_set = build_fact_set(expert_result)
    if (
        not _guard_no_new_facts(rendered.answer, draft, expert_result)
        or not _guard_fact_consistency(rendered.answer, fact_set)
    ):
        logger.warning(
            "[CS ResponseComposer] 守卫拦截（输出含未核验事实），回模板: "
            "expert=%s", expert_name,
        )
        _metrics_inc("cs_response_total", {"source": "llm_fallback"})
        _metrics_inc(
            "cs_response_llm_fallback_total", {"reason": "fact_guard_fail"},
        )
        _tag_trace(
            cs_response_source="llm_fallback",
            cs_response_guard_result="rejected_unverified_facts",
            cs_response_fallback_reason="fact_guard_fail",
            cs_response_latency_ms=elapsed_ms,
        )
        return draft, "llm_fallback"

    _metrics_inc("cs_response_total", {"source": "llm"})
    _tag_trace(
        cs_response_source="llm",
        cs_response_latency_ms=elapsed_ms,
        cs_response_guard_result="passed",
        cs_response_prompt_version=prompt_version,
    )
    return rendered.answer, "llm"


def _compose_with_llm(
    expert_name: str,
    draft: str,
    expert_result: dict,
    cs_route: dict,
    state: dict,
) -> tuple[CSRenderedResponse, int | None] | None:
    """LLM 转写（一次调用）。任何失败返回 None（调用方回模板）。"""
    import json

    from backend.config.customer_service import CS_RESPONSE_COMPOSER_TIMEOUT_MS
    from backend.customer_service.understanding.llm_runtime import (
        llm_invoke_once,
        mask_for_llm,
    )
    from backend.customer_service.understanding.validator import (
        extract_json_object,
    )

    facts = build_fact_set(expert_result)
    masked_message = mask_for_llm(str(state.get("user_message", "") or ""))

    try:
        from backend.customer_service.prompting import render_prompt_with_version

        prompt, prompt_version = render_prompt_with_version(
            "customer_service.response_composer",
            expert_type=expert_name,
            user_message=masked_message[:300],
            facts=json.dumps(facts, ensure_ascii=False, default=str)[:1500],
            # 注册 Prompt 保留 draft 变量以兼容已发布版本，但模板草稿
            # 不进入模型上下文；事实只来自白名单 FactSet。
            draft="",
        )
    except Exception as exc:  # noqa: BLE001 — prompt 链故障回模板
        logger.warning("[CS ResponseComposer] prompt 渲染失败: %s", exc)
        return None

    content = llm_invoke_once(prompt, CS_RESPONSE_COMPOSER_TIMEOUT_MS)
    if content is None:
        return None

    # 输出协议：{"answer": "..."}；纯文本输出也接受（防过度格式化脆弱）
    data_out = extract_json_object(content)
    answer = ""
    if data_out is not None and isinstance(data_out.get("answer"), str):
        answer = data_out["answer"].strip()
    elif content and not content.startswith("{"):
        answer = content.strip()

    if not answer:
        return None
    return CSRenderedResponse(answer=answer), prompt_version


def _sanitize_facts(data: dict) -> dict:
    """facts 白名单化：剥离内部审计/状态机/身份字段，只留可展示业务事实。"""
    return build_fact_set({"data": data})


def _guard_no_new_facts(answer: str, draft: str, expert_result: dict) -> bool:
    """P0-18：LLM 输出不得引入 draft/事实之外的订单号。

    抽取输出与基线（draft + expert_result.data）两侧订单号集合，输出侧
    出现基线没有的订单号 = 新增业务事实 → 拒绝回模板。
    """
    baseline = _collect_order_nos(draft)
    baseline |= _collect_order_nos(str(expert_result.get("data") or {}))
    return _collect_order_nos(answer) <= baseline


def _guard_fact_consistency(answer: str, facts: dict) -> bool:
    """拒绝 FactSet 未支持的金额、状态、承诺、退款和来源声明。"""
    safe_facts = project_fact_set(facts)
    allowed_amounts = _fact_amounts(safe_facts)
    answer_amounts = _answer_amounts(answer)
    if answer_amounts:
        for amount, currencies, start, end in answer_amounts:
            claim_kind = _amount_claim_kind(answer, start, end)
            if claim_kind is None:
                return False
            order_scope = _amount_order_scope(
                answer, start, end, safe_facts, len(answer_amounts),
            )
            if order_scope is None:
                return False
            amount_candidates = (
                {item for item in allowed_amounts if item[2] in order_scope}
                if order_scope else allowed_amounts
            )
            if not any(
                allowed_amount == amount
                and allowed_kind == claim_kind
                and (
                    currencies is None
                    or (bool(currencies) and allowed_currency in currencies)
                )
                for allowed_amount, allowed_currency, _, allowed_kind
                in amount_candidates
            ):
                return False

    claims = (
        (r"(?:已|已经)发货|(?:已|已经)寄出|寄出了|(?:已|已经)发出|"
         r"(?:已|已经)揽收|(?:已|已经)出库|运输中|正在运输|在途",
         "shipped", "shipping"),
        (r"(?:未|没|尚未)发货|待发货|尚未寄出|"
         r"(?:并不是|并非|并没有|并没|并不|没有|没|不是|不曾)"
         r".{0,4}(?:已|已经)?发货", "not_shipped", "shipping"),
        (r"(?:已|已经)签收|(?:已|已经)送达|已送到", "delivered", "shipping"),
        (r"(?:未|没有|没|尚未)(?:签收|送达|送到)|"
         r"(?:并不是|并非|并没有|并没|并不|不是|不曾)"
         r".{0,4}(?:已|已经)?(?:签收|送达|送到)",
         "not_delivered", "shipping"),
        (r"(?:已|已经)取消", "cancelled", "shipping"),
    )
    for pattern, expected, field in claims:
        for match in re.finditer(pattern, answer):
            claim_expected = expected
            if expected == "shipped" and _is_negated_status_claim(
                answer, match.start(),
            ):
                claim_expected = "not_shipped"
            elif expected == "delivered" and _is_negated_status_claim(
                answer, match.start(),
            ):
                claim_expected = "not_delivered"
            if not _status_claim_supported(
                answer, match.start(), safe_facts, claim_expected, field,
            ):
                return False

    order_claims = (
        (r"(?:已|已经)支付|(?:已|已经)付款", "paid"),
        (r"待支付|等待付款", "pending"),
        (r"(?:已|已经)完成", "completed"),
    )
    for pattern, expected in order_claims:
        for match in re.finditer(pattern, answer):
            if expected == "completed" and re.search(
                r"退款(?:申请)?[^，,。；;！？!?\n]{0,12}$",
                answer[max(0, match.start() - 20):match.start()],
            ):
                continue
            if expected == "completed" and re.match(
                r"退款(?:申请)?", answer[match.end():],
            ):
                continue
            if not _status_claim_supported(
                answer, match.start(), safe_facts, expected, "order",
            ):
                return False

    for eta_clause in re.split(r"[，,。；;！？!?\n]+", answer or ""):
        if not _has_delivery_or_arrival_promise(eta_clause):
            continue
        answer_eta = _eta_tokens_from_text(eta_clause)
        dispatch_claim = bool(re.search(
            r"发货|出库|寄出|揽收|派送|派件", eta_clause,
        ))
        if dispatch_claim and answer_eta:
            # 当前 FactSet 只有预计送达时间，没有预计发货时间。
            return False
        refund_claim = bool(re.search(r"到账", eta_clause))
        logistics_claim = bool(re.search(
            r"送达|送到|送货|签收|收货|到货|到达|配送|投递|妥投",
            eta_clause,
        ))
        if refund_claim == logistics_claim:
            return False
        allowed_eta = (
            _refund_eta_tokens(safe_facts)
            if refund_claim else _eta_tokens(safe_facts)
        )
        if not allowed_eta or not answer_eta or not all(
            _eta_token_matches(token, allowed_eta) for token in answer_eta
        ):
            return False

    operation_statuses = _refund_operation_statuses(safe_facts)
    operation = safe_facts.get("operation")
    simulated_operation = (
        isinstance(operation, dict) and operation.get("simulated") is True
    )
    execution_claims = re.finditer(
        r"(?:已|已经)(?:成功)?(?:(?:为您|帮您)?(?:申请|提交|发起)退款)|"
        r"退款申请(?:已|已经)?(?:提交|受理|完成)|退款(?:已|已经)到账|"
        r"申请退款(?:已|已经)?(?:提交|受理|完成)|"
        r"退款(?:已|已经)(?:完成|成功|到账)|"
        r"(?:已|已经)完成退款|退款成功|已退款",
        answer,
    )
    for execution_claim in execution_claims:
        if operation_statuses & {"failed", "cancelled"}:
            return False
        if not operation_statuses & {"executed", "success"}:
            return False
        if (simulated_operation or _has_sandbox_source(safe_facts)) and not (
            _has_positive_simulation_label(answer, execution_claim.start())
        ):
            return False
    pending_proposal_claim = re.search(
        r"(?:已|已经)(?:为您)?(?:生成|创建)(?:了)?"
        r"(?:待确认的?)?退款申请|(?:待您|请您)确认.{0,8}退款申请|"
        r"(?:待确认|待您(?:确认|批准)|等待.{0,4}(?:确认|批准)|"
        r"等(?:待)?您.{0,4}(?:确认|批准)|需要您.{0,4}(?:确认|批准))"
        r".{0,8}退款申请|"
        r"退款申请.{0,24}(?:待确认|待您(?:确认|批准)|等待.{0,4}(?:确认|批准)|"
        r"等(?:待)?您.{0,4}(?:确认|批准)|需要您.{0,4}(?:确认|批准)|"
        r"批准后(?:再)?提交)",
        answer,
    )
    if pending_proposal_claim:
        if "pending_confirmation" not in operation_statuses:
            return False
    eligibility = _refund_eligibility(safe_facts)
    for match in re.finditer(
        r"符合退款(?:资格|条件)|可以申请退款|可办理退款|具备退款资格|"
        r"(?:可以|可)(?:申请)?退款|支持退款",
        answer,
    ):
        claimed_eligible = not _is_negated_refund_claim(answer, match.start())
        if eligibility is not claimed_eligible:
            return False

    real_source_claim = re.compile(
        r"(?:(?:真实|实际|生产|线上)(?:的)?"
        r"[\u4e00-\u9fff、，, ]{0,16}"
        r"(?:数据|数据源|数据库|平台|商城|订单|物流|交易|系统|环境|接口)|"
        r"线上平台已|实际已(?:退款|完成|到账))",
    )
    real_source_claims = list(real_source_claim.finditer(answer))
    if real_source_claims:
        if _has_sandbox_source(safe_facts) or not _has_business_source(safe_facts):
            if any(
                not re.search(
                    r"(?:并不是|并非|不是|不|非)(?:一个|一种|所谓)?\s*$",
                    answer[max(0, match.start() - 8):match.start()],
                )
                for match in real_source_claims
            ):
                return False
    return True


def _fact_amounts(
    facts: dict,
) -> set[tuple[str, str | None, str | None, str]]:
    from decimal import Decimal, InvalidOperation

    values: set[tuple[str, str | None, str | None, str]] = set()

    def add_amounts(
        section: object,
        kind: str,
        inherited_order_no: str | None = None,
    ) -> None:
        if not isinstance(section, dict):
            return
        currency_value = section.get("currency")
        currency = (
            str(currency_value).upper()
            if isinstance(currency_value, str) and currency_value.upper() != "UNKNOWN"
            else None
        )
        order_no = section.get("order_no") or inherited_order_no
        order_no = str(order_no) if order_no else None
        for key in ("amount", "total_amount"):
            item = section.get(key)
            if item is None or isinstance(item, bool):
                continue
            try:
                number = Decimal(str(item).replace(",", ""))
                if number.is_finite() and number >= 0:
                    values.add((
                        format(number.normalize(), "f"), currency, order_no, kind,
                    ))
            except (InvalidOperation, ValueError):
                continue

    for key in ("order", "orders"):
        section = facts.get(key)
        for item in section if isinstance(section, list) else [section]:
            add_amounts(item, "order")
    add_amounts(facts.get("refund"), "refund")
    task_result = facts.get("task_result")
    if isinstance(task_result, dict):
        add_amounts(task_result.get("facts"), "order")
    return values


def _fact_order_numbers(facts: dict) -> list[str]:
    orders = facts.get("orders")
    if isinstance(orders, list):
        return [
            str(item.get("order_no") or "") if isinstance(item, dict) else ""
            for item in orders
        ]
    order = facts.get("order")
    if isinstance(order, dict) and order.get("order_no"):
        return [str(order["order_no"])]
    task_result = facts.get("task_result")
    if isinstance(task_result, dict):
        task_facts = task_result.get("facts")
        if isinstance(task_facts, dict) and task_facts.get("order_no"):
            return [str(task_facts["order_no"])]
    return []


def _amount_claim_kind(answer: str, start: int, end: int) -> str | None:
    """按金额前最近的字段标签分类，忽略金额后的旁注。"""
    refund_label = re.compile(
        r"退款(?:的)?(?:金额|额)?|退还(?:款项|金额|款)?|"
        r"退货款(?:项|金额)?",
    )
    order_label = re.compile(
        r"订单(?:的)?(?:总金额|总额|金额)?|商品(?:总额|金额)?|"
        r"下单金额|合计金额|总金额",
    )

    def labels(text: str, offset: int = 0) -> list[tuple[int, int, str]]:
        found: list[tuple[int, int, str]] = []
        for pattern, kind in ((refund_label, "refund"), (order_label, "order")):
            found.extend(
                (offset + match.start(), offset + match.end(), kind)
                for match in pattern.finditer(text)
            )
        return found

    prefix_start = max(0, start - 48)
    prefix = answer[prefix_start:start]
    prior_labels = labels(prefix, prefix_start)
    if prior_labels:
        return max(prior_labels, key=lambda item: (item[1], item[1] - item[0]))[2]

    suffix = answer[end:min(len(answer), end + 32)]
    following_labels = labels(suffix, end)
    if following_labels:
        return min(following_labels, key=lambda item: (item[0], -(item[1] - item[0])))[2]
    return None


def _mentioned_order_ordinals(answer: str) -> set[int]:
    numeral_values = {
        "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    result: set[int] = set()
    for match in re.finditer(
        r"第\s*(\d{1,2}|[一二两三四五六七八九十]+)"
        r"\s*(?:个|笔|张)?\s*(?:订单|单)",
        answer or "",
    ):
        token = match.group(1)
        if token.isdigit():
            ordinal = int(token)
        elif "十" in token:
            before, _, after = token.partition("十")
            ordinal = (numeral_values.get(before, 1) * 10 if before else 10)
            ordinal += numeral_values.get(after, 0)
        else:
            ordinal = numeral_values.get(token, 0)
        if ordinal > 0:
            result.add(ordinal - 1)
    return result


def _answer_amounts(
    answer: str,
) -> list[tuple[str, frozenset[str] | None, int, int]]:
    from decimal import Decimal, InvalidOperation

    number = r"(?<![\d.,])((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?![\d.,])"
    currency = (
        r"(人民币|澳元|港币|加元|英镑|日元|块钱|块|元|RMB|CNY|USD|EUR|JPY|HKD|"
        r"HK\$|美元|美金|[\u4e00-\u9fff]{1,4}(?:元|币)|"
        r"\$|€|¥|￥|£|₹|₩|₽|[A-Za-z]{2,5}\$?)"
    )
    prefix = re.compile(
        currency + r"\s*" + number,
        re.IGNORECASE,
    )
    suffix = re.compile(number + r"\s*" + currency, re.IGNORECASE)
    contextual = re.compile(
        r"(?:金额|退款额|订单金额)[^0-9]{0,4}" + number,
        re.IGNORECASE,
    )
    currency_candidates = {
        "人民币": frozenset({"CNY"}), "RMB": frozenset({"CNY"}),
        "CNY": frozenset({"CNY"}), "元": frozenset({"CNY"}),
        "块": frozenset({"CNY"}), "块钱": frozenset({"CNY"}),
        "USD": frozenset({"USD"}), "美元": frozenset({"USD"}),
        "美金": frozenset({"USD"}), "$": frozenset({"USD"}),
        "EUR": frozenset({"EUR"}), "€": frozenset({"EUR"}),
        "JPY": frozenset({"JPY"}), "HKD": frozenset({"HKD"}),
        "HK$": frozenset({"HKD"}),
        "日元": frozenset({"JPY"}), "港币": frozenset({"HKD"}),
        "澳元": frozenset({"AUD"}), "加元": frozenset({"CAD"}),
        "英镑": frozenset({"GBP"}), "£": frozenset({"GBP"}),
        "₹": frozenset({"INR"}), "₩": frozenset({"KRW"}),
        "₽": frozenset({"RUB"}),
        "¥": frozenset({"CNY", "JPY"}), "￥": frozenset({"CNY", "JPY"}),
    }
    values: dict[tuple[int, int, str], frozenset[str] | None] = {}
    currency_token = r"(?:" + currency[1:-1] + r")"

    def append_matches(
        pattern: re.Pattern, number_group: int, currency_group: int | None,
    ) -> None:
        for match in pattern.finditer(answer or ""):
            raw = match.group(number_group)
            token = match.group(currency_group) if currency_group is not None else None
            amount_start, amount_end = match.span(number_group)
            before_amount = (answer or "")[max(0, amount_start - 12):amount_start]
            is_negative = bool(re.search(
                r"[-−－﹣]\s*(?:(?:人民币|RMB|CNY|USD|EUR|JPY|HKD|HK\$|"
                r"美元|美金|[¥￥$€£₹₩₽]|[A-Za-z]{2,5}\$?)\s*)?$",
                before_amount,
                re.IGNORECASE,
            ))
            if currency_group is None:
                before = (answer or "")[max(0, amount_start - 12):amount_start]
                after = (answer or "")[amount_end:amount_end + 12]
                if re.search(currency_token + r"\s*$", before, re.IGNORECASE):
                    continue
                if re.match(r"\s*" + currency_token, after, re.IGNORECASE):
                    continue
            try:
                amount = Decimal(raw.replace(",", ""))
                if amount.is_finite() and amount >= 0:
                    token_key = token.upper() if token else ""
                    currencies = (
                        currency_candidates.get(token_key, frozenset())
                        if token else None
                    )
                    if is_negative:
                        currencies = frozenset()
                    key = (amount_start, amount_end, format(amount.normalize(), "f"))
                    prior = values.get(key)
                    if prior is None:
                        values[key] = currencies
                    elif currencies is not None:
                        values[key] = currencies if prior is None else prior & currencies
            except (InvalidOperation, ValueError):
                continue

    append_matches(prefix, 2, 1)
    append_matches(suffix, 1, 2)
    append_matches(contextual, 1, None)
    return [
        (amount, currencies, start, end)
        for (start, end, amount), currencies in sorted(values.items())
    ]


def _amount_order_scope(
    answer: str,
    start: int,
    end: int,
    facts: dict,
    amount_count: int,
) -> set[str] | None:
    """把金额限制到同一分句明确提及的订单；歧义时拒绝生成。"""
    left_boundary = max(
        (answer.rfind(mark, 0, start) for mark in "，。；;！？!?\n"),
        default=-1,
    )
    right_candidates = [
        answer.find(mark, end) for mark in "，。；;！？!?\n"
        if answer.find(mark, end) >= 0
    ]
    right_boundary = min(right_candidates, default=len(answer))
    clause = answer[left_boundary + 1:right_boundary]
    order_mentions = _order_references_for_clause(
        answer, left_boundary, clause, facts,
    )
    if order_mentions is None:
        return None
    if len(order_mentions) > 1:
        return None
    if order_mentions:
        known = {
            order_no for _, _, order_no, _ in _fact_amounts(facts) if order_no
        }
        return order_mentions if order_mentions <= known else None

    global_order_mentions = _order_references_in_text(answer, facts)
    if global_order_mentions is None:
        return None
    if global_order_mentions:
        if len(global_order_mentions) == 1:
            return global_order_mentions
        if amount_count == 1:
            return None
        return None
    return set()


def _order_references_in_text(text: str, facts: dict) -> set[str] | None:
    """将文本中的订单号/序数映射到 FactSet，冲突或越界时返回 None。"""
    references = _collect_order_nos(text)
    ordinals = _mentioned_order_ordinals(text)
    if not ordinals:
        return references
    ordered_numbers = _fact_order_numbers(facts)
    if any(index >= len(ordered_numbers) or not ordered_numbers[index]
           for index in ordinals):
        return None
    ordinal_numbers = {ordered_numbers[index] for index in ordinals}
    if references:
        references &= ordinal_numbers
        return references or None
    return ordinal_numbers


def _order_references_for_clause(
    answer: str,
    left_boundary: int,
    clause: str,
    facts: dict,
) -> set[str] | None:
    references = _order_references_in_text(clause, facts)
    if references is None:
        return None
    if not re.search(
        r"(?:另(?:外)?一笔|另(?:外)?一个|另(?:外)?一单|"
        r"(?:另(?:外)?|剩下|余下|其余)(?:的)?那笔|"
        r"(?:另(?:外)?|剩下|余下|其余)(?:的)?那单)(?:订单|单)?",
        clause,
    ):
        return references

    prior = _order_references_in_text(answer[:left_boundary + 1], facts)
    known = _known_fact_order_numbers(facts)
    if prior is None or len(prior) != 1:
        return None
    remaining = known - prior
    if len(remaining) != 1:
        return None
    if references and references != remaining:
        return None
    return remaining


def _known_fact_order_numbers(facts: dict) -> set[str]:
    result = {item for item in _fact_order_numbers(facts) if item}
    for key in ("order", "orders", "logistics"):
        section = facts.get(key)
        sections = section if isinstance(section, list) else [section]
        result.update(
            str(item["order_no"]) for item in sections
            if isinstance(item, dict) and item.get("order_no")
        )
    for _, _, order_no, _ in _fact_amounts(facts):
        if order_no:
            result.add(order_no)
    task_result = facts.get("task_result")
    if isinstance(task_result, dict):
        task_facts = task_result.get("facts")
        if isinstance(task_facts, dict) and task_facts.get("order_no"):
            result.add(str(task_facts["order_no"]))
    return result


def _shipping_statuses(facts: dict) -> set[str]:
    result: set[str] = set()
    for section in (facts.get("order"), facts.get("logistics")):
        if isinstance(section, dict):
            for key in ("status", "shipping_status"):
                value = section.get(key)
                if isinstance(value, str):
                    result.add(value)
                    if key == "status" and value in {"pending", "paid"}:
                        result.add("not_shipped")
    task_result = facts.get("task_result")
    if isinstance(task_result, dict):
        task_facts = task_result.get("facts")
        if isinstance(task_facts, dict):
            for key in ("shipping_status", "order_status"):
                value = task_facts.get(key)
                if isinstance(value, str):
                    result.add(value)
    return result


def _status_claim_supported(
    answer: str,
    claim_start: int,
    facts: dict,
    expected: str,
    field: str,
) -> bool:
    """按同一分句中的订单引用核验状态，避免把一单状态套到另一单。"""
    left_boundary = max(
        (answer.rfind(mark, 0, claim_start) for mark in "，。；;！？!?\n"),
        default=-1,
    )
    right_candidates = [
        answer.find(mark, claim_start) for mark in "，。；;！？!?\n"
        if answer.find(mark, claim_start) >= 0
    ]
    clause = answer[left_boundary + 1:min(right_candidates, default=len(answer))]
    references = _order_references_for_clause(
        answer, left_boundary, clause, facts,
    )
    if references is None:
        return False

    records = _order_status_records(facts)

    def matches(record: dict[str, str]) -> bool:
        if record.get(f"{field}_conflict") == "true":
            return False
        value = record.get(field)
        if expected == "not_delivered":
            return value in {"not_shipped", "shipped", "cancelled"}
        return value == expected

    if references:
        matching = [record for record in records
                    if record.get("order_no") in references]
        return bool(matching) and all(matches(record) for record in matching)

    if records:
        # 无明确订单时，多单数据必须完整且一致，才允许概括为全局状态。
        return bool(records) and all(matches(record) for record in records)

    # 兼容只有规范化任务事实、没有订单实体的单一查询结果。
    if field == "shipping":
        statuses = _shipping_statuses(facts)
        if expected == "not_delivered":
            return bool(statuses) and "delivered" not in statuses and "unknown" not in statuses
        return expected in statuses
    return expected in _order_statuses(facts)


def _is_negated_status_claim(answer: str, claim_start: int) -> bool:
    prefix = answer[max(0, claim_start - 20):claim_start]
    return bool(re.search(
        r"(?:并不是|并非|并没有|并没|并不|未|没有|没|尚未|不是|不曾)"
        r"\s*(?:(?:已|已经)\s*)?$",
        prefix,
    ))


def _order_status_records(facts: dict) -> list[dict[str, str]]:
    """合并订单、物流与任务事实中的订单状态字段。"""
    records: dict[str, dict[str, str]] = {}
    anonymous_index = 0
    known_order_numbers = set(_fact_order_numbers(facts))
    for section_key in ("order", "orders", "logistics"):
        section = facts.get(section_key)
        sections = section if isinstance(section, list) else [section]
        for item in sections:
            if isinstance(item, dict) and item.get("order_no"):
                known_order_numbers.add(str(item["order_no"]))
    task_result = facts.get("task_result")
    if isinstance(task_result, dict):
        task_facts = task_result.get("facts")
        if isinstance(task_facts, dict) and task_facts.get("order_no"):
            known_order_numbers.add(str(task_facts["order_no"]))
    sole_order_no = (
        next(iter(known_order_numbers)) if len(known_order_numbers) == 1 else None
    )

    def merge(section: object, *, order_status_key: str,
              shipping_status_key: str) -> None:
        nonlocal anonymous_index
        if not isinstance(section, dict):
            return
        order_no = section.get("order_no")
        if isinstance(order_no, str) and order_no:
            record_key = order_no
        elif sole_order_no:
            record_key = sole_order_no
            order_no = sole_order_no
        else:
            anonymous_index += 1
            record_key = f"__anonymous_{anonymous_index}"
        record = records.setdefault(record_key, {})
        if order_no:
            record["order_no"] = str(order_no)

        def set_status(
            field: str, value: str, *, explicit: bool = True,
        ) -> None:
            existing = record.get(field)
            existing_explicit = record.get(f"{field}_explicit") == "true"
            if (
                existing is not None
                and existing != value
                and explicit
                and existing_explicit
            ):
                record[f"{field}_conflict"] = "true"
            record[field] = value
            if explicit:
                record[f"{field}_explicit"] = "true"

        order_status = section.get(order_status_key)
        shipping_status = section.get(shipping_status_key)
        if isinstance(order_status, str):
            set_status("order", order_status)
            if order_status in {"not_shipped", "shipped", "delivered", "cancelled"}:
                set_status("shipping", order_status)
            elif order_status in {"pending", "paid"}:
                set_status("shipping", "not_shipped", explicit=False)
        if isinstance(shipping_status, str):
            set_status("shipping", shipping_status)

    for key in ("order", "orders"):
        section = facts.get(key)
        for item in section if isinstance(section, list) else [section]:
            merge(item, order_status_key="status",
                  shipping_status_key="shipping_status")

    merge(facts.get("logistics"), order_status_key="status",
          shipping_status_key="shipping_status")
    task_result = facts.get("task_result")
    if isinstance(task_result, dict):
        merge(task_result.get("facts"), order_status_key="order_status",
              shipping_status_key="shipping_status")
    return list(records.values())


def _order_statuses(facts: dict) -> set[str]:
    result: set[str] = set()
    for section in (facts.get("order"), facts.get("orders")):
        items = section if isinstance(section, list) else [section]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("status"), str):
                result.add(item["status"])
    task_result = facts.get("task_result")
    if isinstance(task_result, dict):
        task_facts = task_result.get("facts")
        if isinstance(task_facts, dict):
            value = task_facts.get("order_status")
            if isinstance(value, str):
                result.add(value)
    return result


def _eta_tokens(facts: dict) -> set[str]:
    values: list[str] = []
    logistics = facts.get("logistics")
    if isinstance(logistics, dict) and logistics.get("estimated_delivery"):
        values.append(str(logistics["estimated_delivery"]))
    return _eta_tokens_from_text(" ".join(values))


def _refund_eta_tokens(facts: dict) -> set[str]:
    refund = facts.get("refund")
    if not isinstance(refund, dict) or not refund.get("estimated_arrival"):
        return set()
    return _eta_tokens_from_text(str(refund["estimated_arrival"]))


def _has_positive_simulation_label(answer: str, claim_start: int) -> bool:
    """只接受执行声明附近的肯定模拟/沙盒标记。"""
    left_boundary = max(
        (answer.rfind(mark, 0, claim_start) for mark in "，,。；;！？!?\n"),
        default=-1,
    )
    right_candidates = [
        answer.find(mark, claim_start) for mark in "，,。；;！？!?\n"
        if answer.find(mark, claim_start) >= 0
    ]
    clause_start = left_boundary + 1
    clause = answer[clause_start:min(right_candidates, default=len(answer))]
    local_claim_start = claim_start - clause_start
    candidate = None
    for match in re.finditer(r"模拟|沙盒", clause):
        if (
            match.start() < local_claim_start
            and local_claim_start - match.end() <= 32
        ):
            candidate = match
    if candidate is None:
        return False

    prefix = clause[max(0, candidate.start() - 20):candidate.start()]
    negated_simulation = re.search(
        r"(?:并不是|并非|不是|不能算(?:作)?|不应算(?:作)?|算不上|"
        r"不能认为|不能视为|不算(?:作)?|不属于|并未|未|不|非)"
        r"(?:一个|一种|一笔|一次|一项|所谓|这类|这种|真实(?:的)?|"
        r"真正(?:的)?){0,2}\s*$",
        prefix,
    ) or re.search(
        r"(?:我)?(?:不觉得|不认为|没觉得|并不觉得|并不认为)"
        r"(?:这|该|它)?(?:就是|是)?\s*$",
        prefix,
    )
    suffix = clause[candidate.end():candidate.end() + 12]
    negated_suffix = re.match(
        r"\s*(?:结果|退款|操作)?(?:并非|不是|不算|不属于)", suffix,
    )
    return not bool(negated_simulation or negated_suffix)


def _eta_tokens_from_text(text: str) -> set[str]:
    tokens: set[str] = set()
    source = text or ""
    full_date = re.compile(
        r"(?P<year>20\d{2})[-/.年](?P<month>\d{1,2})[-/.月]"
        r"(?P<day>\d{1,2})日?",
    )
    covered_spans: list[tuple[int, int]] = []
    for match in full_date.finditer(source):
        covered_spans.append(match.span())
        tokens.add(
            f"{int(match.group('year')):04d}-"
            f"{int(match.group('month')):02d}-"
            f"{int(match.group('day')):02d}"
        )

    month_day = re.compile(
        r"(?<![\d/-])(?P<month>\d{1,2})\s*月\s*"
        r"(?P<day>\d{1,2})\s*日?",
    )
    for match in month_day.finditer(source):
        if any(match.start() < end and match.end() > start
               for start, end in covered_spans):
            continue
        tokens.add(f"--{int(match.group('month')):02d}-"
                   f"{int(match.group('day')):02d}")

    for pattern in (
        r"((?:\d+(?:[-至]\d+)?|[一二三四五六七八九十半两]+)"
        r"\s*(?:分钟|小时|天|工作日))",
        r"(今天|明天|后天|次日|当天|马上|立刻|稍后|近期|尽快|"
        r"(?:本周|这周|下周|下星期|下下周)(?:[一二三四五六日天]|末)|"
        r"(?:周|星期)(?:[一二三四五六日天]|末))",
    ):
        for match in re.finditer(pattern, source):
            tokens.add(re.sub(r"\s+", "", match.group(1)))
    return tokens


def _eta_token_matches(claim: str, allowed: set[str]) -> bool:
    if claim.startswith("--"):
        return any(token.endswith(claim[1:]) for token in allowed)
    return claim in allowed


def _has_delivery_or_arrival_promise(answer: str) -> bool:
    return bool(re.search(
        r"(?:"
        r"(?:预计|保证|一定|肯定|会在|将在|会于|将于|承诺|"
        r"尽快|马上|立刻|稍后|近期).{0,24}"
        r"(?:送达|送到|送货|签收|收货|到货|到达|配送|投递|妥投|"
        r"派送|派件|到账|发货|揽收|出库|寄出)"
        r"|"
        r"(?:\d+(?:[-至]\d+)?\s*(?:分钟|小时|天|工作日)|"
        r"[一二三四五六七八九十半两]+\s*(?:分钟|小时|天|工作日)|"
        r"今天|明天|后天|次日|当天|马上|立刻|稍后|近期|尽快|"
        r"(?:本周|这周|下周|下星期|下下周)(?:[一二三四五六日天]|末)|"
        r"(?:周|星期)(?:[一二三四五六日天]|末)|"
        r"\d{1,2}\s*月\s*\d{1,2}\s*日?).{0,8}"
        r"(?:送达|送到|送货|签收|收货|到货|到达|配送|投递|妥投|"
        r"派送|派件|到账|发货|揽收|出库|寄出)"
        r")",
        answer or "",
    ))


def _refund_operation_statuses(facts: dict) -> set[str]:
    result: set[str] = set()
    refund = facts.get("refund")
    if isinstance(refund, dict) and isinstance(refund.get("status"), str):
        result.add(refund["status"])
    operation = facts.get("operation")
    if (
        isinstance(operation, dict)
        and operation.get("action") == "refund"
        and isinstance(operation.get("status"), str)
    ):
        result.add(operation["status"])
    return result


def _refund_eligibility(facts: dict) -> bool | None:
    for key in ("refund", "order"):
        section = facts.get(key)
        if isinstance(section, dict) and isinstance(section.get("eligible"), bool):
            return section["eligible"]
        if isinstance(section, dict) and isinstance(
            section.get("refund_eligible"), bool,
        ):
            return section["refund_eligible"]
    return None


def _has_sandbox_source(facts: dict) -> bool:
    values: list[object] = [facts.get("source")]
    for key in ("task_result", "operation"):
        section = facts.get(key)
        if isinstance(section, dict):
            values.append(section.get("source"))
    return any(isinstance(value, str) and value.startswith("sandbox_")
               for value in values)


def _has_business_source(facts: dict) -> bool:
    values: list[object] = [facts.get("source")]
    for key in ("task_result", "operation"):
        section = facts.get(key)
        if isinstance(section, dict):
            values.append(section.get("source"))
    return any(isinstance(value, str) and value.startswith("business_")
               for value in values)


def _is_negated_refund_claim(answer: str, claim_start: int) -> bool:
    raw_prefix = answer[:claim_start]
    boundary = max(
        (raw_prefix.rfind(mark) for mark in "，,。；;！？!?\n"),
        default=-1,
    )
    prefix = raw_prefix[boundary + 1:][-12:]
    if re.search(
        r"(?:并非|并不是|不是|不算|并不算|不能算(?:作)?|不认为|不觉得)"
        r"(?:不|未|没)$",
        prefix,
    ):
        return False
    return bool(re.search(
        r"(?:并不是|并非|不是|并不|不能(?:够)?|不可|无法|不应|尚未|"
        r"没有|没|未|不太|不)\s*$",
        prefix,
    ))


def _collect_order_nos(text: str) -> set[str]:
    return {
        m for m in _ORDER_NO_LIKE.findall(text or "") if _is_order_no(m)
    }
