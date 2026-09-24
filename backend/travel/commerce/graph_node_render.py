"""travel/commerce/graph_node_render.py — 渲染入口与澄清话术（STOP K6）

reporter.render 的图内包装 + 缺槽位澄清模板。澄清话术与 travel 域
build_clarification 同立场：**缺什么问什么，不猜、不硬查**。
"""
from __future__ import annotations

from backend.travel.commerce.reporter import render
from backend.travel.commerce.service import CommerceResult

_CLARIFY_BY_MISSING = {
    "city": "请告诉我在哪个城市入住？",
    "check_in": "请告诉我入住日期（例：10月3日到5日）。",
    "check_out": "请告诉我退房日期。",
    "origin": "请告诉我出发城市。",
    "destination": "请告诉我目的地城市。",
    "departure_date": "请告诉我出发日期（例：10月3日）。",
    "params": "查询参数有误，请调整后重试。",
    "intent": "请明确说明想查酒店还是机票。",
}


def build_clarification(commerce_type: str, missing: list[str]) -> str:
    """缺槽位追问（确定性模板；一次问齐，不挤牙膏）。"""
    type_word = {"hotel": "酒店", "flight": "航班"}.get(commerce_type, "行程")
    questions = [
        _CLARIFY_BY_MISSING.get(m, f"请补充：{m}")
        for m in missing if m != "params"
    ]
    intro = f"我可以帮您查询{type_word}，但还需要一些信息："
    return intro + "\n" + "\n".join(f"- {q}" for q in questions)


def render_commerce_result(result: CommerceResult) -> str:
    """CommerceResult → 用户可见文本（reporter.render 直通）。"""
    return render(result)
