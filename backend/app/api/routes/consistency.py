"""资产一致性报告 API（M6 / 台账 D6）

GET /consistency/report — 平台六类资产 + 契约 lock 的单页对账矩阵。

为什么存在：对账数据源全部已有（/api/agents、/api/capabilities、
/api/agents/tools、一致性测试），但散在三处且无 CI——管理员要拼多个
页面才能回答「代码真实值与管理端是否一致」。本端点把全部派生逻辑聚
合为八行 PASS/FAIL 矩阵，供管理端「资产一致性中心」页与巡检脚本消费。

只读原则（G2）：全部数值从代码权威源实时派生（builder/registry/yaml/
AST 扫描/lock 比对），禁止人工维护数字，无任何写路径。
权限：仅管理员（require_admin_user，同 /api/approvals 口径）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request

from backend.app.api.deps import require_admin_user

router = APIRouter(prefix="/consistency", tags=["Consistency"])

REPO_ROOT = Path(__file__).resolve().parents[4]  # backend/app/api/routes → 仓库根
LOCK_PATH = REPO_ROOT / "backend" / "tool_contracts.lock.json"


def _section(name: str, status: bool, counts: dict, issues: list[str]) -> dict:
    return {
        "name": name,
        "status": "PASS" if status else "FAIL",
        "counts": counts,
        "issues": issues,
    }


def _check_agents() -> dict:
    from backend.orchestration.capability_registry import tool_registry
    from backend.orchestration.domain_registry import domain_graph_registry
    from backend.skills import registry as skill_registry

    skill_nodes = tool_registry.get_skill_nodes()
    skills = skill_registry.list_skills()
    missing_nodes = sorted(set(skills) - set(n[: -len("_skill")] for n in skill_nodes))
    issues = [f"Skill 无图节点: {missing_nodes}"] if missing_nodes else []
    counts = {
        "skill_nodes": len(skill_nodes),
        "skills": len(skills),
        "domain_graphs": len(domain_graph_registry.get_all()),
    }
    return _section("agents", not issues, counts, issues)


def _check_skills_and_capabilities() -> tuple[dict, dict]:
    from backend.orchestration.router.manifest import load_manifest
    from backend.skills import registry as skill_registry

    manifest = load_manifest()
    registered_caps = skill_registry.list_capabilities()
    declared_caps = {c.name for c in manifest.capabilities}
    declared_skills = {c.skill for c in manifest.capabilities}

    unregistered = sorted(declared_caps - set(registered_caps))          # 声明未注册
    orphan_caps = sorted(set(registered_caps) - declared_caps)           # 注册未声明
    orphan_skills = sorted(set(skill_registry.list_skills()) - declared_skills)

    skill_issues = ([f"Skill 注册但 manifest 无 capability: {orphan_skills}"] if orphan_skills else [])
    cap_issues = (
        ([f"manifest 声明未注册（漂移）: {unregistered}"] if unregistered else [])
        + ([f"注册但 manifest 未声明: {orphan_caps}"] if orphan_caps else [])
    )
    skills_section = _section(
        "skills", not skill_issues,
        {"skills": len(skill_registry.list_skills()),
         "declared_skills": len(declared_skills)},
        skill_issues,
    )
    caps_section = _section(
        "capabilities", not cap_issues,
        {"capabilities": len(declared_caps),
         "routed": sum(1 for c in manifest.capabilities if c.routed),
         "internal": sum(1 for c in manifest.capabilities if not c.routed)},
        cap_issues,
    )
    return skills_section, caps_section


def _check_tools() -> dict:
    from backend.tools.tool_registry import scan_repo_declared_tools, tool_registry

    declared = scan_repo_declared_tools(REPO_ROOT)
    loaded = set(tool_registry.tool_names)
    declared_names = set(declared)
    issues = []
    if duplicates := tool_registry.check_duplicates():
        issues.append(f"重复定义: {list(duplicates)}")
    if not_loaded := sorted(declared_names - loaded):
        issues.append(f"@tool 未加载: {not_loaded}")
    if phantom := sorted(loaded - declared_names):
        issues.append(f"幽灵注册: {phantom}")
    return _section(
        "tools", not issues,
        {"loaded": len(loaded), "declared": len(declared_names),
         "duplicates": len(duplicates or {}), "not_loaded": len(not_loaded or []),
         "phantom": len(phantom or [])},
        issues,
    )


def _check_workflows() -> dict:
    from backend.orchestration.router.manifest import load_manifest
    from backend.orchestration.workflows import register_all

    registered = set(register_all())  # 幂等，重复调用安全
    declared = {w.name for w in load_manifest().workflows}
    issues = []
    if missing := sorted(declared - registered):
        issues.append(f"manifest 声明未注册（向量路由失明）: {missing}")
    if extra := sorted(registered - declared):
        issues.append(f"注册但 manifest 未声明: {extra}")
    return _section("workflows", not issues,
                    {"workflows": len(declared)}, issues)


def _check_mcp() -> dict:
    issues: list[str] = []
    servers: list = []
    tools: list = []
    try:
        from mcp_servers.manager import manager

        servers = manager.list_servers()
        tools = manager.discover()
    except Exception as e:  # MCP 不可用不应让整份报告 500
        issues.append(f"MCP manager 异常: {e}")
    return _section("mcp", not issues,
                    {"servers": len(servers), "tools": len(tools)}, issues)


def _check_tool_contract_lock() -> dict:
    from backend.scripts.gen_tool_contract_lock import classify_lock_diff, derive_snapshot

    if not LOCK_PATH.exists():
        return _section("tool_contract_lock", False, {},
                        ["lock 文件缺失：先运行 gen_tool_contract_lock 并提交"])
    try:
        existing = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        report = classify_lock_diff(existing.get("tools", {}), derive_snapshot()["tools"])
    except (ValueError, OSError) as e:
        return _section("tool_contract_lock", False, {}, [f"lock 解析失败: {e}"])
    issues = [
        f"[{item['classification']}] {item['tool']}: "
        + ", ".join(c["kind"] for c in item["changes"])
        for item in report["changed_tools"]
    ]
    return _section(
        "tool_contract_lock", report["classification"] == "IN_SYNC",
        {"tool_count": len(existing.get("tools", {})),
         "breaking": report["summary"]["BREAKING"],
         "degraded": report["summary"]["DEGRADED"],
         "compatible": report["summary"]["COMPATIBLE"]},
        issues,
    )


@router.get("/report")
async def consistency_report(request: Request):
    """平台资产一致性单页报告（全部实时派生，只读）。"""
    await require_admin_user(request)

    skills_section, caps_section = _check_skills_and_capabilities()
    sections = [
        _check_agents(),
        skills_section,
        caps_section,
        _check_tools(),
        _check_workflows(),
        _check_mcp(),
        _check_tool_contract_lock(),
    ]
    failed = [s["name"] for s in sections if s["status"] == "FAIL"]
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "overall": "PASS" if not failed else "FAIL",
        "failed_sections": failed,
        "sections": sections,
    }


__all__ = ["router"]
