"""skills/travel_poi/skill.py — 旅游 POI 检索 Skill

Capability: travel.poi_search

为什么只把这一项注册成主图能力：
  行程生成（travel.plan）**刻意不注册**。它需要「槽位追问 → 骨架分配 →
  排程 → 校验 → 修复」的有状态多步流程，已经由旅游域图承担；再包一个
  Skill 让主图 planner 直接调，就会产生第二套实现，且少了约束校验这道
  安全网 —— 同一件事两个实现、其中一个还没校验，是明确的倒退。
  而 POI 候选检索是**无状态单点能力**，主图（如竞品选址、门店周边分析）
  完全可以复用，注册它不会带来歧义。
"""
from backend.shared.logger import logger
from backend.skills.base import BaseSkill
from backend.tools.travel.poi import travel_poi_search_tool


class TravelPoiSkill(BaseSkill):
    name = "travel_poi"
    capabilities = ["travel.poi_search"]
    description = (
        "检索旅行目的地城市（当前支持：福州、厦门、杭州）的候选兴趣点（POI），"
        "返回含营业时段、坐标、票价、建议停留时长的结构化列表。"
        "适用于行程规划前的候选池获取，或选址/周边分析中的地点摸底。"
        "注意：返回的是候选池，不含排程、通勤与时间可行性校验。"
    )
    params_schema = {
        "city": {"type": "string", "required": True,
                 "description": "目的地城市名，如「福州」「厦门」「杭州」"},
        "preferences": {"type": "string", "required": False,
                        "description": "逗号分隔偏好标签：自然/人文/美食/亲子/购物/夜生活/摄影"},
        "avoid": {"type": "string", "required": False,
                  "description": "逗号分隔的避雷关键词（地名或类别），命中即剔除"},
        "must_go": {"type": "string", "required": False,
                    "description": "逗号分隔的必去地点名，命中项标记 required 并优先返回"},
        "limit": {"type": "integer", "required": False,
                  "description": "返回条数上限（默认 20）"},
    }
    examples = [
        {"city": "福州", "preferences": "人文,摄影", "must_go": "三坊七巷", "limit": 10},
        {"city": "厦门", "avoid": "购物", "limit": 8},
    ]
    # Tool 返回 JSON 字符串，声明 structured 让边界归一化成 dict
    output_type = "structured"

    @property
    def _tool_fn(self):
        return travel_poi_search_tool


async def travel_poi_skill_node(state: dict) -> dict:
    """Skill 节点函数（供主图 Planner 的 DAG 调用）"""
    skill = TravelPoiSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "travel.poi_search")
    logger.info("[TravelPOISkill] step=%s cap=%s", state.get("current_step_id"), cap)
    return await skill.execute(state, step_capability=cap)
