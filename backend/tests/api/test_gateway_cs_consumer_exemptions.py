"""网关角色闸豁免清单的消费者端点锚定。

/api/cs 前缀闸只放行 supervisor/agent（apisix/plugins/gateway-auth.lua），
用户侧端点靠 ROLE_GATE_EXEMPT_PATTERNS 正则豁免。cs-workstation-v2 曾漏加
4 条（customer-context / refund-requests 列表与详情 / refund-draft），导致
普通用户 403 role-insufficient——用户端右栏客户资料与退款折叠卡全断
（2026-11-03 公开投产实测发现）。本测试静态锚定这些豁免必须存在，防止
清单回退再次拦截消费者链路。

另锚定 Lua pattern 元字符转义（2026-11-03 第二层发现）：豁免清单用
string.match(uri, pat) 匹配，path 里的字面 `-` 必须写成 `%-`——裸 `-` 是
「最短重复」修饰符，pattern 静默不匹配（首修时四条新 pattern 含裸 `-` 全部
无效，而既有无 `-` 条目正常，掩盖了缺陷）。
"""
from __future__ import annotations

import re
from pathlib import Path

LUA = Path(__file__).resolve().parents[3] / "apisix" / "plugins" / "gateway-auth.lua"


def _exempt_pattern_lines(text: str) -> list[str]:
    """提取 ROLE_GATE_EXEMPT_PATTERNS 表内引号中的 pattern 字符串。"""
    table = re.search(
        r"ROLE_GATE_EXEMPT_PATTERNS\s*=\s*\{(.*?)\n\}", text, re.S,
    )
    assert table, "未找到 ROLE_GATE_EXEMPT_PATTERNS 表"
    return re.findall(r'"([^"]+)"', table.group(1))


def test_consumer_exempt_patterns_present():
    text = LUA.read_text(encoding="utf-8")
    for pat in (
        '"^/api/cs/customer%-context$"',
        '"^/api/cs/refund%-requests$"',
        '"^/api/cs/refund%-requests/[^/]+$"',
        '"^/api/cs/conversations/my/.+/refund%-draft$"',
    ):
        assert pat in text, f"豁免清单缺少消费者端点 pattern: {pat}"


def test_exempt_patterns_escape_literal_dash():
    """表内每个 pattern 的字面 `-` 必须转义为 `%-`（`[]` 字符类内除外）。"""
    text = LUA.read_text(encoding="utf-8")
    for pat in _exempt_pattern_lines(text):
        # 移除字符类 [...]/[^...]，其内的 - 是字面区间/字面量
        outside = re.sub(r"\[.*?\]", "", pat)
        bad = re.search(r"(?<!%)-", outside)
        assert not bad, (
            f"pattern 存在未转义的字面 `-`（Lua 最短重复修饰符，静默失配）: {pat}"
        )


def test_exempt_section_documents_consumer_scope():
    """豁免段落必须保留「只放用户侧」边界说明，防止未来把坐席端点误豁免。"""
    text = LUA.read_text(encoding="utf-8")
    assert "只放用户侧" in text
