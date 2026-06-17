"""MemoryWorthinessClassifier — rule-first, LLM fallback"""
import re
from llm.llm_factory import llm
from utils.logger import logger

# Order matters — earlier patterns match first
_STORE_SIGNALS = [
    # Identity / role / skill
    r"(?<!\w)(工程师|经理|主管|总监|架构师|设计师|产品经理|开发|测试|运维|运营)(?!\w)",
    r"(?<!\w)(后端|前端|全栈|算法|数据|AI|ML|DevOps|SRE)(?!\w)",
    r"我是", r"我叫", r"我在", r"我的", r"我负责", r"我管理",
    r"我喜欢", r"我习惯", r"我常用", r"我偏好", r"我擅长",
    # Project / technical
    r"项目", r"系统", r"架构", r"技术栈", r"方案", r"决策",
    r"开发", r"部署", r"配置", r"数据库", r"框架",
    r"代码", r"测试", r"上线", r"运维", r"监控",
    r"FastAPI", r"Django", r"Flask", r"React", r"Vue",
    r"Docker", r"K8s", r"Kubernetes", r"Redis", r"PostgreSQL",
]

_IGNORE_SIGNALS = [
    r"天气", r"你好", r"谢谢", r"好的", r"收到", r"明白",
    r"今天.*吃", r"周末.*去", r"哈哈", r"嗯", r"哦",
    r"再见", r"拜拜", r"稍等", r"等一下",
]

_TRIGGER_PROMPT = """判断这条信息是否值得存入长期记忆。只需回答 STORE 或 IGNORE。

信息: "{content}"

规则:
- 关于用户身份/角色/技能/偏好的事实 → STORE
- 关于项目/工作/技术决策的信息 → STORE
- 问候/闲聊/确认/情绪表达 → IGNORE

回答:"""


class MemoryWorthinessClassifier:
    def classify(self, content: str, fact_type: str = "") -> str:
        # user_fact / preference from LLM extraction → strong STORE signal
        if fact_type in ("user_fact", "preference"):
            return "STORE"
        # Rule layer: IGNORE first
        for pat in _IGNORE_SIGNALS:
            if re.search(pat, content):
                return "IGNORE"
        for pat in _STORE_SIGNALS:
            if re.search(pat, content):
                return "STORE"
        # Heuristic: too short → IGNORE
        if len(content) < 4:
            return "IGNORE"
        return self._llm_classify(content)

    def _llm_classify(self, content: str) -> str:
        try:
            resp = llm.invoke(_TRIGGER_PROMPT.format(content=content))
            text = resp.content if hasattr(resp, "content") else str(resp)
            return "STORE" if "STORE" in text.upper() else "IGNORE"
        except Exception as e:
            logger.warning(f"[Trigger] LLM 分类失败: {e}")
            return "IGNORE"
