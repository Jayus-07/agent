"""RAG Chain Prompt 模板定义与构建器。

优先从 prompt_service 获取，降级到内置 DEFAULT 常量。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

from backend.shared.logger import logger

# =====================================================
# Prompt: 历史感知查询重写（DEFAULT fallback — 优先从 prompt_service 获取）
# =====================================================

DEFAULT_CONTEXTUALIZE_SYSTEM = """你是跨境电商知识库的查询重写助手。

根据对话历史，将用户问题重新表述为独立的检索查询。

规则:
1. 如果用户使用代词（他、她、这个、那个、它），请替换为对话历史中的具体实体
2. 如果问题已经独立完整，直接返回原问题
3. 不要回答问题，只输出改写后的查询
4. 保留所有专有名词、技术术语、业务词汇
5. 不要添加解释或 markdown 格式"""

# =====================================================
# Prompt: QA 回答（DEFAULT fallback — 优先从 prompt_service 获取）
# =====================================================

DEFAULT_QA_SYSTEM = """你是电商企业知识库助手。你只能依据「资料」中明确提供的信息回答问题。

## 核心规则

1. **单证据原则**：每个事实、数字、日期、时效、条件，必须能由一个 Evidence 独立支持。禁止拼接多个 Evidence 推导原文不存在的新事实。

2. **证据边界**：每条 Evidence 标注了 [Query]、[文档]、[章节]。不同 Query、不同章节的信息属于不同上下文，**禁止跨边界拼接**。

3. **数字/时效零容忍**：所有数字、日期、百分比、SLA 必须与原文逐字一致。禁止修改、换算、推断。禁止将一条 Evidence 中的数字套用到另一条 Evidence。

4. **信息不足时**：明确写「资料未提及」。禁止猜测、常识补充、相似流程推断。

## 回答格式

正文用 Markdown。多个要点必须用有序列表分点呈现（每点独占一行：`1. xxx [E1]` 换行 `2. xxx [E2]`），禁止把多个要点挤在同一段落。每个事实必须带 Evidence 引用 [En]（如 [E1]、[E2]）。

资料充分时示例：
```
1. 通用售后流程：审核结果为 1-2 个工作日内反馈。[E1]
2. 差评处理要求：48小时内给出具体解决方案。[E2]
```

信息不足时：
```
资料未提及。
```

正文末尾必须输出：
- 可回答 → `<!--META{{"can_answer":true,"citations":["E1","E2"],"confidence":0.85}}-->`
- 不可回答 → `<!--META{{"can_answer":false,"reason":"no_evidence","confidence":0.1}}-->`

reason 取值：no_evidence / low_relevance / insufficient / out_of_scope

资料:
{context}"""

DEFAULT_DOCUMENT_TEMPLATE = (
    "[Evidence E{index}]\n"
    "{query_label}"
    "{doc_label}"
    "{section_label}"
    "{chunk_label}"
    "{type_label}"
    "{domain_label}"
    "{page_content}"
)

# 历史感知改写只在问题确实依赖上下文时启用。原问题独立完整时跳过改写，
# 既省一次 LLM 调用，也避免模型把知识库/平台名称扩写进检索 query。
_HISTORY_REWRITE_CUES = (
    "他", "她", "它", "这个", "那个", "这些", "那些", "该", "其", "此",
    "上述", "前述", "刚才", "刚刚", "之前", "前面", "还有", "另外", "再",
    "那", "那么", "继续", "同样", "换成", "改成", "呢",
)


def _needs_history_rewrite(question: str) -> bool:
    """判断问题是否真的需要结合历史改写。

    保守策略：只有出现代词、承接语或极短追问时才走 history-aware 改写；
    独立完整的问题直接走 standalone 检索，防止改写模型引入原问题没有的
    业务域/产品/组织词。
    """
    q = (question or "").strip()
    if not q:
        return False
    if len(q) <= 5:
        return True
    return any(cue in q for cue in _HISTORY_REWRITE_CUES)


def _build_contextualize_prompt() -> ChatPromptTemplate:
    """从 prompt_service 构建查询重写 ChatPromptTemplate。"""
    # 延迟导入避开 transformers 链 ~7s
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    system_text = DEFAULT_CONTEXTUALIZE_SYSTEM
    human_text = "{input}"
    try:
        from backend.prompts.service import prompt_service
        full = prompt_service.get_template_sync("rag.contextualize")
        parts = full.split("---", 1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise ValueError("rag.contextualize 缺少 system/human 分隔")
        system_text, human_text = (part.strip() for part in parts)
    except Exception as e:  # noqa: BLE001
        logger.warning("[RAGChain] rag.contextualize 回退内置默认（%s）", type(e).__name__)
    return ChatPromptTemplate.from_messages([
        ("system", system_text),
        MessagesPlaceholder("chat_history"),
        ("human", human_text),
    ])


def _build_qa_prompt() -> ChatPromptTemplate:
    """从 prompt_service 构建 QA ChatPromptTemplate。"""
    # 延迟导入避开 transformers 链 ~7s
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    system_text = DEFAULT_QA_SYSTEM
    try:
        from backend.prompts.service import prompt_service
        full = prompt_service.get_template_sync("rag.qa")
        parts = full.split("---", 1)
        if len(parts) == 2:
            system_text = parts[0].strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RAGChain] prompt_service 不可用，rag.qa 回退内置默认: {e}")
    return ChatPromptTemplate.from_messages([
        ("system", system_text),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ])


def _build_document_prompt() -> PromptTemplate:
    """从 prompt_service 构建证据格式化 PromptTemplate。"""
    # 延迟导入避开 transformers 链 ~7s
    from langchain_core.prompts import PromptTemplate
    template_str = DEFAULT_DOCUMENT_TEMPLATE
    try:
        from backend.prompts.service import prompt_service
        template_str = prompt_service.get_template_sync("rag.document")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RAGChain] prompt_service 不可用，rag.document 回退内置默认: {e}")
    return PromptTemplate.from_template(template_str)
