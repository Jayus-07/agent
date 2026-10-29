"""plan_revision.py — 运行中任务的多轮自然语言计划修订（决策 #29）

目标：用户对异步任务（tasks.execute_agent 链路）的执行计划做多轮自然语言
修改：draft（LLM 起草 + 服务端强制校验）→ apply（写回 checkpoint plan 并经
既有 resume 链重新入图）。复用既有主图/Checkpointer/任务执行链，不新建
第二执行器。

语义边界（v1，诚实声明——API 与注释均显式声明，不得含糊）：
  - 只允许修改 **pending/failed** 步骤的 description/params/capability；
    修订步骤以**新 step_id**（`{sid}r{version}`）重发——step_results 的
    merge reducer 按状态进展择优（failed 不可降级回 pending），新 id 无
    旧结果即天然被 supervisor 派发（与 degradation 的 `{sid}_fallback`
    同款先例）；旧 step_id 的历史结果原样保留。
  - **success 步骤不可修改**，只可保留复用（supervisor 对非 pending 步骤
    天然跳过——结果复用语义已验证存在）或作废引用（从修订计划移除）。
    **已完成的外部写入不会被撤销、回滚或伪装撤销**——作废只是修订后的
    计划不再引用该结果，外部系统里已发生的事实不变。
  - running 步骤不可修改（并发保护）；skipped 步骤不可被 LLM 直接修订，
    但修订其 failed 上游时由服务端**机械级联**重发（不级联则修订永远
    不会传播到被跳过的下游，修订失去意义）。
  - 不允许新增步骤（新增能力 = 创建新任务）；不允许新增写操作
    （governance.operation 判定：非 READ 即写）。
  - 并发版本检查：修订基于 base_plan_version（= 该 thread 已应用的最新
    new_version，无修订时 0），不匹配 → 409；apply 原子认领 + 部分唯一
    索引兜底并发。
  - 修订后计划强制过既有 normalize_plan（授权过滤 allowed）与
    plan_depth ≤ MAX_PLAN_DEPTH 校验。

持久化通道（摸底结论）：任务路径 thread_id = `task-{task_id}` 固定，
PostgresSaver 持久化整图 state；读取 = build_task_graph().get_state()，
写回 = graph.update_state(config, {"plan": ...})（plan 通道无 reducer，
整体替换），重入图 = task_manager.resume_task（与用户输入注入同一
update_state 通道先例）。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from backend.shared.logger import logger

# 可修订步骤状态白名单（决策 #29 红线：只允许 pending/failed）
_REVISABLE_STATUSES = frozenset({"pending", "failed"})
# apply 前复核 modified 条目的状态白名单：在起草口径上放宽 skipped——
# 指向 skipped 的 modified 条目只能是起草时服务端机械级联登记的重发项
# （LLM 无法直接修订 skipped，起草已拒；apply 仅在任务静止时可用，
# 静止期不会新增 skipped，见到的 skipped 即起草时的原状）
_REAPPLY_STATUSES = _REVISABLE_STATUSES | {"skipped"}
# 摘要化输出长度上限（送 LLM 的步骤产出摘要；result_refs 摘要规则的
# 本地等价实现——大 output 只送截断摘要，不送全文）
_OUTPUT_SUMMARY_MAX_CHARS = 300


class PlanRevisionError(Exception):
    """计划修订业务错误基类（http_status 供路由映射）。"""

    http_status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class RevisionNotFound(PlanRevisionError):
    """任务/计划/修订记录不存在（含归属不符——不泄露存在性）。"""

    http_status = 404


class RevisionConflict(PlanRevisionError):
    """并发版本冲突或任务状态不允许（409）。"""

    http_status = 409


class RevisionInvalid(PlanRevisionError):
    """修订内容不合法（服务端校验拒绝，不信任模型输出）。"""

    http_status = 400


# ═══════════════════════════════════════════════════
# DB 访问（同步 psycopg2；与 discovery/service.py 同款助手）
# ═══════════════════════════════════════════════════

def _db() -> Any:
    from backend.infra.db import get_memory_engine

    return get_memory_engine().raw_connection()


def _query_all(sql: str, params: dict | None = None) -> list[dict]:
    from psycopg2.extras import RealDictCursor

    conn = _db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or {})
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _execute(sql: str, params: dict | None = None) -> int:
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            affected = cur.rowcount
        conn.commit()
        return affected
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: dict) -> dict:
    out = dict(row)
    for key in ("revised_plan", "changes"):
        raw = out.get(key)
        if isinstance(raw, str):
            try:
                out[key] = json.loads(raw)
            except Exception:
                pass
    if out.get("created_at") is not None:
        out["created_at"] = str(out["created_at"])
    if out.get("applied_at") is not None:
        out["applied_at"] = str(out["applied_at"])
    return out


# ═══════════════════════════════════════════════════
# 任务 / checkpoint 状态读取
# ═══════════════════════════════════════════════════

def _get_task_by_thread(thread_id: str) -> dict | None:
    rows = _query_all(
        "SELECT id, user_id, tenant_id, status, graph_name "
        "FROM tasks WHERE thread_id = %(t)s "
        "ORDER BY created_at DESC LIMIT 1",
        {"t": thread_id})
    return rows[0] if rows else None


def _load_graph_state(thread_id: str) -> dict:
    """读取任务线程的权威 LangGraph state（plan/step_results/request_context）。

    复用任务执行器专用图单例（同一 PostgresSaver checkpointer）；线程无
    checkpoint 历史（任务未开跑）时返回空 dict。
    """
    from backend.orchestration.checkpoint.task_executor import build_task_graph

    graph = build_task_graph()
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = graph.get_state(config)
    except Exception as exc:  # noqa: BLE001 — checkpoint 损坏按无状态处理
        logger.warning("[PlanRevision] get_state 失败 thread=%s: %s", thread_id, exc)
        return {}
    values = getattr(snapshot, "values", None) or {}
    return dict(values) if isinstance(values, dict) else {}


def _patch_task_plan(thread_id: str, revised_plan: dict,
                     step_results_patch: dict) -> None:
    """把修订计划写回 checkpoint（plan 通道无 reducer，整体替换）。

    step_results_patch 仅做优先级安全的收尾标记（pending → skipped，
    2 ≥ 0 胜出；failed 不可降级，保持原状——历史事实不被改写）。
    """
    from backend.orchestration.checkpoint.task_executor import build_task_graph

    graph = build_task_graph()
    config = {"configurable": {"thread_id": thread_id}}
    values: dict[str, Any] = {"plan": revised_plan}
    if step_results_patch:
        values["step_results"] = step_results_patch
    graph.update_state(config, values)


# ═══════════════════════════════════════════════════
# 版本账本
# ═══════════════════════════════════════════════════

def _current_plan_version(thread_id: str) -> int:
    """当前已应用版本：该 thread 最新 applied 修订的 new_version（无则 0）。"""
    rows = _query_all(
        "SELECT new_version FROM ai.plan_revisions "
        "WHERE thread_id = %(t)s AND status = 'applied' "
        "ORDER BY new_version DESC LIMIT 1",
        {"t": thread_id})
    return int(rows[0]["new_version"]) if rows else 0


# ═══════════════════════════════════════════════════
# 服务端强制校验（不信任模型输出）
# ═══════════════════════════════════════════════════

def _authorized_capabilities(state_values: dict) -> set[str]:
    """当前请求的授权可见能力集（与 Planner/Critique/执行期门同谓词）。"""
    from backend.core.product_context import filter_capabilities_for_state
    from backend.orchestration.router.manifest import effective_manifest

    ctx = state_values.get("request_context")
    state = {
        "product_id": (
            ctx.get("product_id") if isinstance(ctx, dict)
            else getattr(ctx, "product_id", None)
        ) or "internal",
        "domain": str(state_values.get("domain") or ""),
    }
    return set(filter_capabilities_for_state(
        effective_manifest().planner_visible_capability_names, state))


def _is_write_operation(capability: str, params: dict) -> bool:
    """governance.operation 判定：非 READ 即写（含 action 参数动态映射）。"""
    from backend.core.tool_governance.models import Operation
    from backend.core.tool_governance.registry import get_tool_spec

    spec = get_tool_spec(capability)
    if spec is None:
        # 无治理声明的 capability 无法证明是只读 → 按写操作处理（fail-closed）
        return True
    return spec.effective_operation(dict(params or {})) != Operation.READ


def _partition_steps(
    plan: dict, step_results: dict,
) -> dict[str, dict]:
    """步骤状态分区：{step_id: {"status": ..., "error": ..., "summary": ...}}。

    step_results 无条目 → 视为 pending（supervisor 同口径）。
    """
    partition: dict[str, dict] = {}
    nodes = plan.get("nodes", {})
    for sid, node in nodes.items():
        sr = step_results.get(sid) or {}
        status = str(sr.get("status") or "pending")
        partition[sid] = {
            "status": status,
            "capability": node.get("capability", ""),
            "description": node.get("description", ""),
            "error": str(sr.get("error") or ""),
            # 摘要化输出：只送截断摘要（不送全文/二进制/大对象）
            "summary": _summarize_output(sr.get("output")),
        }
    return partition


def _summarize_output(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, dict):
        # result_ref / summary 形态优先送摘要字段，其余截断
        for key in ("summary", "answer", "preview"):
            val = output.get(key)
            if isinstance(val, str) and val:
                return val[:_OUTPUT_SUMMARY_MAX_CHARS]
        text = json.dumps(output, ensure_ascii=False, default=str)
    else:
        text = str(output)
    if len(text) > _OUTPUT_SUMMARY_MAX_CHARS:
        text = text[:_OUTPUT_SUMMARY_MAX_CHARS] + "…（截断）"
    return text


def _dependents(edges: dict, sid: str) -> list[str]:
    return [k for k, deps in edges.items() if sid in (deps or [])]


def _validate_and_build_revised_plan(
    plan: dict,
    partition: dict[str, dict],
    llm_output: dict,
    allowed: set[str],
    new_version: int,
    instruction: str,
) -> tuple[dict, list[dict]]:
    """校验 LLM 修订输出并确定性构造修订计划。任何越界 → RevisionInvalid。

    服务端是唯一事实源：LLM 只能表达「修订哪些 pending/failed 步骤」与
    「作废哪些步骤引用」，计划的实际构造、新 id 分配、依赖改线、级联重发
    全部由本函数机械完成。
    """
    nodes = json.loads(json.dumps(plan.get("nodes", {})))
    edges = json.loads(json.dumps(plan.get("edges", {})))

    revisions = llm_output.get("revisions")
    discards = llm_output.get("discard_step_ids", [])
    if not isinstance(revisions, list) or not isinstance(discards, list):
        raise RevisionInvalid("模型输出格式不符合修订契约（需要 revisions/discard_step_ids）")

    seen_rev: set[str] = set()
    for item in revisions:
        if not isinstance(item, dict):
            raise RevisionInvalid("revisions 条目必须是对象")
        sid = str(item.get("step_id", ""))
        if not sid or sid in seen_rev:
            raise RevisionInvalid(f"修订条目 step_id 缺失或重复: {sid!r}")
        seen_rev.add(sid)
        if sid not in nodes:
            # 不允许新增步骤（新增能力 = 创建新任务）
            raise RevisionInvalid(
                f"步骤 {sid} 不在当前计划中：不支持新增步骤（新增能力请创建新任务）")
        status = partition.get(sid, {}).get("status", "pending")
        if status not in _REVISABLE_STATUSES:
            if status == "success":
                raise RevisionInvalid(
                    f"步骤 {sid} 已成功执行，结果不可修改（已完成的外部写入不会"
                    "被撤销），仅可保留复用或作废引用")
            if status == "running":
                raise RevisionInvalid(f"步骤 {sid} 正在运行，不可修改（并发保护）")
            raise RevisionInvalid(f"步骤 {sid} 状态为 {status}，仅 pending/failed 步骤可修改")

    seen_disc: set[str] = set()
    for sid in discards:
        sid = str(sid)
        if sid in seen_disc:
            raise RevisionInvalid(f"作废条目重复: {sid!r}")
        seen_disc.add(sid)
        if sid not in nodes:
            raise RevisionInvalid(f"作废步骤 {sid} 不在当前计划中")
        if sid in seen_rev:
            raise RevisionInvalid(f"步骤 {sid} 不能同时修订与作废")
        if partition.get(sid, {}).get("status") == "running":
            raise RevisionConflict(f"步骤 {sid} 正在运行，不可作废（并发保护）")

    changes: list[dict] = []
    # old_sid → new_sid 映射（依赖改线用）
    remap: dict[str, str] = {}

    # ── 1. 修订步骤：新 step_id 重发 ─────────────────────────
    for item in revisions:
        sid = str(item["step_id"])
        orig = nodes[sid]
        new_cap = str(item.get("capability") or orig.get("capability", ""))
        new_params = item.get("params") if item.get("params") is not None \
            else dict(orig.get("params") or {})
        new_desc = str(item.get("description") or orig.get("description", ""))
        if not isinstance(new_params, dict):
            raise RevisionInvalid(f"步骤 {sid} 的 params 必须是对象")

        if new_cap != orig.get("capability", ""):
            if new_cap not in allowed:
                raise RevisionInvalid(
                    f"步骤 {sid} 修订后的 capability '{new_cap}' 不在当前授权"
                    "可见清单内（越权能力被拒绝）")
            if _is_write_operation(new_cap, new_params):
                raise RevisionInvalid(
                    f"步骤 {sid} 修订为 '{new_cap}' 是写操作：不允许通过修订"
                    "引入新的写操作（新增能力请创建新任务）")
        elif item.get("params") is not None:
            # capability 未变但 params 变化：不得引入新的写操作（原计划
            # 已有的写操作保持原样，执行期 Governance/确认门照常生效）
            if (_is_write_operation(new_cap, new_params)
                    and not _is_write_operation(orig.get("capability", ""),
                                                orig.get("params") or {})):
                raise RevisionInvalid(
                    f"步骤 {sid} 的参数修订引入了写操作（原为只读），已被拒绝")

        new_id = f"{sid}r{new_version}"
        nodes.pop(sid)
        nodes[new_id] = {
            "step_id": new_id,
            "capability": new_cap,
            "description": new_desc,
            "params": new_params,
        }
        # 依赖改线：保留原依赖；下游依赖者指向新 id
        edges[new_id] = list(edges.get(sid) or [])
        edges.pop(sid, None)
        for deps in edges.values():
            while sid in (deps or []):
                deps[deps.index(sid)] = new_id
        remap[sid] = new_id
        changes.append({
            "step_id": sid, "new_step_id": new_id, "action": "modified",
            "note": str(item.get("note") or instruction)[:200],
        })

    # ── 2. 级联重发：修订步骤的 skipped 下游（机械重发，非 LLM 决策）──
    # skipped 是「上游失败被跳过」的终态，不级联则修订永远传播不到下游。
    queue = [new_id for new_id in remap.values()]
    cascaded: set[str] = set()
    while queue:
        current = queue.pop(0)
        for dep_sid in _dependents(edges, current):
            if dep_sid in cascaded:
                continue
            if partition.get(dep_sid, {}).get("status") != "skipped":
                continue
            cascaded.add(dep_sid)
            new_id = f"{dep_sid}r{new_version}"
            orig = nodes[dep_sid]
            nodes.pop(dep_sid)
            nodes[new_id] = {
                "step_id": new_id,
                "capability": orig.get("capability", ""),
                "description": orig.get("description", ""),
                "params": dict(orig.get("params") or {}),
            }
            edges[new_id] = list(edges.get(dep_sid) or [])
            edges.pop(dep_sid, None)
            for deps in edges.values():
                while dep_sid in (deps or []):
                    deps[deps.index(dep_sid)] = new_id
            remap[dep_sid] = new_id
            queue.append(new_id)
            changes.append({
                "step_id": dep_sid, "new_step_id": new_id, "action": "modified",
                "note": "上游步骤修订后级联重跑（原状态 skipped，机械级联）",
            })

    # ── 3. 作废引用：从修订计划移除（外部写入不撤销）──────────
    for sid in discards:
        sid = str(sid)
        remaining_dependents = [d for d in _dependents(edges, sid)
                                if d not in remap and d not in seen_disc]
        if remaining_dependents:
            raise RevisionInvalid(
                f"步骤 {sid} 仍有下游依赖（{','.join(sorted(remaining_dependents))}），"
                "无法作废——请一并修订下游步骤或先作废下游")
        nodes.pop(sid, None)
        edges.pop(sid, None)
        for deps in list(edges.values()):
            while sid in (deps or []):
                deps.remove(sid)
        changes.append({
            "step_id": sid, "action": "discarded",
            "note": "作废引用：修订后计划不再使用该结果；已发生的外部写入"
                    "不会被撤销或回滚",
        })

    # ── 4. kept 条目：未触动的步骤显式登记（审计口径）─────────
    touched = {c["step_id"] for c in changes}
    for sid in plan.get("nodes", {}):
        if sid not in touched:
            changes.append({"step_id": sid, "action": "kept", "note": ""})

    # 孤立 edges 清理（照 critique._auto_fix_plan 同款）
    edges = {k: v for k, v in edges.items() if k in nodes}
    for k in list(edges.keys()):
        edges[k] = [d for d in edges[k] if d in nodes]

    revised = {"nodes": nodes, "edges": edges}

    # ── 5. 既有守门：normalize_plan（授权过滤）+ plan_depth ──────
    from backend.agents.planner.plan_utils import MAX_PLAN_DEPTH, normalize_plan, plan_depth

    revised = normalize_plan(revised, allowed=allowed)
    if not revised.get("nodes"):
        raise RevisionInvalid("修订后计划为空（授权过滤后无有效步骤），拒绝落草稿")
    depth = plan_depth(revised)
    if depth > MAX_PLAN_DEPTH:
        raise RevisionInvalid(
            f"修订后计划依赖深度 {depth} 超过上限 {MAX_PLAN_DEPTH}，请简化依赖链")
    return revised, changes


# ═══════════════════════════════════════════════════
# 对外服务入口
# ═══════════════════════════════════════════════════

def draft_revision(thread_id: str, tenant_id: str, user_id: str,
                   instruction: str, base_plan_version: int) -> dict:
    """起草计划修订（LLM + 服务端强制校验），落 draft 行并返回。

    失败路径显式抛错（RevisionNotFound/Conflict/Invalid），不静默。
    """
    instruction = str(instruction or "").strip()
    if not instruction:
        raise RevisionInvalid("修改指令不能为空")

    task = _get_task_by_thread(thread_id)
    if task is None or str(task.get("user_id") or "") != str(user_id):
        raise RevisionNotFound("任务不存在")
    if str(task.get("tenant_id") or "default") != str(tenant_id or "default"):
        # 租户不符按 404 处理（不泄露存在性）
        raise RevisionNotFound("任务不存在")

    task_status = str(task.get("status") or "")
    if task_status in ("SUCCESS", "CANCELLED"):
        raise RevisionConflict(f"任务已终态（{task_status}），无需修订")

    # 并发版本检查
    current_version = _current_plan_version(thread_id)
    if current_version != int(base_plan_version):
        raise RevisionConflict(
            f"计划版本已变化（当前 v{current_version}，请求基于 "
            f"v{base_plan_version}），请刷新后基于最新版本重新起草")

    state_values = _load_graph_state(thread_id)
    plan = state_values.get("plan") or {}
    if not (plan.get("nodes") or {}):
        raise RevisionNotFound("计划尚未生成或无可修订对象（任务未进入规划阶段）")
    step_results = state_values.get("step_results") or {}
    partition = _partition_steps(plan, step_results)

    revisable = [sid for sid, p in partition.items()
                 if p["status"] in _REVISABLE_STATUSES]
    if not revisable:
        raise RevisionInvalid(
            "当前计划没有 pending/failed 步骤可修订"
            "（全部步骤已成功/跳过/运行中）")

    allowed = _authorized_capabilities(state_values)

    # ── LLM 起草（输入 = 当前计划 + 状态分区 + 指令；产出摘要化）──
    from backend.infra.llm import llm
    from backend.prompts.service import prompt_service

    capabilities_schema = _capabilities_schema_text(allowed)
    system_prompt = prompt_service.render_sync(
        "plan.revision",
        capabilities_schema=capabilities_schema,
    ).text
    plan_view_json = json.dumps(
        {"nodes": partition_view(plan), "edges": plan.get("edges", {})},
        ensure_ascii=False, indent=2)
    partition_json = json.dumps(partition, ensure_ascii=False, indent=2)
    user_message = (
        f"用户修改指令：{instruction}\n\n"
        f"当前计划（nodes/edges）：\n{plan_view_json}\n\n"
        f"各步骤执行状态（含成功步骤的产出摘要）：\n{partition_json}\n\n"
        "请输出修订 JSON。"
    )
    try:
        response = llm.invoke([
            ("system", system_prompt),
            ("human", user_message),
        ])
        content = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:  # noqa: BLE001 — LLM 失败显式报错，不静默
        raise PlanRevisionError(f"修订起草失败（模型调用异常）: {exc}") from exc

    from backend.agents.planner.plan_utils import extract_json

    llm_output = extract_json(content, source="plan.revision")
    if not isinstance(llm_output, dict) or not llm_output:
        raise RevisionInvalid("模型未返回可解析的修订 JSON，请调整指令后重试")

    new_version = current_version + 1
    revised_plan, changes = _validate_and_build_revised_plan(
        plan, partition, llm_output, allowed, new_version, instruction)

    revision_id = f"planrev_{uuid.uuid4().hex[:16]}"
    _execute(
        """
        INSERT INTO ai.plan_revisions
            (id, thread_id, tenant_id, user_id, base_plan_version,
             new_version, instruction, revised_plan, changes, status)
        VALUES (%(id)s, %(t)s, %(tenant)s, %(u)s, %(base)s,
                %(nv)s, %(inst)s, %(plan)s, %(changes)s, 'draft')
        """,
        {
            "id": revision_id, "t": thread_id, "tenant": tenant_id, "u": user_id,
            "base": int(base_plan_version), "nv": new_version,
            "inst": instruction,
            "plan": json.dumps(revised_plan, ensure_ascii=False, default=str),
            "changes": json.dumps(changes, ensure_ascii=False, default=str),
        })
    logger.info(
        "[PlanRevision] draft %s thread=%s base=v%s new=v%s changes=%d",
        revision_id, thread_id, base_plan_version, new_version, len(changes))
    return {
        "id": revision_id,
        "thread_id": thread_id,
        "task_id": task.get("id"),
        "base_plan_version": int(base_plan_version),
        "new_version": new_version,
        "status": "draft",
        "instruction": instruction,
        "revised_plan": revised_plan,
        "changes": changes,
    }


def partition_view(plan: dict) -> dict:
    """计划节点视图（送 LLM 的轻量形态：step_id/capability/description/params）。"""
    return {
        sid: {
            "step_id": sid,
            "capability": node.get("capability", ""),
            "description": node.get("description", ""),
            "params": node.get("params", {}),
        }
        for sid, node in (plan.get("nodes") or {}).items()
    }


def _capabilities_schema_text(allowed: set[str]) -> str:
    """修订可选能力的 schema 文本（与 critique 同源渲染，授权集内）。"""
    from backend.orchestration.capability_registry import tool_registry

    return tool_registry.get_capabilities_schema_text(allowed=allowed)


def apply_revision(revision_id: str, tenant_id: str, user_id: str,
                   base_plan_version: int) -> dict:
    """应用修订：版本复核 → 原子标记 applied → 写回 checkpoint → 重新入图。

    只允许在任务静止（PAUSED/WAITING_USER/FAILED）时应用；RUNNING/PENDING
    → 409（请先暂停）。写回用既有 update_state 通道，重入图用既有
    resume 链（PAUSED/WAITING_USER 正常恢复；FAILED 走显式 allow_failed
    通道并标记 dispatch_type=plan_revision 供观测）。
    """
    rows = _query_all(
        "SELECT * FROM ai.plan_revisions WHERE id = %(id)s",
        {"id": revision_id})
    if not rows:
        raise RevisionNotFound("修订记录不存在")
    revision = _row_to_dict(rows[0])
    if (str(revision.get("tenant_id") or "default") != str(tenant_id or "default")
            or str(revision.get("user_id") or "") != str(user_id)):
        raise RevisionNotFound("修订记录不存在")

    if revision.get("status") != "draft":
        raise RevisionConflict(
            f"修订当前状态为 {revision.get('status')}，仅 draft 可应用")

    thread_id = str(revision.get("thread_id") or "")
    task = _get_task_by_thread(thread_id)
    if task is None or str(task.get("user_id") or "") != str(user_id):
        raise RevisionNotFound("任务不存在")

    task_status = str(task.get("status") or "")
    if task_status in ("PENDING", "RUNNING"):
        raise RevisionConflict(
            f"任务当前为 {task_status}，请先暂停任务（等待静止）后再应用修订")
    if task_status in ("SUCCESS", "CANCELLED"):
        raise RevisionConflict(f"任务已终态（{task_status}），无法应用修订")

    # 版本复核（起草后计划可能已被其他修订推进）
    current_version = _current_plan_version(thread_id)
    if current_version != int(revision.get("base_plan_version", -1)):
        raise RevisionConflict(
            f"计划版本已变化（当前 v{current_version}，本修订基于 "
            f"v{revision.get('base_plan_version')}），请重新起草")

    if int(base_plan_version) != int(revision.get("base_plan_version", -1)):
        raise RevisionConflict("请求中的 base_plan_version 与修订记录不一致")

    # ── 应用前按当前 state 复核步骤状态（起草后状态可能已变化）────
    state_values = _load_graph_state(thread_id)
    step_results = state_values.get("step_results") or {}
    changes = revision.get("changes") or []
    for change in changes:
        if not isinstance(change, dict):
            continue
        sid = str(change.get("step_id") or "")
        action = str(change.get("action") or "")
        status = str((step_results.get(sid) or {}).get("status") or "pending")
        if action == "modified" and status not in _REAPPLY_STATUSES:
            raise RevisionConflict(
                f"步骤 {sid} 状态已变为 {status}，与本修订不一致，请重新起草")
        if action == "discarded" and status == "running":
            raise RevisionConflict(
                f"步骤 {sid} 正在运行，无法作废（并发保护），请重新起草")

    revised_plan = revision.get("revised_plan") or {}
    if not (revised_plan.get("nodes") or {}):
        raise RevisionInvalid("修订记录缺少有效计划，无法应用")

    # 原子认领版本（部分唯一索引兜底并发 apply；先占版本再写状态，
    # 失败时版本已消耗——显式报错提示手动恢复，不静默不回滚伪装）
    claimed = _execute(
        """
        UPDATE ai.plan_revisions
        SET status = 'applied', applied_at = now()
        WHERE id = %(id)s AND status = 'draft'
        """,
        {"id": revision_id})
    if not claimed:
        raise RevisionConflict("修订已被并发请求应用")
    try:
        _apply_revisions_to_graph(
            thread_id, revised_plan, changes, step_results)
    except Exception as exc:
        logger.error(
            "[PlanRevision] %s 已标记 applied 但写回 checkpoint 失败: %s",
            revision_id, exc)
        raise PlanRevisionError(
            "修订已登记应用，但计划写回失败；请通过任务 resume 接口人工恢复"
            "并检查任务状态") from exc

    # ── 重新入图（既有执行链）───────────────────────────────
    task_id = str(task.get("id") or "")
    try:
        from backend.tasks.task_manager import resume_task

        resume_task(
            task_id,
            allow_failed=(task_status == "FAILED"),
            dispatch_type="plan_revision",
        )
    except ValueError as exc:
        raise RevisionConflict(f"任务状态已变化，重新入队被拒绝: {exc}") from exc

    logger.info(
        "[PlanRevision] applied %s thread=%s task=%s new_version=v%s",
        revision_id, thread_id, task_id, revision.get("new_version"))
    return {
        "id": revision_id,
        "thread_id": thread_id,
        "task_id": task_id,
        "status": "applied",
        "base_plan_version": int(revision.get("base_plan_version", 0)),
        "new_version": int(revision.get("new_version", 0)),
        "changes": changes,
        "task_status": "PENDING",
    }


def _apply_revisions_to_graph(thread_id: str, revised_plan: dict,
                              changes: list[dict], step_results: dict) -> None:
    """把修订计划写回 checkpoint；对被替换/作废的 pending 条目补 skipped 标记。

    step_results 的 merge reducer 按状态进展择优：pending(0) → skipped(2)
    可胜出（诚实标记「被修订替换/作废」）；failed(3)/success(4) 不可降级，
    保持原状（历史事实不被改写）。
    """
    patch: dict[str, dict] = {}
    for change in changes:
        if not isinstance(change, dict):
            continue
        if str(change.get("action")) not in ("modified", "discarded"):
            continue
        sid = str(change.get("step_id") or "")
        sr = step_results.get(sid) or {}
        if str(sr.get("status") or "pending") == "pending":
            patch[sid] = {
                **(sr if isinstance(sr, dict) else {}),
                "status": "skipped",
                "error": "被计划修订替换/作废（引用作废，外部写入未撤销）",
                "finished_at": 0,
            }
    _patch_task_plan(thread_id, revised_plan, patch)


def list_revisions(thread_id: str, tenant_id: str, user_id: str,
                   limit: int = 50) -> list[dict]:
    """某任务的修订历史（归属校验：非本人/他租户 → 空列表，不泄露）。"""
    task = _get_task_by_thread(thread_id)
    if task is None or str(task.get("user_id") or "") != str(user_id) \
            or str(task.get("tenant_id") or "default") != str(tenant_id or "default"):
        return []
    rows = _query_all(
        "SELECT id, thread_id, tenant_id, user_id, base_plan_version, "
        "new_version, instruction, revised_plan, changes, status, "
        "created_at, applied_at "
        "FROM ai.plan_revisions WHERE thread_id = %(t)s "
        "ORDER BY created_at DESC LIMIT %(lim)s",
        {"t": thread_id, "lim": max(1, min(int(limit), 100))})
    result = []
    for row in rows:
        item = _row_to_dict(row)
        # 列表不回传完整计划（体积），详情按需另查
        item.pop("revised_plan", None)
        result.append(item)
    return result
