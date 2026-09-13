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

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 1.5

# ── 错误分类：类型 → 判定子串（lower 匹配，先命中先定类型）。
# 决定两件事：可否重试（UNRETRYABLE_ERROR_TYPES 之外均可重试）、
# sr["error_type"] 的留痕取值。取代旧版纯布尔匹配的 UNRETRYABLE_PATTERNS。
_ERROR_TYPE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("timeout", ("超时", "timed out", "timeout")),
    ("permission", ("权限不足", "permission denied", "unauthorized", "forbidden")),
    ("not_found", ("no such table", "table does not exist", "not found")),
    ("syntax", ("syntax error",)),
    ("invalid_param", ("column not found", "invalid parameter",
                       "参数校验失败", "缺少必填参数")),
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


class BaseSkill(ABC):
    """Skill 抽象基类。每个 Skill 封装一组 Capability。

    子类需声明:
      - capabilities:    ClassVar[list[str]]  — 如 ["sql.query", "sql.analyze"]
      - description:     str                   — Planner prompt 用（必填）
      - params_schema:   dict                  — 参数说明（必填）。推荐类型化格式
                        {"param": {"type": "string|int|object|boolean", "required": bool,
                                   "description": str, "enum": [...]}}；
                        旧式纯字符串值向后兼容（视为 string 可选）
      - examples:        list[dict]            — Planner 用的示例（至少 1 个）
      - _tool_fn:        property → LangChain Tool
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
    # 类级默认：execute 未显式传参时生效。子类可覆盖（如 competitor
    # 单次抓取自身 timeout=90s，必须大于它，否则被 Skill 层先判超时重试）
    default_timeout: ClassVar[float] = DEFAULT_TIMEOUT
    default_max_retries: ClassVar[int] = DEFAULT_MAX_RETRIES

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # 内部兼容类（_CompatSkill）跳过校验
        if cls.__name__.startswith("_"):
            return
        # 必填校验：避免漏写 description 导致 Planner 拿不到能力描述
        if not getattr(cls, "description", ""):
            raise TypeError(
                f"{cls.__name__} 必须声明 description（Planner prompt 需要）"
            )
        if not getattr(cls, "capabilities", []):
            raise TypeError(
                f"{cls.__name__} 必须声明 capabilities（至少 1 个）"
            )
        if not getattr(cls, "examples", []):
            raise TypeError(
                f"{cls.__name__} 必须声明 examples（至少 1 个，Planner 参考）"
            )

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

    # params_schema 声明的 type → 运行时类型检查
    _PARAM_TYPE_CHECKS = {
        "string": str,
        "int": int,
        "object": dict,
        "boolean": bool,
        "number": (int, float),
    }

    def _validate_params(self, params: dict) -> str | None:
        """按 params_schema 运行时校验入参，返回错误消息（None=通过）。

        只校验显式声明的参数，未声明的键不拦（交由 Tool 签名兜底）；
        旧式字符串声明视为 string 可选，跳过。校验失败属 invalid_param
        （不可重试）——与其让 Tool 深处报晦涩错误再空转重试，不如
        在 Skill 边界给模型可读的失败原因。
        """
        errors = []
        for name, spec in self.params_schema.items():
            if isinstance(spec, str):
                continue
            val = params.get(name)
            if spec.get("required") and val in (None, ""):
                errors.append(
                    f"缺少必填参数 {name}: {spec.get('description', '')}")
                continue
            if val is None:
                continue
            ptype = spec.get("type", "string")
            expected = self._PARAM_TYPE_CHECKS.get(ptype)
            type_ok = expected is not None and isinstance(val, expected)
            # bool 是 int 的子类：声明 int 时布尔值应判为类型错误
            if ptype == "int" and isinstance(val, bool):
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

        # ── 参数契约校验：失败不可重试，直接落 failed ──
        param_error = self._validate_params(params)
        if param_error:
            error = f"参数校验失败: {param_error}"
            sr.update(status="failed", output=None, error=error,
                      error_type="invalid_param", retries=0,
                      started_at=time.time(), finished_at=time.time())
            step_results[step_id] = dict(sr)
            logger.warning(f"[{self.name}] step={step_id} {error}")
            return {"step_results": step_results}

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

        last_error = None
        tool_fn, invoke_params = self._select_tool(sr["capability"], params)
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

                sr["status"] = "success"
                sr["output"] = output
                sr["error"] = None
                sr["error_type"] = None
                sr["finished_at"] = time.time()
                step_results[step_id] = dict(sr)

                elapsed = sr["finished_at"] - sr.get("started_at", sr["finished_at"])
                logger.info(f"[{self.name}] step={step_id} 成功 (耗时 {elapsed:.2f}s)")

                # ── Tracing: 成功 ──
                trace_collector.end_span(tool_span,
                    output={"result": output},
                    metrics={"elapsed_s": round(elapsed, 2), "retries": attempt})
                break

            except asyncio.TimeoutError:
                last_error = f"步骤执行超时（{timeout}s）"
                logger.warning(f"[{self.name}] step={step_id} 超时")
                trace_collector.add_event(tool_span, f"retry_{attempt+1}", "warn",
                    f"超时重试 ({timeout}s)", {"attempt": attempt + 1})

            except Exception as e:
                last_error = str(e)
                logger.warning(f"[{self.name}] step={step_id} 失败: {e}")
                trace_collector.add_event(tool_span, f"retry_{attempt+1}", "warn",
                    f"执行失败: {last_error[:80]}", {"attempt": attempt + 1})

            if not _is_retryable(str(last_error)):
                break

            if attempt < max_retries:
                delay = RETRY_BACKOFF_BASE ** (attempt + 1)
                await asyncio.sleep(delay)

        if sr.get("status") == "running":
            sr["status"] = "failed"
            sr["error"] = last_error
            sr["error_type"] = classify_error(last_error)
            sr["finished_at"] = time.time()
            step_results[step_id] = dict(sr)

            # ── Tracing: 最终失败 ──
            trace_collector.end_span(tool_span, status="error",
                metrics={"error": last_error, "retries": max_retries})

            code = ("WORKER_TIMEOUT" if sr["error_type"] == "timeout"
                    else "WORKER_RETRY_EXHAUST")
            alert = make_alert(code, {"step_id": step_id, "error": last_error})
            log_degradation(alert)
            logger.error(f"[{self.name}] step={step_id} 最终失败: {last_error}")

        return {"step_results": step_results}


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
