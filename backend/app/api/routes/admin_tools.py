"""Tool 治理统计 API（M2 / 台账 D2）

GET /admin/tools        — 34 Tool 清单（契约 hash + 归属，复用 M1 lock 派生）
GET /admin/tools/stats  — 运行统计聚合（总数/成功/失败/成功率/Top 失败/Top 错误类）

为什么存在：``agent_tool_*`` 指标齐全但管理端无聚合视图，管理员答不了
「哪个 Tool 在失败、为什么」。``skill_failure_total`` 此前是死指标
（定义零埋点），已随本债补埋（skills/base 失败出口）。

数据源：**进程内 Prometheus REGISTRY 直读**（prometheus_client 单例），
不依赖外部 Prometheus 可达性——本机部署零额外依赖即可运行；容器多副本
场景由 Prometheus 抓取侧聚合（本端点=单进程视图，语义在响应中注明）。
错误分类口径 = M3 统一七分类（observability.error_taxonomy）。
权限：仅管理员（require_admin_user）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from backend.app.api.deps import require_admin_user

router = APIRouter(prefix="/admin/tools", tags=["Admin-Tools"])


def _collect_samples(*metric_names: str) -> dict[str, list[dict]]:
    """从进程内 REGISTRY 抽取目标指标的样本（labels+value），软失败返回空。"""
    wanted = set(metric_names)
    found: dict[str, list[dict]] = {}
    try:
        from prometheus_client import REGISTRY

        for metric in REGISTRY.collect():
            if metric.name not in wanted:
                continue
            found.setdefault(metric.name, [])
            for sample in metric.samples:
                if sample.name.endswith("_created"):  # Counter 附带的 _created 序列
                    continue
                found[metric.name].append({
                    "labels": dict(sample.labels),
                    "value": sample.value,
                })
    except Exception:
        pass
    return found


def _aggregate_tool_stats() -> dict[str, Any]:
    """聚合 agent_tool_calls_total / error_class / timeout 指标为管理端视图。"""
    samples = _collect_samples(
        "agent_tool_calls_total",
        "agent_tool_error_class_total",
        "agent_tool_timeout_total",
        "skill_failure_total",
    )
    calls = samples.get("agent_tool_calls_total", [])
    error_classes = samples.get("agent_tool_error_class_total", [])

    tools: dict[str, dict] = {}
    for s in calls:
        name = s["labels"].get("tool", "?")
        status = s["labels"].get("status", "?")
        entry = tools.setdefault(
            name, {"tool": name, "domain": s["labels"].get("domain", ""),
                   "calls": 0.0, "success": 0.0, "failures": 0.0})
        entry["calls"] += s["value"]
        if status == "success":
            entry["success"] += s["value"]
        else:
            entry["failures"] += s["value"]

    for s in error_classes:
        name = s["labels"].get("tool", "?")
        entry = tools.setdefault(name, {"tool": name, "domain": "", "calls": 0.0,
                                        "success": 0.0, "failures": 0.0})
        entry.setdefault("error_classes", {})
        cls = s["labels"].get("error_class", "unknown")
        entry["error_classes"][cls] = entry["error_classes"].get(cls, 0.0) + s["value"]

    for entry in tools.values():
        entry["success_rate"] = (
            round(entry["success"] / entry["calls"], 4) if entry["calls"] else None)

    ranked = sorted(tools.values(), key=lambda e: (-e["failures"], -e["calls"]))
    top_failed = [
        {"tool": e["tool"], "failures": e["failures"], "calls": e["calls"],
         "error_classes": e.get("error_classes", {})}
        for e in ranked if e["failures"] > 0
    ][:10]

    error_totals: dict[str, float] = {}
    for s in error_classes:
        cls = s["labels"].get("error_class", "unknown")
        error_totals[cls] = error_totals.get(cls, 0.0) + s["value"]
    top_error_classes = sorted(error_totals.items(), key=lambda kv: -kv[1])[:8]

    skill_failures = {}
    for s in samples.get("skill_failure_total", []):
        key = (s["labels"].get("skill", "?"), s["labels"].get("error_type", "unknown"))
        skill_failures[key] = skill_failures.get(key, 0.0) + s["value"]

    total_calls = sum(e["calls"] for e in tools.values())
    total_success = sum(e["success"] for e in tools.values())
    return {
        "scope": "process",  # 单进程 REGISTRY 视图；多副本聚合走 Prometheus
        "totals": {
            "tools_seen": len(tools),
            "calls": total_calls,
            "success": total_success,
            "failures": total_calls - total_success,
            "success_rate": round(total_success / total_calls, 4) if total_calls else None,
        },
        "top_failed_tools": top_failed,
        "top_error_classes": [
            {"error_class": cls, "count": n} for cls, n in top_error_classes
        ],
        "skill_failures": [
            {"skill": k[0], "error_type": k[1], "count": v}
            for k, v in sorted(skill_failures.items(), key=lambda kv: -kv[1])
        ],
        "tools": sorted(tools.values(), key=lambda e: e["tool"]),
    }


@router.get("/stats")
async def tool_stats(request: Request):
    """Tool 运行统计聚合（进程内 Prometheus 直读，M3 七分类口径）。"""
    await require_admin_user(request)
    return _aggregate_tool_stats()


@router.get("")
async def tool_inventory(request: Request):
    """Tool 治理清单：lock 契约（hash/归属/output_type）× 运行统计合并视图。"""
    await require_admin_user(request)
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    lock = {}
    lock_error = ""
    lock_path = repo_root / "backend" / "tool_contracts.lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8")).get("tools", {})
    except (OSError, ValueError) as e:
        lock_error = f"lock 读取失败: {e}"

    stats = {t["tool"]: t for t in _aggregate_tool_stats()["tools"]}
    inventory = []
    for name, entry in sorted(lock.items()):
        run = stats.get(name, {})
        inventory.append({
            "name": name,
            "module": entry.get("module", ""),
            "capabilities": entry.get("capabilities", []),
            "output_types": entry.get("output_types", {}),
            "content_hash": entry.get("content_hash", ""),
            "description_hash": entry.get("description_hash", ""),
            "runtime": {k: run.get(k) for k in
                        ("calls", "success", "failures", "success_rate", "error_classes")},
        })
    return {
        "count": len(inventory),
        "lock_git_sha": json.loads(lock_path.read_text(encoding="utf-8"))
        .get("git_sha", "") if not lock_error else "",
        "lock_error": lock_error,
        "tools": inventory,
    }


__all__ = ["router"]
