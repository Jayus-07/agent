"""Agent/Capability 只读总览 API 契约测试（B13 管理端 /agents、/skills 数据源）

不 mock 内部注册表（manifest / skills registry 本身就是被验证的事实源），
只验证响应结构与三层对账口径：
  - /agents：kind 分类齐全、count 自洽、Skill 节点带能力归属
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
