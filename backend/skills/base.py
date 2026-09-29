"""
skills/base.py — BaseSkill 抽象类

每个 Skill 表示一种或多种业务 Capability。
Skill 通过 Tool 完成具体工作，不感知 Planner/调度逻辑。

子类只需声明:
  - capabilities: list[str]  — 注册的 Capability（如 ["sql.query"]）
  - _tool_fn                 — 关联的 LangChain Tool
"""

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from backend.shared.logger import logger
from backend.shared.error_protocol import error_envelope_from_exception
from backend.skills.validation import (
    ValidationFailure,
    validate_invocation,
    validate_output,
    validate_semantics,
)
from backend.core.tool_runtime.models import ToolStatus  # noqa: F401（_STATUS_TO_ERROR_TYPE 用）

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 1.5  # 旧执行循环退避（TOOL_RUNTIME_ENABLED=false 回滚时仍生效）

# ── 错误分类：类型 → 判定子串（lower 匹配，先命中先定类型）。
# 决定两件事：可否重试（UNRETRYABLE_ERROR_TYPES 之外均可重试）、
# sr["error_type"] 的留痕取值。取代旧版纯布尔匹配的 UNRETRYABLE_PATTERNS。
_ERROR_TYPE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("timeout", ("超时", "timed out", "timeout")),
    ("permission", ("权限不足", "permission denied", "unauthorized", "forbidden")),
    # invalid_param 必须先于 not_found："column not found" 含子串 "not found"，
    # 但它是 Schema/参数问题而非表不存在
    ("invalid_param", ("column not found", "invalid parameter",
                       "参数校验失败", "缺少必填参数")),
    ("syntax", ("syntax error",)),
    ("not_found", ("no such table", "table does not exist", "not found")),
]

# 命中即不可重试的错误类型；unknown/timeout 之外的其他类型可重试
UNRETRYABLE_ERROR_TYPES = ("permission", "not_found", "syntax", "invalid_param")


def classify_error(error: Any) -> str:
    """错误 → 结构化类型（timeout/permission/not_found/syntax/invalid_param/unknown）。

    Tool 层返回的错误大多是 str（异常 str、带标记的降级消息），在 Skill
    边界统一分类一次，重试决策与 trace 留痕共用同一份语义，避免各 Skill
    自行维护一套字符串匹配（SQLSkill 的 status 判定除外——那是结构化的）。
    """
    s = str(error).lower()
    for etype, patterns in _ERROR_TYPE_PATTERNS:
        if any(p in s for p in patterns):
            return etype
    return "unknown"


def _is_retryable(error: str) -> bool:
    return classify_error(error) not in UNRETRYABLE_ERROR_TYPES


# params_schema 声明的 type → 运行时类型检查。
# "integer" 是 JSON Schema 风格别名（web_search 已在用，此前会被静默跳过）
PARAM_TYPE_CHECKS = {
    "string": str,
    "int": int,
    "integer": int,
    "object": dict,
    "boolean": bool,
    "number": (int, float),
}


def validate_params(params_schema: dict, params: dict) -> str | None:
    """按 params_schema 运行时校验入参，返回错误消息（None=通过）。

    模块级纯函数：BaseSkill._validate_params 与 tool_selector（function
    calling 填参校验）共用同一份校验语义，避免两处漂移。规则：
      - 只校验显式声明的参数，未声明的键不拦（交由 Tool 签名兜底）
      - 旧式字符串声明视为 string 可选，跳过
      - auto=True 的参数由运行时自动注入（如 business.analyze 的
        sql_result 走 previous_outputs），不参与校验
    """
    errors = []
    for name, spec in params_schema.items():
        if isinstance(spec, str):
            continue
        if isinstance(spec, dict) and spec.get("auto"):
            continue
        val = params.get(name)
        if spec.get("required") and val in (None, ""):
            errors.append(
                f"缺少必填参数 {name}: {spec.get('description', '')}")
            continue
        if val is None:
            continue
        ptype = spec.get("type", "string")
        expected = PARAM_TYPE_CHECKS.get(ptype)
        type_ok = expected is not None and isinstance(val, expected)
        # bool 是 int 的子类：声明 int/integer 时布尔值应判为类型错误
        if ptype in ("int", "integer") and isinstance(val, bool):
            type_ok = False
        if expected is not None and not type_ok:
            errors.append(
                f"参数 {name} 类型应为 {ptype}，实际为 {type(val).__name__}")
            continue
        enum = spec.get("enum")
        if enum and val not in enum:
            errors.append(
                f"参数 {name} 取值 {val!r} 不在允许范围 {list(enum)}")
    return "; ".join(errors) or None


class BaseSkill(ABC):
    """Skill 抽象基类。每个 Skill 封装一组 Capability。

    子类只声明 ``name`` 与执行行为；Capability 的 description、参数、示例
    由 ``capabilities.yaml`` 在 Skill Registry 启动期绑定。保留下列属性是
    为旧调用方兼容，禁止在 Skill 子类作者维护：
      - capabilities / description / params_schema / examples
      - _tool_fn: property → LangChain Tool
      - default_timeout / default_max_retries — 类级执行参数（可选覆盖）
      - output_type / output_types — 输出契约（可选，见下）

    Capability 是 Planner 与 Skill 之间唯一的契约。

    输出契约: execute() 在 Tool 返回边界按声明归一化，保证下游
    （final_answer、done 事件 sources、记忆落库）拿到的类型稳定。
      - output_type:   ClassVar[str]，默认 "text"（str）；
                       "structured" 表示 dict（如 SQLResult.model_dump()）
      - output_types:  ClassVar[dict]，按 capability 覆盖，优先于 output_type
    重写 execute() 的子类（如 SQLSkill）自行保证输出类型，不走本归一化。
    """

    name: str = ""
    capabilities: ClassVar[list[str]] = []
    description: str = ""
    params_schema: ClassVar[dict] = {}
    examples: ClassVar[list[dict]] = []
    output_type: ClassVar[str] = "text"
    output_types: ClassVar[dict] = {}
    capability_metadata: ClassVar[dict[str, dict]] = {}
    # 类级默认：execute 未显式传参时生效。子类可覆盖（如 competitor
    # 单次抓取自身 timeout=90s，必须大于它，否则被 Skill 层先判超时重试）
    default_timeout: ClassVar[float] = DEFAULT_TIMEOUT
    default_max_retries: ClassVar[int] = DEFAULT_MAX_RETRIES

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # 内部兼容类（_CompatSkill）跳过校验
        if cls.__name__.startswith("_"):
            return
        if not getattr(cls, "name", ""):
            raise TypeError(f"{cls.__name__} 必须声明 name（manifest skill 绑定需要）")

    @property
    @abstractmethod
    def _tool_fn(self) -> Any:
        """返回关联的 LangChain Tool 可调用对象"""
        ...

    def _select_tool(self, capability: str, params: dict) -> tuple[Any, dict]:
        """返回本次调用使用的 (tool_fn, invoke_params)。

        默认用 _tool_fn 原样传参；一个 Skill 对应多个 Tool 时覆盖此钩子，
        按 capability/params 分发（参照 CompetitorAnalysisSkill），并应把
        params 过滤到目标 Tool 的签名内——LangChain invoke 遇到未知参数
        会直接抛错。
        """
        return self._tool_fn, params

    # 历史名保留（子类/测试可能引用）：指向模块级 PARAM_TYPE_CHECKS
    _PARAM_TYPE_CHECKS = PARAM_TYPE_CHECKS

    def _validate_params(self, params: dict, capability: str = "") -> str | None:
        """按 params_schema 运行时校验入参，返回错误消息（None=通过）。

        校验语义收敛在模块级 validate_params()（tool_selector 的 FC
        填参校验共用同一份），失败属 invalid_param（不可重试）——与其
        让 Tool 深处报晦涩错误再空转重试，不如在 Skill 边界给模型可读
        的失败原因。
        """
        metadata = self.capability_metadata.get(capability, {})
        schema = metadata.get("params_schema", self.params_schema)
        return validate_params(schema, params)

    def _normalize_output(self, capability: str, output: Any) -> Any:
        """输出契约边界：按 capability 声明的类型归一化 Tool 返回值。

        text（默认）: 保证返回 str——dict/list 序列化为 JSON 字符串。
          背景: sql.query 曾把 SQLResult dict 原样透传进 final_answer，
          下游所有按字符串处理的地方（done 事件 sources、emit_delta、
          记忆落库写 VARCHAR）全部崩溃，且日志中 2026-09-07 已有同类报错。
        structured: 保证返回 dict——str 尝试 json.loads，失败保持原样并告警。
        """
        import json

        declared = self.output_types.get(capability, self.output_type)
        type_name = type(output).__name__
        if declared == "structured":
            if isinstance(output, dict):
                return output
            if isinstance(output, str):
                try:
                    parsed = json.loads(output)
                    if isinstance(parsed, dict):
                        logger.warning(
                            f"[{self.name}] capability={capability} 声明 structured "
                            f"但 Tool 返回 str，已从 JSON 解析"
                        )
                        return parsed
                except (ValueError, TypeError):
                    pass
            logger.warning(
                f"[{self.name}] capability={capability} 声明 structured "
                f"但 Tool 返回 {type_name}，保持原样"
            )
            return output

        # 默认 text
        if isinstance(output, str):
            return output
        logger.warning(
            f"[{self.name}] capability={capability} 声明 text 输出但 Tool "
            f"返回 {type_name}，已序列化为字符串"
        )
        if isinstance(output, (dict, list)):
            return json.dumps(output, ensure_ascii=False, default=str)
        return str(output)

    async def execute(
        self,
        state: dict,
        step_capability: str = "",
        max_retries: int | None = None,
        timeout: float | None = None,
    ) -> dict:
        """执行当前 Capability：从 state 提取 step → 调用 Tool → 写回结果。

        每次 Tool 调用都会创建 Span（type=tool_call），重试记录为 span.events。
        timeout/max_retries 缺省时取类级 default_timeout / default_max_retries。
        返回: {"step_results": {...}}
        """
        from backend.observability.alerts import make_alert, log_degradation
        from backend.observability.tracer import trace_collector

        # 原始实参（None=未显式指定）：治理层用它按"显式实参 > 注册表策略 >
        # 类级默认"的优先级解析；legacy 循环仍用补齐默认值后的形态
        raw_timeout = timeout
        raw_max_retries = max_retries

        timeout = self.default_timeout if timeout is None else timeout
        max_retries = self.default_max_retries if max_retries is None else max_retries

        step_id = state.get("current_step_id")
        if not step_id:
            logger.error(f"[{self.name}] current_step_id 为空")
            return {}

        plan = state.get("plan", {})
        step_info = plan.get("nodes", {}).get(step_id)
        if not step_info:
            logger.error(f"[{self.name}] 找不到 step: {step_id}")
            return {}

        step_results = dict(state.get("step_results", {}))

        sr = step_results.get(step_id, {})
        sr["step_id"] = step_id
        sr["capability"] = step_capability or step_info.get("capability", "unknown")
        sr["description"] = step_info.get("description", "")
        sr["retries"] = 0

        params = dict(step_info.get("params", {}))
        params.pop("_previous_outputs", None)

        # ── 四层前置校验：参数 + 权限失败不可进入 Tool ──
        capability = sr["capability"]
        try:
            validate_invocation(
                sr["capability"], params,
                lambda candidate_params: self._validate_params(
                    candidate_params, capability,
                ),
            )
        except ValidationFailure as exc:
            reason = (exc.envelope.details or {}).get("reason", "")
            error = (
                f"参数校验失败: {reason}"
                if exc.layer == "parameter" and reason
                else exc.envelope.message
            )
            error_protocol = error_envelope_from_exception(
                exc, source="skill"
            ).to_dict()
            sr.update(status="failed", output=None, error=error,
                      error_type=error_protocol["code"].lower(), retries=0,
                      error_protocol=error_protocol,
                      started_at=time.time(), finished_at=time.time())
            step_results[step_id] = dict(sr)
            logger.warning(f"[{self.name}] step={step_id} {error}")
            # 只返回自有步骤（2026-09-15 整改）：并行 Send 时各分支不再
            # 携带他人的 running 过期快照；全局累积由 AgentState
            # ._merge_step_results 按键合并完成。
            return {"step_results": {step_id: step_results[step_id]}}

        # ── Tracing: 创建 tool_call span ──
        cap = sr["capability"]
        # P1-5: 真实嵌套 — 优先挂到已存在的执行方 span（中间件节点），
        # 旧实现固定指向上游尚未合成的 "skill-{step_id}"，被 tracer 回退到
        # root 导致整棵树扁平。
        parent_id = f"skill-{step_id}"
        active_trace = trace_collector.current()
        if active_trace is not None:
            existing = {s.span_id for s in active_trace.spans}
            for cand in (f"skill-{step_id}", f"{self.name}:{step_id}",
                         "skill_executor", "workflow_executor"):
                if cand in existing:
                    parent_id = cand
                    break
        tool_span = trace_collector.start_span(
            f"tool-{step_id}", parent_id=parent_id,
            name=f"{self.name}:{cap}" if self.name else cap,
            type="tool_call",
            input={"params": params, "capability": cap},
        )

        tool_fn, invoke_params = self._select_tool(sr["capability"], params)

        # ── 执行：统一治理层（默认）或旧执行循环（紧急回滚开关）──
        if _tool_runtime_enabled():
            await self._execute_governed(
                state, sr, step_results, tool_fn, invoke_params, params,
                raw_timeout, raw_max_retries, tool_span,
            )
        else:
            await self._execute_legacy_loop(
                sr, step_results, tool_fn, invoke_params, params,
                timeout, max_retries, tool_span,
            )
        return {"step_results": {step_id: step_results[step_id]}}

    # ================================================================
    # 统一治理执行（core/tool_runtime）：Deadline / Timeout / Retry /
    # CircuitBreaker / Bulkhead / ErrorMapper / Metrics
    # ================================================================
    async def _execute_governed(
        self, state: dict, sr: dict, step_results: dict,
        tool_fn: Any, invoke_params: dict, params: dict,
        timeout: float | None, max_retries: int | None,
        tool_span: str,
    ) -> None:
        from dataclasses import replace as dc_replace

        from backend.observability.alerts import make_alert, log_degradation
        from backend.observability.tracer import trace_collector as _tc
        from backend.core.tool_runtime.executor import safe_tool_executor
        from backend.core.tool_runtime.models import ToolCriticality, ToolStatus
        from backend.core.tool_runtime.policy import get_policy, is_registered
        from backend.core.tool_runtime.deadline import RequestDeadline

        trace_collector = _tc

        cap = sr["capability"]
        reg = get_policy(cap)
        registered = is_registered(cap)

        # 参数优先级：显式实参 > 注册表策略（仅注册 tool）> 类级默认（未注册 tool 保持旧行为）
        eff_timeout_s = timeout
        if eff_timeout_s is None:
            eff_timeout_s = (reg.timeout_ms / 1000) if registered else self.default_timeout
        eff_retries = max_retries
        if eff_retries is None:
            eff_retries = reg.retries if registered else self.default_max_retries
        pol = dc_replace(reg, timeout_ms=eff_timeout_s * 1000, retries=eff_retries)

        deadline = _deadline_from_state(state)

        def _on_event(event: str, info: dict) -> None:
            """executor 治理事件 → trace span events（timeout/fail 标 warn）。"""
            level = "warn" if ("fail" in event or "timeout" in event or "insufficient" in event) else "info"
            trace_collector.add_event(tool_span, event, level, event, info)

        result = await safe_tool_executor.run(
            tool_key=cap,
            call=lambda: asyncio.to_thread(tool_fn.invoke, invoke_params),
            policy=pol, deadline=deadline,
            domain=self.name or cap.split(".", 1)[0],
            on_event=_on_event,
        )

        # ── ToolResult → step_results 契约字段（下游零改动）+ 治理扩展字段 ──
        sr["retries"] = result.retry_count
        sr["latency_ms"] = result.latency_ms
        sr["tool_status"] = result.status.value
        sr["criticality"] = pol.criticality.value
        sr["operation_type"] = pol.operation_type.value
        sr["error_code"] = result.error_code
        sr["fallback_used"] = result.fallback_used
        sr["degraded"] = result.status is ToolStatus.DEGRADED

        if result.status is ToolStatus.SUCCESS:
            output = self._normalize_output(cap, result.data)
            declared_type = self.output_types.get(cap, self.output_type)
            try:
                validate_output(cap, output, declared_type)
                validate_semantics(cap, params, output)
            except ValidationFailure as e:
                # 后置校验失败是确定性错误，不重试（与旧循环同语义）
                sr.update(status="failed", output=None,
                          error=f"输出校验失败: {e.layer}", error_type="invalid_param",
                          finished_at=time.time())
                step_results[sr["step_id"]] = dict(sr)
                trace_collector.end_span(tool_span, status="error",
                    metrics={"error": f"validation:{e.layer}", "retries": result.retry_count})
                logger.warning(f"[{self.name}] step={sr['step_id']} 输出校验失败: {e.layer}")
                return
            sr.update(status="success", output=output, error=None, error_type=None,
                      finished_at=time.time())
            # L1 上下文预算（2026-09-22）：工具结果写入 step_results 前统一
            # Guard——超 TOOL_INLINE_MAX_TOKENS 降级为预览（含 18 个 Markdown
            # 老 Tool，它们也走本基类）。校验在 Guard 之前：Guard 产出的预览
            # 结构不再参与 output 契约校验。
            output = _apply_tool_result_budget(output, cap, sr["step_id"])
            if isinstance(output, dict) and output.get("context_compacted"):
                sr["context_compacted"] = True
                sr["original_output_tokens"] = output.get("original_tokens")
            sr["output"] = output
            step_results[sr["step_id"]] = dict(sr)
            elapsed = result.latency_ms / 1000
            logger.info(f"[{self.name}] step={sr['step_id']} 成功 (耗时 {elapsed:.2f}s)")
            trace_collector.end_span(tool_span,
                output={"result": output},
                metrics={"elapsed_s": round(elapsed, 2), "retries": result.retry_count})
            return

        # ── 失败/降级分支：按 criticality 决定 workflow 走向（§8/§15/§16）──
        user_msg = result.user_friendly_message()
        sr["needs_verification"] = bool(
            result.fallback_used == "check_operation_status")

        if pol.criticality is ToolCriticality.OPTIONAL:
            # optional：直接跳过，一个可选 Tool 失败不能导致整个 Workflow error
            sr.update(status="skipped", output=None,
                      error=f"{user_msg}（非关键步骤已跳过）",
                      error_type=_status_to_error_type(result.status, result.error_code or ""),
                      finished_at=time.time())
            step_results[sr["step_id"]] = dict(sr)
            trace_collector.end_span(tool_span, status="skipped",
                metrics={"error": result.error_code or "", "retries": result.retry_count,
                         "fallback": result.fallback_used or ""})
            logger.warning(
                f"[{self.name}] step={sr['step_id']} optional 失败已跳过: {result.error_code}")
            return

        # required / important：步骤失败，Reporter 给明确说明；
        # important 额外把业务结果标记为 degraded（root trace 不再整体 error）
        error_protocol = error_envelope_from_exception(
            result.original_exception or RuntimeError(user_msg),
            source="skill",
        ).to_dict()
        sr.update(status="failed", output=None, error=user_msg,
                  error_type=_status_to_error_type(result.status, result.error_code or ""),
                  error_protocol=error_protocol, finished_at=time.time())
        step_results[sr["step_id"]] = dict(sr)

        trace_collector.end_span(tool_span, status="error",
            metrics={"error": result.error_message or user_msg,
                     "error_code": result.error_code or "",
                     "retries": result.retry_count,
                     "fallback": result.fallback_used or ""})

        code = ("WORKER_TIMEOUT" if result.status is ToolStatus.TIMEOUT
                else "WORKER_RETRY_EXHAUST")
        alert = make_alert(code, {"step_id": sr["step_id"],
                                  "error": result.error_message or user_msg,
                                  "criticality": pol.criticality.value})
        log_degradation(alert)
        logger.error(f"[{self.name}] step={sr['step_id']} 最终失败 "
                     f"({pol.criticality.value}): {result.error_code} {user_msg}")

        # 业务结果标注：required → failed；important → degraded
        _mark_business_outcome(trace_collector, pol.criticality, cap,
                               result.error_code or "")

    # ================================================================
    # 旧执行循环（TOOL_RUNTIME_ENABLED=false 时回滚用，行为与历史版本一致）
    # ================================================================
    async def _execute_legacy_loop(
        self, sr: dict, step_results: dict,
        tool_fn: Any, invoke_params: dict, params: dict,
        timeout: float, max_retries: int,
        tool_span: str,
    ) -> None:
        from backend.observability.alerts import make_alert, log_degradation
        from backend.observability.tracer import trace_collector as _tc

        trace_collector = _tc
        step_id = sr["step_id"]
        last_error = None
        for attempt in range(max_retries + 1):
            sr["status"] = "running"
            sr["started_at"] = time.time()
            sr["retries"] = attempt
            step_results[step_id] = dict(sr)

            try:
                logger.info(
                    f"[{self.name}] step={step_id} cap={sr['capability']} "
                    f"(第{attempt+1}/{max_retries+1}次，timeout={timeout}s)"
                )

                output = await asyncio.wait_for(
                    asyncio.to_thread(tool_fn.invoke, invoke_params),
                    timeout=timeout,
                )
                output = self._normalize_output(sr["capability"], output)
                declared_type = self.output_types.get(
                    sr["capability"], self.output_type
                )
                validate_output(sr["capability"], output, declared_type)
                validate_semantics(sr["capability"], params, output)

                sr["status"] = "success"
                sr["output"] = output
                sr["error"] = None
                sr["error_type"] = None
                sr["finished_at"] = time.time()
                # L1 上下文预算：与治理路径同语义（校验后、落 step_results 前）
                output = _apply_tool_result_budget(output, sr["capability"], step_id)
                if isinstance(output, dict) and output.get("context_compacted"):
                    sr["context_compacted"] = True
                    sr["original_output_tokens"] = output.get("original_tokens")
                sr["output"] = output
                step_results[step_id] = dict(sr)

                elapsed = sr["finished_at"] - sr.get("started_at", sr["finished_at"])
                logger.info(f"[{self.name}] step={step_id} 成功 (耗时 {elapsed:.2f}s)")

                trace_collector.end_span(tool_span,
                    output={"result": output},
                    metrics={"elapsed_s": round(elapsed, 2), "retries": attempt})
                return

            except ValidationFailure as e:
                # 后置校验失败是确定性错误，不重试 Tool，避免重复副作用。
                last_error = e
                logger.warning(
                    f"[{self.name}] step={step_id} 校验失败: {e.layer}"
                )
                break

            except asyncio.TimeoutError:
                last_error = asyncio.TimeoutError(f"步骤执行超时（{timeout}s）")
                logger.warning(f"[{self.name}] step={step_id} 超时")
                trace_collector.add_event(tool_span, f"retry_{attempt+1}", "warn",
                    f"超时重试 ({timeout}s)", {"attempt": attempt + 1})

            except Exception as e:
                last_error = e
                logger.warning(f"[{self.name}] step={step_id} 失败: {e}")
                trace_collector.add_event(
                    tool_span, f"retry_{attempt+1}", "warn",
                    f"执行失败: {str(last_error)[:80]}",
                    {"attempt": attempt + 1},
                )

            if not _is_retryable(str(last_error)):
                break

            if attempt < max_retries:
                delay = RETRY_BACKOFF_BASE ** (attempt + 1)
                await asyncio.sleep(delay)

        if sr.get("status") == "running":
            error_protocol = error_envelope_from_exception(
                last_error or RuntimeError("skill execution failed"),
                source="skill",
            ).to_dict()
            sr["status"] = "failed"
            sr["error"] = error_protocol["message"]
            sr["error_type"] = (
                last_error.envelope.code.value.lower()
                if isinstance(last_error, ValidationFailure)
                else classify_error(last_error)
            )
            sr["error_protocol"] = error_protocol
            sr["finished_at"] = time.time()
            step_results[step_id] = dict(sr)

            trace_collector.end_span(tool_span, status="error",
                metrics={"error": str(last_error), "retries": max_retries})

            code = ("WORKER_TIMEOUT" if sr["error_type"] == "timeout"
                    else "WORKER_RETRY_EXHAUST")
            alert = make_alert(code, {"step_id": step_id, "error": str(last_error)})
            log_degradation(alert)
            logger.error(f"[{self.name}] step={step_id} 最终失败: {last_error}")


def _apply_tool_result_budget(output: Any, capability: str, step_id: str) -> Any:
    """L1 工具结果预算 Guard 的接线点（软失败：Guard 异常不拖垮 Skill 执行）。"""
    try:
        from backend.context_budget.tool_guard import guard_tool_result
        return guard_tool_result(output, capability=capability, step_id=step_id)
    except Exception:
        logger.debug("tool result budget guard 失败，原样放行", exc_info=True)
        return output


def _tool_runtime_enabled() -> bool:
    """治理层开关（紧急回滚用）。软失败：配置缺失时按开启处理。"""
    try:
        from backend.config.settings import TOOL_RUNTIME_ENABLED
        return bool(TOOL_RUNTIME_ENABLED)
    except Exception:
        return True


def _deadline_from_state(state: dict | None):
    """从图状态取请求 Deadline；无（后台任务/测试假 state）返回 None = 不做预算约束。"""
    try:
        if not state:
            return None
        rc = state.get("request_context")
        if rc is None:
            return None
        if isinstance(rc, dict):
            from backend.core.tool_runtime.deadline import RequestDeadline
            return RequestDeadline.from_dict(rc.get("deadline"))
        return getattr(rc, "deadline", None)
    except Exception:
        return None


_STATUS_TO_ERROR_TYPE = {
    ToolStatus.TIMEOUT: "timeout",
    ToolStatus.UNAUTHORIZED: "permission",
    ToolStatus.INVALID_REQUEST: "invalid_param",
    ToolStatus.UNAVAILABLE: "network",
    ToolStatus.RATE_LIMITED: "network",
    ToolStatus.FAILED: "unknown",
}


def _status_to_error_type(status: "ToolStatus", error_code: str = "") -> str:
    """ToolStatus(+error_code) → 旧版 sr["error_type"] 词表
    （timeout/permission/not_found/invalid_param/network/unknown），
    与 classify_error 的留痕语义保持兼容。"""
    code = (error_code or "").lower()
    if code in ("not_found", "timeout"):
        return code
    return _STATUS_TO_ERROR_TYPE.get(status, "unknown")


def _mark_business_outcome(trace_collector, criticality, capability: str,
                           error_code: str) -> None:
    """把 criticality 语义写进 trace metadata（§16：business_outcome 三态）。

    required → failed；important → degraded（不覆盖已有的 failed）。
    Reporter 正常给出降级回答时 root trace 显示 degraded 而非 error。
    """
    try:
        from backend.core.tool_runtime.models import ToolCriticality
        from backend.core.tool_runtime.metrics import record_request_degraded

        trace = trace_collector.current()
        if trace is None:
            return
        meta = trace.metadata
        if criticality is ToolCriticality.REQUIRED:
            meta["business_outcome"] = "failed"
        else:
            if meta.get("business_outcome") != "failed":
                meta["business_outcome"] = "degraded"
            record_request_degraded(capability.split(".", 1)[0] or "workflow")
        reasons = meta.setdefault("degraded_reasons", [])
        entry = {"tool": capability, "error_code": error_code}
        if entry not in reasons:
            reasons.append(entry)
        meta["degraded"] = True
    except Exception:
        logger.debug("business_outcome 标注失败", exc_info=True)


# 向后兼容
async def execute_with_retry(state: dict, tool_fn, max_retries=DEFAULT_MAX_RETRIES, timeout=DEFAULT_TIMEOUT) -> dict:
    skill = _CompatSkill(tool_fn)
    return await skill.execute(state, max_retries=max_retries, timeout=timeout)


class _CompatSkill(BaseSkill):
    name = "_compat"
    capabilities = []

    def __init__(self, tool_fn):
        self._tool = tool_fn

    @property
    def _tool_fn(self):
        return self._tool
