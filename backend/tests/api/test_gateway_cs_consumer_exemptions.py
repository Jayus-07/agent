"""网关角色闸豁免清单的消费者端点锚定。

/api/cs 前缀闸只放行 supervisor/agent（apisix/plugins/gateway-auth.lua），
用户侧端点靠 ROLE_GATE_EXEMPT_PATTERNS 正则豁免。cs-workstation-v2 曾漏加
4 条（customer-context / refund-requests 列表与详情 / refund-draft），导致
普通用户 403 role-insufficient——用户端右栏客户资料与退款折叠卡全断
（2026-11-03 公开投产实测发现）。本测试静态锚定这些豁免必须存在，防止
清单回退再次拦截消费者链路。
"""
from __future__ import annotations

from pathlib import Path

LUA = Path(__file__).resolve().parents[3] / "apisix" / "plugins" / "gateway-auth.lua"


def test_consumer_exempt_patterns_present():
    text = LUA.read_text(encoding="utf-8")
    for pat in (
        '"^/api/cs/customer-context$"',
        '"^/api/cs/refund-requests$"',
        '"^/api/cs/refund-requests/[^/]+$"',
        '"^/api/cs/conversations/my/.+/refund-draft$"',
    ):
        assert pat in text, f"豁免清单缺少消费者端点 pattern: {pat}"


def test_exempt_section_documents_consumer_scope():
    """豁免段落必须保留「只放用户侧」边界说明，防止未来把坐席端点误豁免。"""
    text = LUA.read_text(encoding="utf-8")
    assert "只放用户侧" in text
