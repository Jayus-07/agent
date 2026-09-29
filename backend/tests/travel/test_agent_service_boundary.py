"""tests/travel/test_agent_service_boundary.py — Phase 3 边界契约测试

四组冻结断言（用户 Phase 3 校准）：
  1. Agent-Service 依赖方向：Agent 不 import Provider/Tool、Node 不 import
     Provider、Service 不 import Graph；
  2. re-export 兼容：experts 命名空间与 repair 依赖路径继续有效；
  3. Runtime 冻结：run_expert_safely 签名 / status 枚举 / expert_history
     形态 / STOP F hooks 无变化；
  4. 五节点注册顺序冻结：poi → transit → weather → budget/risk →
     validate → repair → report。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import backend.travel.experts as experts_pkg

TRAVEL_DIR = Path(experts_pkg.__file__).resolve().parent.parent

# ---------- 1. 依赖方向（静态扫描） ----------

_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)


def _module_imports(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    return {m.group(1) or m.group(2) for m in _IMPORT_RE.finditer(source)}


def _py_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.py") if p.name != "__init__.py")


class TestAgentDependencyDirection:
    """Agent 层：只准 import services/models/config，禁止触达 Provider/Tool/state。"""

    # Phase 3 三 Agent 严格纪律；requirement_agent 为 Phase 2 冻结形态
    # （poi_seed 数据名录允许，单独断言见下）
    PHASE3_AGENTS = ["research_agent.py", "planning_agent.py",
                     "optimization_agent.py"]

    def test_agents_never_import_providers_or_tools(self):
        for path in _py_files(TRAVEL_DIR / "agents"):
            for mod in _module_imports(path):
                assert not mod.startswith("backend.providers"), (
                    f"{path.name} 直连 Provider 层：{mod}"
                )
                if path.name in self.PHASE3_AGENTS:
                    assert not mod.startswith("backend.tools"), (
                        f"{path.name} 直连 Tool 层：{mod}（Provider 触达只许在 service）"
                    )

    def test_agents_never_import_experts_graph_or_each_other(self):
        for path in _py_files(TRAVEL_DIR / "agents"):
            for mod in _module_imports(path):
                assert not mod.startswith("backend.travel.experts"), (
                    f"{path.name} 反向依赖 experts：{mod}"
                )
                assert not mod.startswith("backend.travel.graph"), (
                    f"{path.name} 触达 graph 层：{mod}"
                )
                assert "agents.research_agent" not in mod or path.name == "research_agent.py"
                assert "agents.planning_agent" not in mod or path.name == "planning_agent.py"
                assert "agents.optimization_agent" not in mod or path.name == "optimization_agent.py"

    def test_requirement_agent_same_discipline(self):
        # Phase 2 的 RequirementAgent 边界一致性：禁 Provider/LLM/图层
        # （poi_seed 数据名录是 Phase 2 冻结形态允许的数据源，不在此限）
        path = TRAVEL_DIR / "agents" / "requirement_agent.py"
        for mod in _module_imports(path):
            assert not mod.startswith("backend.providers")
            assert not mod.startswith(("backend.travel.experts",
                                       "backend.travel.graph"))
            assert "langchain" not in mod and "openai" not in mod


class TestNodeDependencyDirection:
    """Node 层（experts）：state/run_expert_safely/遥测归节点；Provider 直连禁止。"""

    def test_expert_nodes_never_import_providers(self):
        # V1/V2/V3 收敛断言：节点与编排面不得再直连 providers/live_map
        for path in _py_files(TRAVEL_DIR / "experts"):
            for mod in _module_imports(path):
                assert not mod.startswith("backend.providers"), (
                    f"{path.name} 直连 Provider 层：{mod}（应收敛至 service）"
                )
                assert "live_map" not in mod, (
                    f"{path.name} 直连传输 Tool live_map：{mod}"
                )

    def test_base_layer_untouched_by_service_split(self):
        # STOP F 冻结层不得反向依赖 services/agents
        source = (TRAVEL_DIR / "experts" / "base.py").read_text(encoding="utf-8")
        for mod in _module_imports(TRAVEL_DIR / "experts" / "base.py"):
            assert not mod.startswith(("backend.travel.services",
                                       "backend.travel.agents"))
        assert "TravelExpertHooks" in source  # STOP F hooks 接线原样


class TestServiceDependencyDirection:
    """Service 层：算法真身 + 唯一 Provider 触达点；禁止 import graph/experts。"""

    SERVICE_FILES = [
        "poi_service.py", "transit_service.py", "weather_service.py",
        "budget_service.py", "risk_service.py", "requirement_service.py",
    ]

    def test_services_never_import_graph_or_experts(self):
        for name in self.SERVICE_FILES:
            path = TRAVEL_DIR / "services" / name
            assert path.exists(), f"service 文件缺失：{name}"
            for mod in _module_imports(path):
                # 例外（Phase 2 冻结）：requirement_service 包装
                # graph_state.brief_fingerprint——纯函数单一事实源（G2），
                # 非 state IO；Phase 3 五 service 严格零 graph import
                if name == "requirement_service.py" and \
                        mod == "backend.travel.graph_state":
                    assert "brief_fingerprint" in path.read_text(encoding="utf-8")
                    continue
                assert not mod.startswith("backend.travel.graph"), (
                    f"{name} 触达 graph state/builder：{mod}（state 读写归节点）"
                )
                assert not mod.startswith("backend.travel.experts"), (
                    f"{name} 反向依赖 experts：{mod}"
                )

    def test_provider_touchpoints_only_in_services(self):
        # 全域 Provider 直连点唯一性：travel/ 下（除 service 与冻结交易域）
        # 不允许出现 providers import
        allowed_prefixes = ("services", "booking", "commerce", "models")
        offenders: list[str] = []
        for path in TRAVEL_DIR.rglob("*.py"):
            rel = path.relative_to(TRAVEL_DIR).as_posix()
            if rel.startswith(allowed_prefixes) or "register.py" in rel:
                continue
            for mod in _module_imports(path):
                if mod.startswith("backend.providers.travel"):
                    offenders.append(f"{rel}: {mod}")
        assert not offenders, f"Provider 直连越界：{offenders}"


# ---------- 2. re-export 兼容 ----------

class TestReExportCompat:
    def test_experts_namespace_all_symbols_resolve(self):
        for name in experts_pkg.__all__:
            assert hasattr(experts_pkg, name), f"experts/__init__ 符号失效：{name}"

    def test_repair_import_path_frozen(self):
        # repair.py:26 的既有 import 路径零改动继续工作
        from backend.travel.experts.transit import rebuild_days as via_expert
        from backend.travel.services.transit_service import (
            rebuild_days as via_service,
        )

        assert via_expert is via_service  # 同一真身，无双实现

    def test_test_facing_symbols_resolve(self):
        from backend.travel.experts.poi import build_skeleton  # noqa: F401
        from backend.travel.experts.transit import build_itinerary  # noqa: F401
        from backend.travel.experts.weather import (  # noqa: F401
            bad_weather_dates,
            fetch_forecast,
            is_bad_weather,
            plan_weather_swaps,
        )


# ---------- 3. Runtime 冻结 ----------

class TestRuntimeFrozen:
    def test_run_expert_safely_signature_frozen(self):
        from backend.travel.experts.base import run_expert_safely

        params = list(inspect.signature(run_expert_safely).parameters)
        assert params == ["expert_name", "fn", "state"]

    def test_status_and_type_enums_frozen(self):
        from backend.travel.experts.base import TravelExpertStatus, TravelExpertType

        assert {s.value for s in TravelExpertStatus} == {"success", "failed", "skipped"}
        assert {t.value for t in TravelExpertType} == {
            "poi", "transit", "weather", "budget", "risk",
        }

    def test_expert_history_shape_frozen(self):
        # 空候选路径可离线执行：expert_history 追加形态必须与迁移前逐字段一致
        from backend.travel.experts.poi import poi_expert_node

        update = poi_expert_node({"user_message": " xyz"})
        history = update["expert_history"]
        assert history and set(history[-1]) == {"expert", "status", "duration_ms"}
        assert history[-1]["expert"] == "poi"
        assert set(update["last_expert_result"]) >= {"expert", "status"}

    def test_five_expert_node_names_unchanged(self):
        from backend.travel.experts.budget import budget_expert_node
        from backend.travel.experts.poi import poi_expert_node
        from backend.travel.experts.risk import risk_expert_node
        from backend.travel.experts.transit import transit_expert_node
        from backend.travel.experts.weather import weather_expert_node

        for fn in (poi_expert_node, transit_expert_node, weather_expert_node,
                   budget_expert_node, risk_expert_node):
            assert callable(fn)


# ---------- 4. 五节点注册顺序冻结 ----------

class TestNodeRegistrationOrder:
    FROZEN_ORDER = [
        "TRAVEL_SLOT_FILLER",
        "TRAVEL_SUPERVISOR",
        "TRAVEL_POI_EXPERT",
        "TRAVEL_TRANSIT_EXPERT",
        "TRAVEL_WEATHER_EXPERT",
        "TRAVEL_BUDGET_EXPERT",
        "TRAVEL_RISK_EXPERT",
        "TRAVEL_VALIDATOR",
        "TRAVEL_REPAIR",
        "TRAVEL_REPORTER",
    ]

    def test_add_node_sequence_frozen(self):
        source = (TRAVEL_DIR / "graph_builder.py").read_text(encoding="utf-8")
        registered = re.findall(r"wf\.add_node\(\s*([A-Z_]+)", source)
        assert registered == self.FROZEN_ORDER, (
            f"travel 域图节点注册顺序被改动：{registered}"
        )

    def test_supervisor_hub_edges_unchanged(self):
        # 门禁/专家节点全部静态边回 supervisor（ hubs 拓扑零变更）
        source = (TRAVEL_DIR / "graph_builder.py").read_text(encoding="utf-8")
        assert "wf.add_edge(START, TRAVEL_SLOT_FILLER)" in source
        assert "wf.add_edge(TRAVEL_SLOT_FILLER, TRAVEL_SUPERVISOR)" in source
        assert "wf.add_edge(TRAVEL_REPORTER, END)" in source


# ---------- 附加：确定性抽查（parity 基线） ----------

class TestServiceDeterminism:
    def test_build_skeleton_deterministic(self):
        from backend.travel.agents.planning_agent import PlanningAgent
        from backend.travel.models.brief import TravelBrief
        from backend.travel.models.poi import Poi

        candidates = [
            Poi(poi_id=f"p{i}", name=f"点{i}", city="福州", lat=26.0 + i * 0.01,
                lng=119.3, rating=4.0 + i * 0.1, suggested_minutes=90,
                required=(i == 0), tags=[])
            for i in range(6)
        ]
        brief = TravelBrief(destination="福州", days=2, party_size=2)
        a = PlanningAgent().build_skeleton(brief, candidates)
        b = PlanningAgent().build_skeleton(brief, candidates)
        assert [d and [p.poi_id for p in d] for d in a.days] == \
               [d and [p.poi_id for p in d] for d in b.days]

    def test_agents_are_stateless_singletons_safe(self):
        # 同一实例重复调用结果一致（节点级单例复用的前提）
        from backend.travel.agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        it = type("It", (), {"all_pois": lambda self: []})()
        assert agent.assess_risks(it) == agent.assess_risks(it)
