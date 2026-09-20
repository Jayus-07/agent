"""评测答案生成后端 — LLM Judge 的推理链路。

评测生成统一使用 `eval_gen` 角色绑定的模型。模型可以来自已登记的云端供应商
或本地 Ollama；云端模型仅在评测显式 opt-in（--judge/--ragas）时使用，避免
改变既有基线口径。

统一入口 generate_answer(question, context, allow_cloud=...)：
allow_cloud=True 且本地 Ollama 不可用时走云后端；否则回落 Ollama 行为。
"""
from __future__ import annotations

from backend.config.llm import OLLAMA_BASE_URL, OLLAMA_ENABLED
from backend.config import model_roles
from backend.shared.logger import logger

_ollama_skip_logged = False
_cloud_skip_logged = False

_token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
# Evaluator 侧 token 计量（judge / RAGAS 的 LLM 调用）。
# 这两条链路不走 generation 的 SUT 生成路径，也不经过 JSONL token tracker
# （data/token_usage.jsonl 只有 embedding/rerank 记录），此前完全未计入
# token_summary.evaluator.judge（2026-09-17 实测恒为 0）。现统一在此累加，
# 由 service._inject_token_totals 并入 evaluator 侧。
_evaluator_token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0}
_chat_cache: dict[tuple, object] = {}


class EvalModelConfigurationError(RuntimeError):
    """评测角色没有配置或当前供应商不可用。"""


def _configured_eval_model() -> str:
    """读取评测生成角色，不再回落到本地 Ollama 默认模型。"""
    return model_roles.resolve_runtime_name("eval_gen")


def resolve_eval_model() -> tuple[str, str]:
    """解析评测模型及供应商，并在调用前给出可操作的失败原因。"""
    model_name = _configured_eval_model().strip()
    if not model_name:
        raise EvalModelConfigurationError(
            "未配置评测生成模型，请在模型管理的角色绑定中选择已登记模型"
        )

    from backend.infra.llm.credentials import check_provider_usable
    from backend.infra.llm.models import ProviderResolutionError, resolve_provider

    try:
        provider = resolve_provider(model_name, strict=True)
    except ProviderResolutionError as exc:
        raise EvalModelConfigurationError(str(exc)) from exc

    reason = check_provider_usable(provider)
    if reason:
        raise EvalModelConfigurationError(
            f"评测模型 {model_name} 不可用（供应商 {provider}）：{reason}"
        )
    return model_name, provider


def get_token_usage() -> dict[str, int]:
    """返回累计的 token 消耗。"""
    return dict(_token_usage)


def reset_token_usage() -> None:
    """重置 token 计数器（新一轮评测前调用）。"""
    _token_usage["prompt_tokens"] = 0
    _token_usage["completion_tokens"] = 0
    _evaluator_token_usage.update(prompt_tokens=0, completion_tokens=0, llm_calls=0)


def add_evaluator_tokens(prompt_tokens: int, completion_tokens: int) -> None:
    """累加 evaluator 侧（judge/RAGAS）LLM 调用的 token 用量。线程安全（GIL 下 +=）。"""
    _evaluator_token_usage["prompt_tokens"] += int(prompt_tokens or 0)
    _evaluator_token_usage["completion_tokens"] += int(completion_tokens or 0)
    _evaluator_token_usage["llm_calls"] += 1


def get_evaluator_token_usage() -> dict[str, int]:
    """返回 evaluator 侧（judge/RAGAS）累计 token 用量与调用次数。"""
    return dict(_evaluator_token_usage)


class EvaluatorTokenCallback:
    """LangChain 模型级回调：把 evaluator LLM 每次调用的 token 计入评测统计。

    用于 RAGAS（LangchainLLMWrapper 内部调用，拿不到原始返回值）——
    挂在 ChatOpenAI/ChatOllama 的 callbacks 上，on_llm_end 时提取 usage。
    计量失败绝不抛错（不影响评分主流程）。
    """

    def __init__(self):
        from langchain_core.callbacks import BaseCallbackHandler

        outer = self

        class _Handler(BaseCallbackHandler):
            def on_llm_end(self, response, **kwargs) -> None:
                try:
                    usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
                    p = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                    c = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                    if not (p or c):
                        # ChatOllama 等把 usage 放在 generations 消息的 usage_metadata
                        gens = getattr(response, "generations", None) or []
                        msg = getattr(gens[0][0], "message", None) if gens and gens[0] else None
                        um = getattr(msg, "usage_metadata", None) or {}
                        p = um.get("input_tokens") or 0
                        c = um.get("output_tokens") or 0
                    if p or c:
                        add_evaluator_tokens(int(p), int(c))
                except Exception:
                    pass

        self._handler = _Handler()

    def as_handler(self):
        return self._handler


def _make_chat(model: str | None = None, base_url: str | None = None, temperature: float = 0.1):
    # 只有显式传入本地参数的兼容调用才直接构造 Ollama；角色默认路径统一
    # 经过 infra/llm，才能复用 DB 供应商、密钥和协议适配。
    if model is not None or base_url is not None:
        if not OLLAMA_ENABLED:
            raise EvalModelConfigurationError(
                "Ollama 当前未启用，不能使用显式本地评测模型"
            )
        from langchain_ollama import ChatOllama

        key = (model or _configured_eval_model(), base_url or OLLAMA_BASE_URL, temperature)
        cached = _chat_cache.get(key)
        if cached is not None:
            return cached
        chat = ChatOllama(
            model=key[0],
            temperature=key[2],
            base_url=key[1],
        )
        _chat_cache[key] = chat
        return chat

    _model_name, provider = resolve_eval_model()
    from backend.infra.llm import proxy as llm_proxy

    chat = llm_proxy.get_llm_for_role("eval_gen")
    # 评测问题生成使用略高温度；bind 不改变供应商实例缓存，凭据轮换仍由
    # registry_store 统一清理缓存。
    if temperature != 0.1 and hasattr(chat, "bind"):
        return chat.bind(temperature=temperature)
    return chat


def _invoke_chat(
    prompt: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.1,
) -> str:
    """调用评测角色客户端，累计 token 用量，返回文本。"""
    global _ollama_skip_logged
    if model is None and base_url is None:
        try:
            _model_name, provider = resolve_eval_model()
        except EvalModelConfigurationError as exc:
            logger.warning("[EvalGen] %s", exc)
            return ""
        if provider == "ollama" and not OLLAMA_ENABLED:
            if not _ollama_skip_logged:
                logger.info("[Ollama] 本地 Ollama 未启用，评测生成走降级路径")
                _ollama_skip_logged = True
            return ""
    elif not OLLAMA_ENABLED:
        if not _ollama_skip_logged:
            logger.info("[Ollama] 本地 Ollama 未启用，显式本地评测调用被跳过")
            _ollama_skip_logged = True
        return ""
    from langchain_core.messages import HumanMessage

    chat = _make_chat(model, base_url, temperature)
    try:
        response = chat.invoke([HumanMessage(content=prompt)])
        usage = getattr(response, "usage_metadata", None) or {}
        _token_usage["prompt_tokens"] += usage.get("input_tokens", 0)
        _token_usage["completion_tokens"] += usage.get("output_tokens", 0)
        return response.content.strip()
    except Exception as e:
        logger.warning(f"[EvalGen] 调用失败（provider={provider if model is None and base_url is None else 'ollama'}）: {e}")
        return ""


def _build_prompt(question: str, context: list[str]) -> str:
    """构造生成 prompt — 复用生产 prompt 模板链。

    2026-09-11 口径统一：评测专用硬编码 prompt 与生产链漂移，导致
    S6/S7/citation 等 answer 类指标度量的不是线上真实答案质量。
    现按生产链装配方式复刻：rag.qa 模板 system 段（含 {context}）+
    rag.document Evidence 格式 + human 段（{input}）。
    模板渲染失败时回退旧硬编码 prompt（保底可用）。
    """
    try:
        from backend.prompts.service import prompt_service

        full = prompt_service.get_template_sync("rag.qa")
        parts = full.split("---", 1)
        doc_tpl = prompt_service.get_template_sync("rag.document")
        docs = []
        for i, c in enumerate(context, 1):
            docs.append(doc_tpl.format(
                index=i, query_label="", doc_label="", section_label="",
                chunk_label="", type_label="", domain_label="",
                page_content=c,
            ))
        evidence = "\n\n---\n\n".join(docs) if docs else "（无检索结果）"

        if len(parts) == 2:
            system_text = parts[0].strip()
            human_text = parts[1].strip()
            return (
                system_text.replace("{context}", evidence)
                + "\n\n"
                + human_text.replace("{input}", question)
            )
        # 模板无 "---" 分隔符：整段作为 prompt 正文，{context}/{input} 均在其中
        return full.strip().replace("{context}", evidence).replace("{input}", question)
    except Exception:
        pass
    # 回退：旧评测专用 prompt
    context_text = "\n---\n".join(context) if context else "（无检索结果）"
    return (
        f"基于以下参考资料回答问题。如果资料不足以回答，请说明。\n\n"
        f"参考资料:\n{context_text}\n\n"
        f"问题: {question}\n\n"
        f"答案:"
    )


def _invoke_cloud(prompt: str) -> str:
    """兼容旧调用名，实际走 eval_gen 角色绑定的数据库供应商。"""
    return _invoke_chat(prompt)


def _make_cloud_chat(api_key: str | None = None):
    """兼容旧调用名；API Key/base URL/model 均从 eval_gen 数据库绑定解析。"""
    del api_key
    return _make_chat(temperature=0.1)


def _strip_meta(raw: str) -> str:
    """剥离生产模型输出的 META 注释块（前端解析用，评测指标不需要）。"""
    import re as _re
    return _re.sub(r"<!--META.*?-->", "", raw, flags=_re.S).strip()


def generate_answer(
    question: str,
    context: list[str],
    *,
    allow_cloud: bool = False,
) -> str:
    """统一生成入口。

    allow_cloud=True（评测显式 opt-in --judge/--ragas）时，使用 eval_gen
    角色绑定的供应商；未显式允许云调用时保持 cloud 模式不生成答案的旧口径。
    """
    prompt = _build_prompt(question, context)
    try:
        _model_name, provider = resolve_eval_model()
    except EvalModelConfigurationError as exc:
        logger.warning("[EvalGen] %s", exc)
        return ""
    if provider != "ollama" and not allow_cloud:
        logger.info("[EvalGen] 云端评测模型未获得显式 opt-in，跳过答案生成")
        return ""
    if provider == "ollama" and not OLLAMA_ENABLED:
        logger.warning("[EvalGen] 已绑定本地 Ollama 模型，但 Ollama 当前未启用")
        return ""
    raw = _invoke_chat(prompt)
    return _strip_meta(raw) if raw else raw


def get_eval_chat_model(*, temperature: float = 0.0):
    """返回评测/RAGAS 使用的已绑定模型实例。"""
    resolve_eval_model()
    return _make_chat(temperature=temperature)


def generate_answer_ollama(
    question: str,
    context: list[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> str:
    """兼容入口：通过显式本地参数或 eval_gen 角色生成答案。

    Args:
        question: 用户问题
        context: 检索到的上下文片段列表
        model: Ollama 模型名（可选；不再隐式回落 qwen2.5:3b）
        base_url: Ollama 服务地址（默认取 config.OLLAMA_BASE_URL，跟随 OLLAMA_BASE_URL 环境变量）

    Returns:
        生成的答案文本（已剥离 META 注释块），失败时返回空字符串。
    """
    prompt = _build_prompt(question, context)
    raw = _invoke_chat(prompt, model=model, base_url=base_url, temperature=0.1)
    if not raw:
        return raw
    # 剥离生产模型输出的 META 注释块（前端解析用，评测指标不需要）
    import re as _re
    return _re.sub(r"<!--META.*?-->", "", raw, flags=_re.S).strip()


def answer_relevancy_llm(
    question: str,
    answer: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> float:
    """RAGAS-style 答案相关性 — LLM 从答案生成 N 个问题，取 embedding cosine 均值。

    方法：
    1. 让 LLM 从答案反向生成 3 个可能的问题
    2. 用 embedding 模型计算原始问题与生成问题的 cosine 相似度
    3. 取均值作为答案相关性分数

    Returns:
        0~1 的相关性分数，失败时返回 0.0。
    """
    if not answer.strip():
        return 0.0

    generated_questions = _generate_questions_from_answer(
        answer, n=3, model=model, base_url=base_url,
    )
    if not generated_questions:
        return 0.0

    try:
        from backend.evaluation.semantic import get_eval_scorer
        scorer = get_eval_scorer()
        scores = scorer.score_pairs(
            [question] * len(generated_questions),
            generated_questions,
        )
        return round(sum(scores) / len(scores), 4)
    except Exception as e:
        logger.warning(f"[Ollama] 答案相关性计算失败: {e}")
        return 0.0


def _generate_questions_from_answer(
    answer: str,
    *,
    n: int = 3,
    model: str | None = None,
    base_url: str | None = None,
) -> list[str]:
    """让 LLM 从答案反向生成 N 个问题。"""
    prompt = (
        f"根据以下答案，生成 {n} 个可能导致该答案的问题。\n"
        f"每行一个问题，不要编号，不要其他内容。\n\n"
        f"答案: {answer}\n\n"
        f"问题:"
    )
    text = _invoke_chat(prompt, model=model, base_url=base_url, temperature=0.7)
    if not text:
        return []
    questions = [
        q.strip().lstrip("0123456789.、）) ")
        for q in text.split("\n")
        if q.strip() and len(q.strip()) > 4
    ]
    return questions[:n]
