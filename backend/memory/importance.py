"""ImportanceScorer — 5-dimension scoring 0.0-1.0"""
import re

_DIMENSIONS = [
    (r"我是|我叫|我的职位|我的角色|负责", 1.0, "user_long_term_fact"),
    (r"我喜欢|我习惯|我偏好|我常用|我讨厌", 0.8, "user_preference"),
    (r"项目|架构|技术栈|系统|方案", 0.7, "project_context"),
    (r"开发|部署|配置|测试|上线|运维", 0.5, "work_background"),
    (r".*", 0.2, "casual_chat"),  # default
]

class ImportanceScorer:
    THRESHOLD = 0.6

    # 类型保底分：能被 LLM 提取为 user_fact/preference/decision 的内容本身就有记忆价值。
    # 旧实现中内容未命中任何关键词时落入兜底 0.2，导致绝大多数偏好/身份事实
    # 低于 0.6 阈值被丢弃（长期记忆几乎存不进东西）。
    _TYPE_BASE = {
        "user_fact": 0.7,
        "preference": 0.65,
        "decision": 0.65,
        "knowledge": 0.4,
    }

    def score(self, memory_type: str, content: str) -> float:
        weight = 0.2
        for pattern, w, _dim in _DIMENSIONS:
            if re.search(pattern, content):
                weight = w
                break
        # Apply type bonus
        type_bonus = {
            "user_fact": 0.1,
            "preference": 0.05,
            "decision": 0.08,
            "knowledge": 0.0,
        }.get(memory_type, 0.0)
        base = self._TYPE_BASE.get(memory_type, 0.2)
        return min(max(weight, base) + type_bonus, 1.0)

    def should_store(self, score: float) -> bool:
        return score >= self.THRESHOLD
