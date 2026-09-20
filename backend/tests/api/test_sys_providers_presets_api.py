"""api/routes/sys_providers.py —— GET /sys/providers/presets 契约

末端点喂给「新增/编辑供应商」抽屉的厂商·协议下拉。锁三条：

1. **admin only** —— 目录本身不敏感，但它决定「服务会向哪个地址发请求」，
   与 B.6 的 SSRF 边界同源，不该对 editor 开放。
2. **裸 dict（§1.1.1）+ 字段白名单** —— 条目字段必须精确可控。特别地，
   目录里**不允许出现名为 `apiKey` 的字段**（`apiKeyHint` 是「Key 长什么样」
   的说明文字，不是密钥载体），防止日后有人顺手把 Key 塞进这个只读接口。
3. **plans 的 billing 是派生值** —— 与设计文档 B.2/B.3 一致，
   Token Plan 与 Coding Plan 都落 `subscription`。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import require_admin_user
from backend.app.api.routes import sys_providers
from backend.infra.llm import provider_presets as pp


class _FakeIdent:
    actor = "user:test-admin"
    role = "admin"
    kind = "user"


@pytest.fixture
def client() -> TestClient:
    a = FastAPI()
    a.include_router(sys_providers.router)
    a.dependency_overrides[require_admin_user] = lambda: _FakeIdent()
    return TestClient(a)


#: 唯一的合法条目字段集。新增字段必须显式改这里 —— 这不是形式主义，
#: 它是「只读接口不得夹带密钥」这条约束唯一可执行的地方。
_ALLOWED_ITEM_KEYS = {
    "id", "plan", "vendor", "variant", "driver", "driverLabel",
    "baseUrl", "apiKeyHint", "note", "placeholders",
}


# ── 限制：admin only ────────────────────────────────────────────────────


def test_requires_admin():
    """不覆盖鉴权依赖 → 走真实 `require_admin_user`（无 JWT 应被拒）。"""
    a = FastAPI()
    a.include_router(sys_providers.router)
    assert TestClient(a).get("/sys/providers/presets").status_code in (401, 403)


# ── 形状 ────────────────────────────────────────────────────────────────


def test_bare_dict_without_result_envelope(client):
    body = client.get("/sys/providers/presets").json()
    assert isinstance(body["items"], list)
    assert isinstance(body["plans"], list)
    assert body["actor"] == "user:test-admin"
    assert "data" not in body and "result" not in body


def test_plans_expose_derived_billing(client):
    plans = client.get("/sys/providers/presets").json()["plans"]
    assert [(p["id"], p["billing"]) for p in plans] == [
        ("token_plan", "subscription"),
        ("coding_plan", "subscription"),
        ("metered", "metered"),
    ]


def test_items_mirror_module_catalog(client):
    """端点只是模块的投影：条数与 id 必须逐一对应，否则就是漏字段/漏条目。"""
    items = client.get("/sys/providers/presets").json()["items"]
    assert len(items) == len(pp.PROVIDER_PRESETS)
    assert [i["id"] for i in items] == [p["id"] for p in pp.PROVIDER_PRESETS]


def test_item_field_whitelist(client):
    for item in client.get("/sys/providers/presets").json()["items"]:
        assert set(item) <= _ALLOWED_ITEM_KEYS, item.get("id")


def test_item_camel_case_mapping(client):
    """前端按 camelCase 消费（与 ProviderRow 一致），别漏了别名。"""
    items = {i["id"]: i for i in client.get("/sys/providers/presets").json()["items"]}

    volc = items["volc-coding-openai"]
    assert volc["plan"] == "coding_plan"
    assert volc["vendor"] == "火山引擎（方舟）"
    assert volc["driver"] == "openai"
    assert volc["driverLabel"] == "OpenAI 兼容"
    assert volc["baseUrl"] == "https://ark.cn-beijing.volces.com/api/coding/v3"
    assert volc["placeholders"] == []
    assert "按量付费" in volc["note"]          # 用错端点会多花钱的提醒

    aliyun = items["aliyun-metered-cn-openai"]
    assert aliyun["placeholders"] == ["WorkspaceId"]
    assert "{WorkspaceId}" in aliyun["baseUrl"]

    assert items["kimi-code-cn-anthropic"]["driverLabel"] == "Anthropic 兼容"


def test_anthropic_rows_are_reachable(client):
    """目录里相当一部分是 Anthropic 兼容端点。

    若端点把它们过滤掉（或前端拿不到 driver），这些厂商就永远登记不进来。
    """
    items = client.get("/sys/providers/presets").json()["items"]
    anthropic = [i for i in items if i["driver"] == "anthropic"]
    assert len(anthropic) == 17
    assert {i["vendor"] for i in anthropic} >= {
        "火山引擎（方舟）", "DeepSeek", "MiniMax", "小米 MiMo", "Kimi Code"
    }


# ── 不得夹带密钥 ────────────────────────────────────────────────────────


def test_no_api_key_field_anywhere(client):
    raw = client.get("/sys/providers/presets").text
    assert "api_key" not in raw
    assert "key_cipher" not in raw
    for item in client.get("/sys/providers/presets").json()["items"]:
        assert "apiKey" not in item


def test_api_key_hint_is_guidance_not_a_secret(client):
    """`apiKeyHint` 是「Key 长什么样」的说明（如 `sk- 开头`），长度必须像说明文字。"""
    for item in client.get("/sys/providers/presets").json()["items"]:
        assert len(item["apiKeyHint"]) <= 32, item["id"]
