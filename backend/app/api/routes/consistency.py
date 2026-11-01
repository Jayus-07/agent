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


def _check_dynamic_registry_sync(
    issues: list[str], counts: dict,
) -> None:
    """动态治理链对账的同步部分：快照 vs bundle 引用。"""
    from backend.infra.bundle_registry import store as bundle_store
    from backend.infra.mcp_registry import store as mcp_store

    snap = mcp_store.snapshot()
    counts["runtime_tools"] = len(snap.tools)
    counts["derive_errors"] = len(snap.errors)
    for capability, reason in snap.errors[:10]:
        issues.append(f"派生失败 {capability}: {reason}")

    # bundle 引用对账（快照未加载时跳过，不误报）
    bsnap = bundle_store.snapshot()
    counts["bundles"] = len(bsnap.bundles)
    if bsnap.loaded:
        known = set(snap.tools) | {
            c.name for c in _manifest_capabilities()}
        for bid, caps in bsnap.items.items():
            for cap in caps:
                if cap.startswith("mcp.") and cap not in known:
                    issues.append(
                        f"bundle {bsnap.bundles.get(bid, {}).get('name', bid)} "
                        f"引用未生效的动态能力: {cap}")
        counts["bundle_grants"] = len(bsnap.grants)


async def _check_dynamic_registry_db(
    issues: list[str], counts: dict,
) -> None:
    """动态治理链对账的 DB 部分：凭据引用 + invocation 合法性。"""
    from sqlalchemy import text

    from backend.infra.mcp_registry import specs as spec_lib
    from backend.memory.database import get_session

    async for session in get_session():
        rows = (await session.execute(text(
            "SELECT c.name, c.auth_type, "
            "k.connection_id AS has_cred "
            "FROM ai.mcp_connections c "
            "LEFT JOIN ai.mcp_connection_credentials k "
            "ON k.connection_id = c.id WHERE c.enabled"
        ))).mappings().all()
        counts["connections"] = len(rows)
        for r in rows:
            if r["auth_type"] == "static_bearer" and not r["has_cred"]:
                issues.append(
                    f"连接 {r['name']} 声明 Bearer 凭据但密文行缺失")
        inv_rows = (await session.execute(text(
            "SELECT capability_slug, invocation FROM ai.mcp_tools "
            "WHERE status IN ('approved', 'pending')"
        ))).mappings().all()
        # invocation 合法性走统一校验器（fail-fast 语义复用）
        bad = 0
        for r in inv_rows:
            try:
                spec_lib.validate_invocation(
                    dict(r["invocation"] or {}), None)
            except spec_lib.SpecDeriveError as exc:
                bad += 1
                if len(issues) < 20:
                    issues.append(
                        f"工具 {r['capability_slug']} 调用定义非法: {exc}")
        counts["invocation_invalid"] = bad
        break


async def _check_dynamic_registry() -> dict:
    """动态工具治理链对账（Batch 3）：ai.mcp_* 事实源 vs 运行时快照 vs
    能力包引用 vs 凭据引用。

    检查项：
      - 派生失败（derive error）的工具：审核配置或调用定义非法；
      - bundle 成员引用未生效的动态 capability（悬空授权）；
      - 连接声明 Bearer 凭据但密文行缺失；
      - invocation 非法（旧数据未过新校验）。
    """
    issues: list[str] = []
    counts: dict = {}
    try:
        # 对账前刷新运行时快照：快照是进程内缓存，别的进程（并行测试
        # suite / 其他 worker）写入的连接与 bundle 不刷新就会把活数据
        # 误判成悬空授权（v22 run 41344790496 诊断断言实证：bundle
        # 补货分析包 三工具全报「未生效」，实为并行 suite 用例窗口内的
        # 正常数据）。失败回落进程内快照（外层 except 兜底）。
        from backend.infra.bundle_registry import store as bundle_store
        from backend.infra.mcp_registry import store as mcp_store

        try:
            await mcp_store.refresh()
        except Exception as refresh_err:  # noqa: BLE001
            issues.append(f"动态注册表快照刷新失败（按进程内快照对账）: {refresh_err}")
        try:
            await bundle_store.refresh()
        except Exception as refresh_err:  # noqa: BLE001
            issues.append(f"能力包快照刷新失败（按进程内快照对账）: {refresh_err}")
        _check_dynamic_registry_sync(issues, counts)
        await _check_dynamic_registry_db(issues, counts)
    except Exception as e:  # noqa: BLE001 — 对账失败不炸整份报告
        issues.append(f"动态注册表对账异常: {e}")
    return _section("dynamic_registry", not issues, counts, issues)


def _manifest_capabilities():
    from backend.orchestration.router.manifest import effective_manifest

    return effective_manifest().capabilities


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
    dynamic_section = await _check_dynamic_registry()
    sections.append(dynamic_section)
    failed = [s["name"] for s in sections if s["status"] == "FAIL"]
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "overall": "PASS" if not failed else "FAIL",
        "failed_sections": failed,
        "sections": sections,
    }


__all__ = ["router"]
