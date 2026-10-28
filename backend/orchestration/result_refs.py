"""result_refs.py — 任务级受控结果引用（决策 #25）

多步任务里的大结果不再整包驻留 step_results 与 LLM 上下文：

  - ``maybe_offload``：Skill 输出序列化后超阈值 → payload 落
    ``ai.task_result_refs``（默认 24h 失效），返回引用对象
    ``{"__result_ref__": {ref_id, summary, row_count, size_bytes, schema_summary}}``；
    未超阈值原样返回（绝大多数调用走这条快路径，零 DB 开销）。
  - ``resolve_ref``：受控解引用点按租户校验取回整包（租户不匹配
    PermissionError、过期/不存在 LookupError）。
  - ``resolve_previous_outputs``：下游步骤参数注入点把引用对象解回整包，
    保持既有数据流行为（消费点注入见 supervisor/scheduler 与
    graph/direct_executor）。
  - ``ref_value`` / ``resolve_param_templates``：Schema 映射——按
    ``step_id.field.path`` 字段路径取值（None/缺失显式报错，不静默空串）。
  - ``materialize_step_results_for_context``：LLM 上下文装配点（Reporter）
    把引用对象替换为摘要文本——**关键不变式：LLM 上下文只见引用摘要；
    真实数据只在受控解引用点出现**。

失效策略：``expires_at`` 在 resolve 时强制校验（权威）；``cleanup_expired``
为兜底 GC，offload 时低频顺带触发（软失败）。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from backend.shared.logger import logger

# 引用对象标记键：step_results / previous_outputs 里出现该键即视为引用
RESULT_REF_KEY = "__result_ref__"
# 解引用失败标记键：resolve_previous_outputs 对失败条目显式留痕（不静默回空）
RESULT_REF_ERROR_KEY = "__result_ref_error__"

DEFAULT_THRESHOLD_BYTES = 8192
REF_TTL_HOURS = 24
SUMMARY_MAX_CHARS = 500

# 兜底 GC 最小间隔（秒）：offload 高频路径上避免每次都打 DB
_CLEANUP_INTERVAL_SECONDS = 3600
_last_cleanup_at: float = 0.0


def _configured_threshold_bytes() -> int:
    """offload 触发阈值（字节）。env RESULT_REF_THRESHOLD_BYTES 覆盖；0 = 禁用。"""
    try:
        from backend.config.settings import RESULT_REF_THRESHOLD_BYTES as cfg
        return max(0, int(cfg))
    except Exception:  # noqa: BLE001 — 配置缺失按默认值（绝不阻断执行链）
        return DEFAULT_THRESHOLD_BYTES


def is_result_ref(obj: Any) -> bool:
    """判断对象是否为本模块产出的引用对象。"""
    return (
        isinstance(obj, dict)
        and isinstance(obj.get(RESULT_REF_KEY), dict)
        and bool(obj[RESULT_REF_KEY].get("ref_id"))
    )


def ref_id_of(obj: Any) -> str:
    """引用对象 → ref_id；非引用返回空串。"""
    if is_result_ref(obj):
        return str(obj[RESULT_REF_KEY]["ref_id"])
    return ""


# =====================================================
# 摘要（LLM 上下文唯一可见的数据形态）
# =====================================================

def schema_summary_of(output: Any) -> dict:
    """输出对象 → 结构概要（JSONB 落库用）：类型/键集合/行数，不含数据本体。"""
    if isinstance(output, dict):
        out: dict[str, Any] = {"type": "object", "keys": list(output.keys())[:20]}
        rows = output.get("rows")
        if isinstance(rows, list):
            out["row_count"] = len(rows)
            if rows and isinstance(rows[0], dict):
                out["row_keys"] = list(rows[0].keys())[:20]
        return out
    if isinstance(output, (list, tuple)):
        out = {"type": "array", "length": len(output)}
        if output:
            out["item_type"] = type(output[0]).__name__
            if isinstance(output[0], dict):
                out["item_keys"] = list(output[0].keys())[:20]
        return out
    return {"type": type(output).__name__}


def summary_of(output: Any) -> str:
    """输出对象 → 紧凑摘要文本（≤500 字符），供 LLM 上下文与引用对象携带。

    dict/list 生成结构概要（键集合/行数/首元素预览），绝不全量序列化；
    任何摘要异常都不允许阻断 offload 主路径。
    """
    try:
        text = _summary_impl(output)
    except Exception:  # noqa: BLE001 — 摘要软失败
        logger.debug("result ref 摘要生成失败", exc_info=True)
        text = ""
    if not text:
        text = f"<{type(output).__name__} 结果>"
    if len(text) > SUMMARY_MAX_CHARS:
        text = text[: SUMMARY_MAX_CHARS - 1] + "…"
    return text


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _summary_impl(output: Any) -> str:
    if output is None:
        return "（空结果）"
    if isinstance(output, str):
        return _clip(output, SUMMARY_MAX_CHARS)
    if isinstance(output, (int, float, bool)):
        return str(output)
    if isinstance(output, (list, tuple)):
        if not output:
            return "list(共0项)"
        first = _clip(
            json.dumps(output[0], ensure_ascii=False, default=str), 200)
        return f"list(共{len(output)}项) 首元素: {first}"
    if isinstance(output, dict):
        rows = output.get("rows")
        if isinstance(rows, list):
            columns = output.get("columns") or (
                list(rows[0].keys()) if rows and isinstance(rows[0], dict) else [])
            head = f"查询结果: 列={_clip(json.dumps(columns, ensure_ascii=False), 160)}"
            head += f"，共{len(rows)}行"
            if rows and isinstance(rows[0], dict):
                head += f"，首行: {_clip(json.dumps(rows[0], ensure_ascii=False, default=str), 200)}"
            return head
        keys = _clip(json.dumps(list(output.keys())[:10], ensure_ascii=False), 160)
        head = f"object({len(output)}键): {keys}"
        for value in output.values():
            if isinstance(value, (str, int, float, bool)) and value not in ("", None):
                head += f"，{type(value).__name__}示例: {_clip(str(value), 120)}"
                break
        return head
    return str(output)[:SUMMARY_MAX_CHARS]


def _row_count_of(output: Any) -> int | None:
    """行数计数：dict.rows / list 取长度，其余 None（非行式结果）。"""
    if isinstance(output, dict) and isinstance(output.get("rows"), list):
        return len(output["rows"])
    if isinstance(output, (list, tuple)):
        return len(output)
    return None


# =====================================================
# offload / resolve（DB 读写）
# =====================================================

def maybe_offload(
    step_output: Any,
    *,
    request_id: str = "",
    thread_id: str = "",
    step_id: str = "",
    capability: str = "",
    tenant_id: str = "default",
    user_id: str = "",
    threshold_bytes: int | None = None,
) -> Any:
    """Skill 输出超阈值 → 落库换引用对象；未超阈值原样返回（零开销快路径）。

    threshold_bytes 显式传参优先（测试/调用方覆盖）；缺省 None = 读
    RESULT_REF_THRESHOLD_BYTES（默认 8192；0 = 永不 offload，部署零行为变化）。
    不可 JSON 序列化的输出原样返回（内联兼容）。DB 写失败向上抛——
    由调用方（BaseSkill 边界）决定软降级回内联，本函数不吞异常。
    """
    limit = (
        threshold_bytes
        if threshold_bytes is not None
        else _configured_threshold_bytes()
    )
    if limit <= 0 or step_output is None or is_result_ref(step_output):
        return step_output

    try:
        data = json.dumps(step_output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return step_output
    size = len(data.encode("utf-8"))
    if size <= limit:
        return step_output

    _opportunistic_cleanup()

    ref_id = f"taskref_{uuid.uuid4().hex}"
    row_count = _row_count_of(step_output)
    schema = schema_summary_of(step_output)
    with _memory_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ai.task_result_refs
                    (id, request_id, thread_id, step_id, capability,
                     tenant_id, user_id, payload, schema_summary,
                     row_count, size_bytes, expires_at)
                VALUES (%(id)s, %(request_id)s, %(thread_id)s, %(step_id)s,
                        %(capability)s, %(tenant_id)s, %(user_id)s,
                        %(payload)s, %(schema_summary)s, %(row_count)s,
                        %(size_bytes)s, %(expires_at)s)
                """,
                {
                    "id": ref_id,
                    "request_id": request_id or "",
                    "thread_id": thread_id or "",
                    "step_id": step_id or "",
                    "capability": capability or "",
                    "tenant_id": tenant_id or "default",
                    "user_id": user_id or "",
                    # json.dumps 已成功 → psycopg2 Json 参数安全
                    "payload": _jsonb(step_output),
                    "schema_summary": _jsonb(schema),
                    "row_count": row_count,
                    "size_bytes": size,
                    "expires_at": _expires_at_sql(),
                },
            )
    logger.info(
        f"[ResultRefs] step={step_id} capability={capability} offload "
        f"{size}B → {ref_id}（tenant={tenant_id}）"
    )
    return {
        RESULT_REF_KEY: {
            "ref_id": ref_id,
            "summary": summary_of(step_output),
            "row_count": row_count,
            "size_bytes": size,
            "schema_summary": schema,
        }
    }


def resolve_ref(ref_id: str, tenant_id: str, user_id: str = "") -> Any:
    """按 ref_id 取回整包 payload（受控解引用点专用）。

    - 不存在 / 已过期 → LookupError；
    - 租户不匹配 → PermissionError（硬校验，user_id 仅随行留痕）。
    """
    if not ref_id:
        raise LookupError("结果引用 id 为空")
    with _memory_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload, tenant_id, expires_at
                FROM ai.task_result_refs WHERE id = %(id)s
                """,
                {"id": ref_id},
            )
            row = cur.fetchone()
    if row is None:
        raise LookupError(f"结果引用不存在或已清理: {ref_id}")
    payload, row_tenant, expires_at = row
    if expires_at is not None and _now() >= expires_at:
        raise LookupError(f"结果引用已过期: {ref_id}")
    if (row_tenant or "default") != (tenant_id or "default"):
        raise PermissionError(
            f"结果引用 {ref_id} 不属于当前租户，拒绝访问"
        )
    logger.info(f"[ResultRefs] resolve {ref_id}（tenant={tenant_id}, user={user_id}）")
    return payload


def cleanup_expired() -> int:
    """删除过期引用行，返回删除数量（兜底 GC；resolve 时校验才是权威）。"""
    with _memory_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ai.task_result_refs WHERE expires_at < now()")
            deleted = cur.rowcount or 0
    if deleted:
        logger.info(f"[ResultRefs] 清理过期引用 {deleted} 条")
    return deleted


def _opportunistic_cleanup() -> None:
    """offload 路径低频顺带 GC（软失败：绝不影响 offload 本身）。"""
    global _last_cleanup_at
    now = time.monotonic()
    if now - _last_cleanup_at < _CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup_at = now
    try:
        cleanup_expired()
    except Exception:  # noqa: BLE001 — GC 软失败
        logger.debug("[ResultRefs] 过期清理失败（软降级）", exc_info=True)


# =====================================================
# 解引用注入点（保持既有整包数据流）
# =====================================================

def resolve_previous_outputs(
    previous_outputs: dict[str, Any] | None,
    *,
    tenant_id: str = "default",
    user_id: str = "",
) -> dict[str, Any]:
    """把 previous_outputs 中的引用对象解回整包（下游参数注入点专用）。

    非引用条目原样透传；解引用失败（过期/权限/不存在）不静默回空——
    该条目替换为 ``{"__result_ref_error__": {ref_id, reason}}`` 显式标记，
    由消费方按失败处理（如 SQLResult 解析失败 → 步骤 failed）。
    """
    resolved: dict[str, Any] = {}
    for dep_id, value in (previous_outputs or {}).items():
        if not is_result_ref(value):
            resolved[dep_id] = value
            continue
        meta = value[RESULT_REF_KEY]
        try:
            resolved[dep_id] = resolve_ref(
                str(meta.get("ref_id", "")), tenant_id, user_id)
        except (LookupError, PermissionError) as exc:
            logger.warning(
                f"[ResultRefs] previous_outputs[{dep_id}] 解引用失败: {exc}")
            resolved[dep_id] = {
                RESULT_REF_ERROR_KEY: {
                    "ref_id": str(meta.get("ref_id", "")),
                    "reason": str(exc),
                }
            }
    return resolved


# =====================================================
# Schema 映射：字段级路径取值
# =====================================================

def ref_value(
    previous_outputs: dict[str, Any] | None,
    path: str,
    *,
    tenant_id: str = "default",
    user_id: str = "",
) -> Any:
    """按 ``"step_id.field.path"`` 从前置步骤输出取字段值（Schema 映射）。

    - 对应步骤输出是引用对象时先解引用再取值；
    - 步骤不存在 / 中间层级缺失 / 取值为 None → LookupError 显式报错，
      不静默空串（调用方决定失败呈现方式）。
    """
    parts = [p.strip() for p in str(path).split(".") if p.strip()]
    if not parts:
        raise LookupError(f"字段路径为空: {path!r}")
    step_id, field_parts = parts[0], parts[1:]
    if step_id not in (previous_outputs or {}):
        raise LookupError(f"前置步骤不存在: {step_id}（path={path!r}）")
    current = previous_outputs[step_id]
    if is_result_ref(current):
        current = resolve_ref(
            str(current[RESULT_REF_KEY].get("ref_id", "")), tenant_id, user_id)
    walked = step_id
    for i, key in enumerate(field_parts):
        walked = f"{walked}.{key}"
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(key)]
            except (ValueError, IndexError) as exc:
                raise LookupError(
                    f"字段路径 {walked!r} 不可解析（列表下标无效）") from exc
        else:
            raise LookupError(f"字段路径缺失: {walked}")
    if current is None:
        raise LookupError(f"字段取值为 None: {walked}")
    return current


_PARAM_TEMPLATE_RE = None  # 惰性编译（避免模块导入期依赖 re 的微小开销争议）


def resolve_param_templates(
    params: dict,
    previous_outputs: dict[str, Any] | None,
    *,
    tenant_id: str = "default",
    user_id: str = "",
) -> dict:
    """参数模板解析：``{{step_id.field.path}}`` → 按字段路径取值。

    - 整串恰好是一个模板 → 替换为取值原类型（int/dict/list 透传）；
    - 模板嵌在更长字符串中 → 按字符串插值替换；
    - 取值 None/缺失 → LookupError 显式抛出（不静默空串），
      由调用方把失败呈现为该步骤的 invalid_param；
    - 无模板的 params 原样浅拷贝返回（默认行为零变化）。
    """
    global _PARAM_TEMPLATE_RE
    if _PARAM_TEMPLATE_RE is None:
        import re
        _PARAM_TEMPLATE_RE = re.compile(r"\{\{([^{}]+)\}\}")

    resolved: dict = {}
    changed = False
    for key, value in (params or {}).items():
        if not (isinstance(value, str) and "{{" in value):
            resolved[key] = value
            continue
        matches = list(_PARAM_TEMPLATE_RE.finditer(value))
        if not matches:
            resolved[key] = value
            continue
        changed = True
        if len(matches) == 1 and matches[0].span() == (0, len(value)):
            # 整串模板：保留取值原类型
            resolved[key] = ref_value(
                previous_outputs, matches[0].group(1).strip(),
                tenant_id=tenant_id, user_id=user_id)
        else:
            # 嵌入式模板：字符串插值
            def _sub(m: Any) -> str:
                return str(ref_value(
                    previous_outputs, m.group(1).strip(),
                    tenant_id=tenant_id, user_id=user_id))

            resolved[key] = _PARAM_TEMPLATE_RE.sub(_sub, value)
    return resolved if changed else dict(params or {})


# =====================================================
# LLM 上下文装配（关键不变式的执行点）
# =====================================================

def output_for_context(output: Any) -> Any:
    """LLM 上下文装配点用：引用对象 → 摘要文本；其余原样返回。"""
    if not is_result_ref(output):
        return output
    meta = output[RESULT_REF_KEY]
    head = (
        "（该步骤结果较大，已按任务引用存档；以下为摘要，"
        f"ref_id={meta.get('ref_id', '')}）"
    )
    return f"{head}\n{summary_of(meta.get('summary', ''))}"


def materialize_step_results_for_context(step_results: dict | None) -> dict:
    """浅拷贝 step_results 并把引用对象 output 替换为摘要文本。

    Reporter / 任何把步骤输出喂给 LLM 的组装点在入口调用一次即可，
    其余渲染路径（结构化表格 / 数据摘要 / 参考文献提取）全部基于
    摘要后的形态工作，不需要各自感知引用对象。
    """
    if not step_results:
        return dict(step_results or {})
    out: dict = {}
    for sid, sr in step_results.items():
        if isinstance(sr, dict) and is_result_ref(sr.get("output")):
            copied = dict(sr)
            copied["output"] = output_for_context(sr.get("output"))
            copied["result_ref_offloaded"] = True
            out[sid] = copied
        else:
            out[sid] = sr
    return out


# =====================================================
# 身份提取（offload 登记 / 解引用共用）
# =====================================================

def identity_from_state(state: dict | None) -> dict:
    """从图状态提取引用登记/解引用所需身份（tenant/user/请求/会话）。

    取值优先级与仓库既有惯例一致：state 平铺键 → request_context
    （dict 或对象，checkpoint 两种形态都兼容）→ ContextVar（workflow 等
    已 bind 的执行线程）→ 缺省。任何来源失败都降级为空，绝不阻断执行。
    """
    try:
        from backend.core.request_context import (
            get_tool_tenant_id, get_tool_user_id,
        )

        rc = (state or {}).get("request_context")

        def _rc_field(name: str) -> str:
            if isinstance(rc, dict):
                return str(rc.get(name, "") or "")
            return str(getattr(rc, name, "") or "")

        tenant = (
            str((state or {}).get("tenant_id") or "")
            or _rc_field("tenant_id")
            or get_tool_tenant_id()
            or "default"
        )
        user = (
            str((state or {}).get("user_id") or "")
            or _rc_field("user_id")
            or get_tool_user_id()
            or ""
        )
        session = (
            str((state or {}).get("session_id") or "")
            or _rc_field("session_id")
            or ""
        )
        # request_id：trace 权威；无 trace 回退会话标识
        try:
            from backend.observability.tracer import trace_collector
            trace = trace_collector.current()
        except Exception:  # noqa: BLE001 — 观测不可用不影响身份提取
            trace = None
        request_id = str(
            getattr(trace, "request_id", "") or getattr(trace, "id", "") or session
        )
        return {
            "request_id": request_id,
            "thread_id": session,
            "tenant_id": tenant,
            "user_id": user,
        }
    except Exception:  # noqa: BLE001 — 身份提取失败按默认租户处理
        logger.debug("result ref 身份提取失败", exc_info=True)
        return {"request_id": "", "thread_id": "", "tenant_id": "default",
                "user_id": ""}


# =====================================================
# 连接与时间（测试可替换的薄边界）
# =====================================================

def _memory_conn():
    """agent_memory 裸连接（per-op：成功 commit、异常 rollback、退出必关）。

    照 workflow/persistence_pg 的连接纪律；用例见 maybe_offload /
    resolve_ref / cleanup_expired。
    """
    from contextlib import contextmanager

    from backend.infra.db import get_memory_engine

    @contextmanager
    def _conn():
        conn = get_memory_engine().raw_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return _conn()


def _jsonb(value: Any) -> Any:
    from psycopg2.extras import Json
    return Json(value)


def _expires_at_sql():
    """过期时间：now() + 24h（DB 时钟，避免应用/DB 时钟漂移）。"""
    from psycopg2.extensions import AsIs
    return AsIs("now() + interval '24 hours'")


def _now():
    """当前 DB 无关时间比较基准（resolve 过期判断用；测试可 patch）。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


# 供测试重置 GC 节流状态
def _reset_cleanup_throttle_for_tests() -> None:
    global _last_cleanup_at
    _last_cleanup_at = 0.0
