"""Capability 层只读总览 API（B13 管理端 /skills 页数据源）

GET /capabilities  列出 capability 路由清单 + workflow + Skill 注册情况

只读原则：capabilities.yaml 是路由清单的唯一事实源（manifest.py fail-fast
校验 + test_registry_consistency 双向守护），Skill/Tool/MCP 注册表在代码。
本接口只做汇总展示 + 「声明 vs 注册」对账结果，不提供任何写路径。

对账字段 registered：manifest 声明了但 Skill 注册表没有 = False（漂移信号，
正常应恒为 True——不一致会被 test_registry_consistency 在 CI 拦下）。
"""
from fastapi import APIRouter

router = APIRouter(prefix="/capabilities", tags=["Capabilities"])


@router.get("")
async def list_capabilities():
    """列出能力路由清单（capabilities.yaml）与注册对账结果。

    返回:
      count           — capability 总数（含 routed:false 的内部能力）
      capabilities    — [{name, skill, routed, rule_keywords, examples, reason, registered}]
      workflows       — [{name, examples}]（manifest 声明的 workflow，examples 供向量路由）
      skills          — [{name, description, capabilities}]（Skill 注册表现状）
      routed_count    — 参与用户问题路由的 capability 数
    """
    # 惰性导入：skills 注册表会连带 import 全部 Skill 及其 Tool
    from backend.orchestration.router.manifest import load_manifest
    from backend.skills import registry as skill_registry

    manifest = load_manifest()
    registered_map = skill_registry.list_capabilities()  # capability → skill name

    capabilities = [
        {
            "name": c.name,
            "skill": c.skill,
            "routed": c.routed,
            "rule_keywords": list(c.rule_keywords),
            "examples": list(c.examples),
            "reason": c.reason,
            "registered": c.name in registered_map,
        }
        for c in manifest.capabilities
    ]
    workflows = [
        {"name": w.name, "examples": list(w.examples)}
        for w in manifest.workflows
    ]
    skills = []
    for name, caps in skill_registry.list_skills().items():
        inst = skill_registry.get_skill(name)
        skills.append({
            "name": name,
            "description": getattr(inst, "description", "") or "",
            "capabilities": list(caps),
        })

    return {
        "count": len(capabilities),
        "routed_count": sum(1 for c in capabilities if c["routed"]),
        "capabilities": capabilities,
        "workflows": workflows,
        "skills": skills,
    }


__all__ = ["router"]
