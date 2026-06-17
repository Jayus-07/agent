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

    def score(self, memory_type: str, content: str) -> float:
        for pattern, weight, _dim in _DIMENSIONS:
            if re.search(pattern, content):
                # Apply type bonus
                type_bonus = {
                    "user_fact": 0.1,
                    "preference": 0.05,
                    "decision": 0.08,
                    "knowledge": 0.0,
                }.get(memory_type, 0.0)
                return min(weight + type_bonus, 1.0)
        return 0.2

    def should_store(self, score: float) -> bool:
        return score >= self.THRESHOLD
