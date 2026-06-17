"""MemoryWorthinessClassifier — rule-first, LLM fallback"""
import re
from llm.llm_factory import llm
from utils.logger import logger

_STORE_SIGNALS = [
    r"我是", r"我在", r"我喜欢", r"我习惯", r"我常用", r"我偏好",
    r"项目", r"系统", r"架构", r"技术栈", r"负责", r"管理",
    r"开发", r"部署", r"配置", r"数据库", r"方案", r"决策",
]
_IGNORE_SIGNALS = [
    r"天气", r"你好", r"谢谢", r"好的", r"收到", r"明白",
    r"今天.*吃", r"周末.*去", r"哈哈", r"嗯", r"哦",
]

_TRIGGER_PROMPT = """判断这条信息是否值得存入长期记忆。只需回答 STORE 或 IGNORE。

信息: "{content}"

规则:
- 关于用户身份/角色/技能/偏好的事实 → STORE
- 关于项目/工作/技术决策的信息 → STORE
- 问候/闲聊/确认/情绪表达 → IGNORE

回答:"""


class MemoryWorthinessClassifier:
    def classify(self, content: str) -> str:
        # Rule layer
        for pat in _STORE_SIGNALS:
            if re.search(pat, content):
                return "STORE"
        for pat in _IGNORE_SIGNALS:
            if re.search(pat, content):
                return "IGNORE"
        # LLM fallback
        return self._llm_classify(content)

    def _llm_classify(self, content: str) -> str:
        try:
            resp = llm.invoke(_TRIGGER_PROMPT.format(content=content))
            text = resp.content if hasattr(resp, "content") else str(resp)
            return "STORE" if "STORE" in text.upper() else "IGNORE"
        except Exception as e:
            logger.warning(f"[Trigger] LLM 分类失败: {e}")
            return "IGNORE"
