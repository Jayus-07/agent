"""四层设计规范守护测试（2026-09-16 归一）

对应 ``docs/2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md``。
每条断言都对应一次真实发生过的偏差，不是形式主义：

  1. Tool 层完整性：所有 @tool 装饰的函数必须已注册进 tool_registry。
     病根：``map_lookup_tool`` 定义在 skills/map/skill.py 且从未注册
     （34 个里唯一漏注册），工具从注册表里消失无人发现。
  2. Tool 层归属：@tool 不得出现在 backend/skills/ 下（Skill 不定义 Tool）。
     同一个病根的结构面：Tool 出界到 Skill 层。
  3. Skill 节点自注册：每个 Skill 实例都必须在 tool_registry 里有
     ``<name>_skill`` 节点。病根：data_collection / business_analysis 两个
     Skill 的节点注册写在 skills/registry.py 集中处，其余 10 个写在各包
     __init__.py，同一件事两种写法。
  4. workflow 声明 ↔ 注册双向对齐：capabilities.yaml 的 workflows 段与
     register_all() 的注册清单必须一致。病根：selection_decision 已注册却
     不在 manifest（向量路由看不见它）；MarketResearch 在 manifest 里却没被
     评测侧的注册列表覆盖。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"


def _scanner():
    """唯一判据来源：``backend/tools/tool_registry.py``（AST 扫描）。

    刻意不在这里自己写一份 AST 遍历 —— 两套扫描逻辑会漂移，
    而「同一件事两份实现」正是本文件要防的病根。质量脚本
    ``scripts/tool_quality_check.py`` 用的是同一份实现。
    """
    from backend.tools.tool_registry import scan_decorated_tools, scan_repo_declared_tools

    return scan_decorated_tools, scan_repo_declared_tools


# ==================== 1. Tool 层完整性 ====================

class TestToolLayerCompleteness:
    def test_every_decorated_tool_is_registered(self):
        """每个 @tool 装饰的函数都必须在 tool_registry 里（防漏注册）。"""
        import backend.tools  # noqa: F401  触发 tools/__init__ 导入链
        import backend.skills  # noqa: F401  触发 skills/map → tools/map 导入链
        from backend.tools.tool_registry import tool_registry

        _, scan_repo_declared_tools = _scanner()
        declared = scan_repo_declared_tools(REPO_ROOT)

        registered = set(tool_registry.tool_names)
        missing = sorted(set(declared) - registered)
        assert not missing, (
            "以下 @tool 已定义但未注册（注册缺失 → 工具从注册表里静默消失）：\n"
            + "\n".join(f"  - {n}  @ {', '.join(declared[n])}" for n in missing)
        )

    def test_registry_has_no_phantom_tools(self):
        """反向：注册表里的名字都应能在源码里找到对应 @tool 定义。"""
        import backend.tools  # noqa: F401
        import backend.skills  # noqa: F401
        from backend.tools.tool_registry import tool_registry

        _, scan_repo_declared_tools = _scanner()
        declared = set(scan_repo_declared_tools(REPO_ROOT))
        extra = sorted(set(tool_registry.tool_names) - declared)
        assert not extra, f"注册表存在源码中找不到 @tool 定义的名字: {extra}"


# ==================== 2. Tool 层归属 ====================

class TestToolLayerOwnership:
    def test_no_tool_defined_under_skills(self):
        """@tool 只能定义在 backend/tools/ 下 —— Skill 只声明 capability。

        聚合 Tool（如 map_lookup）同样属于 Tool 层：它组合原子 Tool，
        不是 Skill 的私有实现。
        """
        scan_decorated_tools, _ = _scanner()
        offenders = [
            f"  - {py.relative_to(REPO_ROOT).as_posix()}:{lineno}  {fn}"
            for py, fn, lineno in scan_decorated_tools(BACKEND / "skills")
        ]
        assert not offenders, (
            "backend/skills/ 下不允许定义 @tool（应移到 backend/tools/ 并注册）：\n"
            + "\n".join(offenders)
        )


# ==================== 3. Skill 节点自注册 ====================

class TestSkillNodeSelfRegistration:
    def test_every_skill_has_registered_graph_node(self):
        """每个 Skill 实例都要有 <name>_skill 图节点（否则主图无节点可布线）。"""
        import backend.skills  # noqa: F401
        from backend.orchestration.capability_registry import tool_registry
        from backend.skills import registry as skill_reg

        nodes = set(tool_registry.get_skill_node_names())
        missing = sorted(
            f"{inst.name}_skill"
            for inst in skill_reg._instances
            if f"{inst.name}_skill" not in nodes
        )
        assert not missing, f"以下 Skill 未注册图节点: {missing}"

    def test_skill_node_registered_in_own_package(self):
        """节点注册必须写在各自 skills/<name>/__init__.py，不集中代注册。

        判据：skills/registry.py 里不得出现 register_skill_node(...) **调用**
        （唯一写法是各包自注册，见规范 §3.2）。用 AST 判调用而非文本匹配 ——
        注释里提到函数名不算违规。
        """
        reg_py = BACKEND / "skills" / "registry.py"
        tree = ast.parse(reg_py.read_text(encoding="utf-8"))
        calls = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Attribute)
                 and node.func.attr == "register_skill_node")
                or (isinstance(node.func, ast.Name)
                    and node.func.id == "register_skill_node")
            )
        ]
        assert not calls, (
            f"skills/registry.py:{calls} 存在 register_skill_node 调用；"
            "请在 skills/<name>/__init__.py 内自注册"
        )


# ==================== 4. workflow 声明 ↔ 注册双向对齐 ====================

class TestWorkflowManifestAlignment:
    def test_register_all_matches_manifest(self, monkeypatch):
        """register_all() 的清单与 capabilities.yaml 的 workflows 段必须一致。

        用本地 registry 实例，避免污染全局单例（其他测试各自 register）。
        """
        from backend.orchestration.router.manifest import load_manifest
        from backend.orchestration.workflow import registry as reg_mod

        local = reg_mod.WorkflowRegistry()
        monkeypatch.setattr(reg_mod, "get_workflow_registry", lambda: local)

        from backend.orchestration.workflows import register_all

        registered = set(register_all())
        declared = {w.name for w in load_manifest().workflows}

        assert not (declared - registered), (
            f"manifest 声明了但未被 register_all 注册（路由选中后无法执行）: "
            f"{sorted(declared - registered)}"
        )
        assert not (registered - declared), (
            f"已注册但未登记进 manifest（向量路由对它失明）: "
            f"{sorted(registered - declared)}"
        )

    def test_register_all_is_idempotent(self, monkeypatch):
        """重复调用不抛（registry.register 遇重复会抛 ValueError）。"""
        from backend.orchestration.workflow import registry as reg_mod

        local = reg_mod.WorkflowRegistry()
        monkeypatch.setattr(reg_mod, "get_workflow_registry", lambda: local)

        from backend.orchestration.workflows import register_all

        first = register_all()
        second = register_all()
        assert first == second


# ==================== 5. 命名约定 ====================

class TestNamingConvention:
    def test_manifest_names_follow_convention(self):
        """capability 形如 <域>.<动作>、workflow 为纯蛇形 —— load_manifest
        的 fail-fast 校验即是断言（能加载出来就说明命名合规）。"""
        from backend.orchestration.router.manifest import load_manifest

        m = load_manifest()
        assert m.capabilities and m.workflows

    def test_manifest_rejects_dotted_workflow_name(self, tmp_path):
        """带点的 workflow 名必须被拒（那会与 capability 命名空间混淆）。"""
        import yaml

        from backend.orchestration.router.manifest import ManifestError, load_manifest

        bad = {
            "version": 1,
            "capabilities": [
                {"name": "x.y", "skill": "s", "routed": True, "examples": ["a", "b"]}
            ],
            "workflows": [{"name": "bad.name", "examples": ["a"]}],
        }
        f = tmp_path / "bad_wf.yaml"
        f.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ManifestError, match="workflow 名"):
            load_manifest(str(f))
