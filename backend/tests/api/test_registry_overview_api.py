"""Agent/Capability 只读总览 API 契约测试（B13 管理端 /agents、/skills 数据源）

不 mock 内部注册表（manifest / skills registry 本身就是被验证的事实源），
只验证响应结构与三层对账口径：
  - /agents：kind 分类齐全、count 自洽、Skill 节点带能力归属；
    域图节点的归属三元组（domain / domain_label / subflow）与域图注册表同源
    （管理端不再手写归属映射）
  - /capabilities：manifest 声明 ↔ skills 注册表对账（registered 恒真，
    漂移由 test_registry_consistency 在 CI 拦截，这里守住 API 层口径）
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes.agents import router as agents_router
from backend.app.api.routes.capabilities import router as capabilities_router


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(agents_router)
    app.include_router(capabilities_router)
    return TestClient(app)


# ── GET /agents ────────────────────────────────────────

def test_agents_shape(client):
    body = client.get("/agents").json()
    assert body["count"] == len(body["agents"])
    assert sum(body["summary"].values()) == body["count"]
    kinds = {a["kind"] for a in body["agents"]}
    assert kinds == {"orchestration", "skill", "domain_graph"}
    for a in body["agents"]:
        assert a["name"] and a["label"]


def test_agents_orchestration_nodes_present(client):
    body = client.get("/agents").json()
    names = {a["name"] for a in body["agents"] if a["kind"] == "orchestration"}
    # 主图 8 个内置节点（builder.build_main_graph 的 add_node 名单）
    assert {"router", "tool_selector", "planner", "critique",
            "supervisor", "reporter"} <= names


def test_agents_skill_nodes_carry_capabilities(client):
    body = client.get("/agents").json()
    skill_nodes = [a for a in body["agents"] if a["kind"] == "skill"]
    assert skill_nodes, "至少应发现 1 个 Skill 节点（sql/rag 等）"
    with_caps = [a for a in skill_nodes if a["capabilities"]]
    assert with_caps, "Skill 节点应带 capabilities 归属"
    # 每个 Skill 节点名遵循 <skill>_skill 约定
    assert all(a["name"].endswith("_skill") for a in skill_nodes)


def test_agents_domain_graph_attribution_matches_registry(client):
    """域图节点的归属三元组必须与域图注册表逐一对齐（管理端不再手写归属映射）。

    管理端原先自己维护一份 ``ROUTE_MODE_DOMAIN_META``（第五处手写副本），
    且已实证漂移：它写「Travel Domain」，而注册表里 travel 图的 label 是
    「旅游规划图执行」。归属改由本接口下发后，前端只做拼装。
    """
    from backend.orchestration.domain_registry import domain_graph_registry

    graphs = domain_graph_registry.get_all()
    nodes = {
        a["route_mode"]: a
        for a in client.get("/agents").json()["agents"]
        if a["kind"] == "domain_graph"
    }
    assert set(nodes) == set(graphs), "域图清单须与注册表逐一对齐（多一张少一张都是漂移）"

    for name, graph in graphs.items():
        node = nodes[name]
        assert node["subflow"] == graph.subflow
        if graph.domain:
            assert node["domain"] == graph.domain
            # 展示名必须就是父图自己的 label——不许再发明一个后端没有的名字
            assert node["domain_label"] == graphs[graph.domain].label, (
                f"{name} 的 domain_label 不是父图 label：展示层又在自造业务名"
            )
        else:
            assert (node["domain"], node["domain_label"], node["subflow"]) == (None, None, None), (
                f"顶级域图 {name} 不得带归属（本图即顶级域）"
            )


def test_agents_attribution_contract_values_locked(client):
    """锁定对外展示的归属取值（字面量，不写重算式）。

    期望值刻意写死：若改成 ``graph.subflow`` 之类去比对，两边同源，改了注册表
    会一起变，成为恒真断言（M8 用例已踩过此坑）。
    """
    nodes = {
        a["route_mode"]: a
        for a in client.get("/agents").json()["agents"]
        if a["kind"] == "domain_graph"
    }
    assert nodes["travel_commerce"]["domain"] == "travel"
    assert nodes["travel_commerce"]["subflow"] == "commerce"
    assert nodes["travel_booking"]["domain"] == "travel"
    assert nodes["travel_booking"]["subflow"] == "booking"
    assert nodes["travel"]["domain"] is None
    assert nodes["customer_service"]["domain"] is None
    assert nodes["selection_funnel"]["domain"] is None
    # 当前恰有 2 张子流域图
    assert sorted(a["route_mode"] for a in nodes.values() if a["domain"]) == [
        "travel_booking", "travel_commerce",
    ]


def test_agents_attribution_follows_newly_registered_subflow(client):
    """归属只声明在注册表：新注册一张子流图，接口展示自动跟随（前端零改动）。

    这是「派生」而非「照抄」的直接证据——新增域时不需要同步改前端映射，
    也就不存在「漏改即展示错误归属」的静默失败。
    """
    from backend.orchestration.domain_graph import DomainGraph
    from backend.orchestration.domain_registry import domain_graph_registry

    probe = DomainGraph(
        name="_api_probe_subflow",
        node_name="_api_probe_subflow_node",
        label="探针子流图",
        adapter=lambda state: state,
        domain="travel",
        subflow="probe",
    )
    snapshot = domain_graph_registry.get_all()
    try:
        domain_graph_registry.register(probe)
        nodes = {
            a["route_mode"]: a
            for a in client.get("/agents").json()["agents"]
            if a["kind"] == "domain_graph"
        }
        assert nodes["_api_probe_subflow"]["domain"] == "travel"
        assert nodes["_api_probe_subflow"]["subflow"] == "probe"
        assert nodes["_api_probe_subflow"]["domain_label"] == (
            domain_graph_registry.get("travel").label
        )
    finally:
        domain_graph_registry._domains.clear()
        domain_graph_registry._domains.update(snapshot)


# ── GET /capabilities ──────────────────────────────────

def test_capabilities_shape(client):
    body = client.get("/capabilities").json()
    assert body["count"] == len(body["capabilities"])
    assert body["routed_count"] == sum(1 for c in body["capabilities"] if c["routed"])
    assert body["skills"], "Skill 注册表不应为空"
    for w in body["workflows"]:
        assert w["examples"], "workflow 至少 1 条 examples（manifest 校验口径）"


def test_capabilities_routed_contract(client):
    body = client.get("/capabilities").json()
    for c in body["capabilities"]:
        if c["routed"]:
            assert len(c["examples"]) >= 2, f"{c['name']} routed 但 examples 不足"
        else:
            assert c["reason"], f"{c['name']} routed:false 但缺 reason"
    assert body["routed_count"] < body["count"] or body["count"] > 0


def test_capabilities_manifest_matches_skill_registry(client):
    """API 层对账：声明过的 capability 均已注册，且 skill 归属一致。"""
    from backend.skills import registry as skill_registry
    reg = skill_registry.list_capabilities()

    body = client.get("/capabilities").json()
    for c in body["capabilities"]:
        assert c["registered"] is True, f"{c['name']} 已声明但未注册（漂移）"
        assert reg.get(c["name"]) == c["skill"], f"{c['name']} skill 归属不一致"

    # 双向：注册表里每个 capability 也都被 manifest 声明
    declared = {c["name"] for c in body["capabilities"]}
    assert set(reg) <= declared
