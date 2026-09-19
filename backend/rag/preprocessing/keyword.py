"""关键词提取 — 规则 + jieba + LLM（按文档类型分流）

电商场景分流策略:
  - faq / product_spec → 规则优先，< 3 个命中补 LLM
  - policy / compliance / legal → 强制 LLM，规则作补充
  - general / 其他 → 规则 + LLM 双线，标注来源

关键词规则: 动态管理 → 从 keyword_store 热加载（60s TTL），替代 config 写死。
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Set, Dict

import jieba.analyse

from backend.config import DOMAIN_RULES, blacklist
from backend.shared.logger import logger

# LLM Decision Router 评分阈值
LLM_SCORE_THRESHOLD = 50      # >= 50 → 调 LLM
LLM_FORCED_TYPES = {
    "policy", "compliance", "legal",           # 原有高风险
    "security", "financial",                   # 企业安全/财务
    "customer_data", "contract_template",       # 客户数据/合同模板
}
LLM_FALLBACK_TYPES = {"faq", "product_spec", "listing", "sop"}


@dataclass
class KeywordResult:
    """关键词提取结果，区分来源"""
    rule_keywords: List[dict] = field(default_factory=list)   # [{"word": "...", "source": "rule"}, ...]
    llm_keywords: List[dict] = field(default_factory=list)    # [{"word": "...", "source": "llm"}, ...]
    llm_tokens: Dict[str, int] = field(default_factory=dict)  # {"prompt_tokens": N, "completion_tokens": M}
    llm_strategy: str = ""                                     # "rule_first" | "llm_force" | "dual_merge"
    llm_decision: dict = field(default_factory=dict)           # {"llm_used": bool, "llm_score": int, "llm_reason": str}

    def all_keywords(self) -> List[str]:
        """合并去重，只返回词条字符串列表（兼容旧调用方）"""
        seen = set()
        result = []
        for kw in self.rule_keywords + self.llm_keywords:
            w = kw["word"]
            if w not in seen:
                seen.add(w)
                result.append(w)
        return result

# =====================================================
# 预编译正则 — 领域词列表变化少，编译一次复用
# =====================================================
_re_domain_kw = re.compile(
    '|'.join(re.escape(kw) for rules in DOMAIN_RULES.values() for kw in rules),
    re.IGNORECASE
)


@lru_cache(maxsize=512)
def extract_chunk_keywords_cached(text: str, top_k: int = 6, doc_type: str = "general") -> List[str]:
    """提取 chunk 级别关键词（带缓存，动态词库，按 doc_type 过滤）"""
    keywords: Set[str] = set()
    text_lower = text.lower()

    # 从动态存储加载关键词（60s 缓存，支持热更新）
    try:
        from backend.rag.preprocessing.keyword_store import get_keyword_store
        store = get_keyword_store()
        keywords_for_type = store.get_keywords_for_doc_type(doc_type)
        active = store.get_active()
        signal_rules = active.get("signal_rules", {})
    except Exception:
        keywords_for_type = []
        signal_rules = {}

    # 1. 动态关键词匹配（只匹配该文档类型的词 + 通用词）
    if keywords_for_type:
        _re_dynamic = re.compile(
            "|".join(re.escape(kw) for kw in keywords_for_type if len(kw) > 1),
            re.IGNORECASE,
        )
        keywords.update(_re_dynamic.findall(text_lower))

    # 2. 领域词匹配
    keywords.update(_re_domain_kw.findall(text_lower))

    # 3. 信号词检测（动态加载）
    for signal_name, signal_kws in signal_rules.items():
        if any(kw.lower() in text_lower for kw in signal_kws):
            keywords.add(signal_name)

    # 4. jieba 补充
    try:
        extra = jieba.analyse.extract_tags(text, topK=top_k)
        keywords.update(extra)
    except Exception as e:
        logger.debug(f"jieba关键词提取异常: {e}")

    # 黑名单过滤 + 单字过滤 + 数字/Markdown 过滤
    def _is_junk(kw: str) -> bool:
        kw_stripped = kw.strip()
        if len(kw_stripped) <= 1:
            return True
        if kw_stripped in blacklist:
            return True
        if kw_stripped.isdigit():
            return True
        if re.match(r'^#+$', kw_stripped):          # 纯 Markdown 标题标记
            return True
        if re.match(r'^[\d\s.,;:!?，。；：！？、""''（）()]+$', kw_stripped):  # 纯数字+标点
            return True
        return False

    result = [k for k in keywords if not _is_junk(k)]
    return result[:top_k]


def extract_chunk_keywords(text: str, top_k: int = 6) -> List[str]:
    """提取 chunk 级别关键词（入口函数）"""
    return extract_chunk_keywords_cached(text, top_k)


def extract_rule_keywords(text: str, top_k: int = 10, doc_type: str = "general") -> List[str]:
    """纯规则关键词（不调 LLM）— 正则 + jieba + 电商词库"""
    return extract_chunk_keywords_cached(text, top_k=top_k, doc_type=doc_type)

# 向后兼容别名
extract_doc_keywords_rule = extract_rule_keywords


# Ollama 可用性缓存：避免每次索引都重试连接
_ollama_cache: dict[str, bool] = {}

def _ollama_available(model_name: str) -> bool:
    """检查 Ollama 服务是否可用 + 模型是否已拉取。结果缓存 5 分钟。

    ENV_MODE=cloud 时直接返回 False，不发起本地连接。
    """
    import time, requests

    from backend.config.llm import OLLAMA_BASE_URL, OLLAMA_ENABLED

    now = time.time()
    cached = _ollama_cache.get('_ts', 0)
    if now - cached < 300 and model_name in _ollama_cache:
        return _ollama_cache[model_name]
    if not OLLAMA_ENABLED:
        available = False
        logger.info("[ChunkLLM] ENV_MODE=cloud，本地 Ollama 已禁用，chunk LLM 关键词走规则降级")
    else:
        try:
            r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2)
            if r.status_code == 200:
                models = [m.get("name", "") for m in r.json().get("models", [])]
                available = any(model_name in m or model_name.split(':')[0] in m for m in models)
            else:
                available = False
        except Exception:
            available = False
        if not available:
            logger.info(f"[ChunkLLM] Ollama 不可用或模型 {model_name} 未找到，chunk LLM 关键词禁用")
    _ollama_cache['_ts'] = now
    _ollama_cache[model_name] = available
    return available


def extract_chunk_keywords_qwen_batch(chunks: list[str], top_k: int = 5) -> tuple:
    """批量 Chunk LLM 关键词提取 — 一次 prompt 处理多个 chunk（省 N-1 次调用）。

    Returns: ([[kw1, kw2, ...], ...], model_name)
    """
    from backend.config.rag import CHUNK_LLM_MODEL
    import json as _json

    model_name = CHUNK_LLM_MODEL

    # 健康检查：Ollama 不可用则直接跳过，不浪费超时时间
    if not _ollama_available(model_name):
        logger.info(f'[ChunkLLM] Ollama 不可用，跳过 {len(chunks)} chunks 的 LLM 关键词提取')
        return [[] for _ in chunks], model_name

    from langchain_ollama import ChatOllama
    chunk_texts = [f"<chunk id={i}>{t[:800]}</chunk>" for i, t in enumerate(chunks)]
    all_chunks = "\n".join(chunk_texts)

    from backend.prompts.service import prompt_service
    rendered = prompt_service.render_sync(
        "rag.preprocessing.chunk_batch",
        chunks_count=str(len(chunks)), top_k=str(top_k), all_chunks=all_chunks,
    )
    prompt = rendered.text

    try:
        llm = ChatOllama(model=model_name, temperature=0.0, num_ctx=4096, request_timeout=60)
        result = llm.invoke(prompt)
        content = result.content.strip() if hasattr(result, "content") else str(result).strip()

        # 清洗 markdown 围栏
        content = re.sub(r'^\s*$', '', content, flags=re.MULTILINE)
        content = content.strip()

        # 尝试多种 JSON 格式
        parsed = None
        for pattern in [r'\[\[.*\]\]', r'\[.*\]']:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                try:
                    parsed = _json.loads(match.group())
                    break
                except Exception:
                    continue

        if parsed and isinstance(parsed, list):
            keywords_per_chunk = []
            for items in parsed:
                if isinstance(items, list):
                    kws = [str(k).strip() for k in items if str(k).strip() and len(str(k).strip()) > 1][:top_k]
                else:
                    kws = []
                keywords_per_chunk.append(kws)
            while len(keywords_per_chunk) < len(chunks):
                keywords_per_chunk.append([])
            total = sum(len(k) for k in keywords_per_chunk)
            if total > 0:
                logger.info(f"[ChunkLLM Batch] {model_name} → {len(chunks)} chunks, 总计 {total} 关键词")
            else:
                logger.warning(f"[ChunkLLM Batch] 解析成功但无有效关键词，raw: {content[:200]}")
            return keywords_per_chunk, model_name

        logger.warning(f"[ChunkLLM Batch] JSON 解析失败，raw: {content[:200]}")
        return [[] for _ in chunks], model_name
    except Exception as e:
        logger.warning(f"[ChunkLLM Batch] 失败: {e}")
        return [[] for _ in chunks], model_name


def extract_chunk_keywords_qwen(text: str, top_k: int = 5) -> tuple:
    """Chunk 级 LLM 关键词提取 — 使用本地 Qwen2.5:3b（Ollama），免费无消耗。

    仅对 LLM_FORCED_TYPES 文档的 chunk 调用，返回 (keyword_list, model_name)。
    失败时返回空列表，不影响索引流程。
    """
    from backend.config.llm import OLLAMA_ENABLED

    if not OLLAMA_ENABLED:
        return [], "disabled"

    try:
        from backend.config.rag import CHUNK_LLM_MODEL
    except ImportError:
        CHUNK_LLM_MODEL = "qwen2.5:3b"  # type: ignore[assignment]
    model_name: str = CHUNK_LLM_MODEL

    try:
        from langchain_ollama import ChatOllama
        from backend.config import LLM_TEMPERATURE
        import json as _json

        safe_text = text[:1500]
        from backend.prompts.service import prompt_service
        prompt = prompt_service.render_sync(
            "rag.preprocessing.chunk_single",
            top_k=str(top_k), safe_text=safe_text,
        ).text

        llm = ChatOllama(
            model=model_name,
            temperature=0.0,
            num_ctx=2048,
            request_timeout=30,
        )
        result = llm.invoke(prompt)
        content = result.content.strip() if hasattr(result, "content") else str(result).strip()

        match = re.search(r"\[.*?\]", content, re.DOTALL)
        if match:
            kws = _json.loads(match.group())
            keywords = [str(k).strip() for k in kws if str(k).strip() and len(str(k).strip()) > 1][:top_k]
        else:
            keywords = [w.strip() for w in content.replace('"', "").replace("'", "").split(",") if w.strip()][:top_k]

        logger.debug(f"[ChunkLLM] {model_name} 提取 {len(keywords)} 个关键词: {keywords}")
        return keywords, model_name

    except Exception as e:
        logger.warning(f"[ChunkLLM] 提取失败（非致命）: {e}")
        return [], model_name


def extract_doc_keywords_llm(text: str, top_k: int = 10) -> tuple:
    """LLM 关键词提取 — 返回 (keyword_dicts, token_dict)。

    路由逻辑：DOC_LLM_MODEL 有值且 ENV_MODE=local → 本地 Ollama；否则 → _LLMProxy（DeepSeek/当前模型）。
    """
    from backend.config.llm import OLLAMA_ENABLED
    from backend.config.rag import DOC_LLM_MODEL

    if DOC_LLM_MODEL and OLLAMA_ENABLED:
        return _extract_doc_keywords_ollama(text, top_k, DOC_LLM_MODEL)
    return _extract_doc_keywords_proxy(text, top_k)


def _extract_doc_keywords_ollama(text: str, top_k: int, model: str) -> tuple:
    """本地 Ollama 提取文档级关键词（免费，zero cost）。"""
    import json as _json
    from langchain_ollama import ChatOllama

    safe_text = text[:6000]
    from backend.prompts.service import prompt_service
    prompt = prompt_service.render_sync(
        "rag.preprocessing.doc_ollama",
        top_k=str(top_k), safe_text=safe_text,
    ).text
    try:
        llm = ChatOllama(model=model, temperature=0.0, num_ctx=4096, request_timeout=60)
        result = llm.invoke(prompt)
        content = result.content.strip() if hasattr(result, "content") else str(result).strip()
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            kws = _json.loads(match.group())
            kws = [str(k).strip() for k in kws if str(k).strip() and len(str(k).strip()) > 1][:top_k]
        else:
            kws = [w.strip() for w in content.replace('"','').replace("'","").split(",") if w.strip()][:top_k]
        kw_dicts = [{"word": w, "source": "llm"} for w in kws]
        tokens = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0, "model": model}
        logger.info(f"[DocLLM Ollama] {model} 提取 {len(kw_dicts)} 个关键词")
        return kw_dicts, tokens
    except Exception as e:
        logger.warning(f"[DocLLM Ollama] 失败: {e}")
        return [], {}


def _extract_doc_keywords_proxy(text: str, top_k: int) -> tuple:
    """通过 _LLMProxy 提取文档级关键词（Cloud API，含 token 计量）。"""
    from backend.infra.llm import llm
    from backend.rag.preprocessing.llm_enrichment import invoke_metadata_llm
    safe_text = text.encode("utf-8", errors="ignore")[:6000].decode("utf-8", errors="ignore")
    from backend.prompts.service import prompt_service
    prompt = prompt_service.render_sync(
        "rag.preprocessing.doc_proxy",
        top_k=str(top_k), safe_text=safe_text,
    ).text

    try:
        # 清理旧 token 记录（ContextVar 不可变替换）
        from backend.infra.llm.proxy import _last_call_meta_var
        _last_call_meta_var.set({})

        result = invoke_metadata_llm(prompt)
        content = result.content.strip() if hasattr(result, "content") else str(result).strip()

        # 提取 JSON 数组
        import json
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            keywords = json.loads(match.group())
            if isinstance(keywords, list):
                kws = [str(k).strip() for k in keywords if str(k).strip() and len(str(k).strip()) > 1]
            else:
                kws = []
        else:
            kws = [w.strip() for w in content.replace('"', '').replace("'", "").split(",") if w.strip()]

        # 读取 token + 花费 + 模型名（模型名以 proxy 记录的实际模型为准——
        # 运行时切换/按请求覆盖时 LLM_MODEL 不等于真实调用的模型）
        from backend.config import LLM_MODEL
        _meta = _last_call_meta_var.get()
        actual_model = _meta.get("model") or LLM_MODEL
        tokens = {
            "prompt_tokens": _meta.get("prompt_tokens", 0),
            "completion_tokens": _meta.get("completion_tokens", 0),
            "cost_usd": _meta.get("cost_usd", 0),
            "model": actual_model,
        }
        kw_dicts = [{"word": w, "source": "llm"} for w in kws[:top_k]]
        logger.info(f"[LLM Keywords] {actual_model} 提取 {len(kw_dicts)} 个, tokens: {tokens}")
        return kw_dicts, tokens

    except Exception as e:
        logger.warning(f"[LLM Keywords] 失败，回退规则: {e}")
        return [], {}


def extract_doc_keywords(text: str, top_k: int = 10) -> KeywordResult:
    """文档级关键词提取 — 按 doc_type 自动分流（兼容旧调用方）。

    需调用方自行传入 doc_type → 使用 extract_doc_keywords_typed()
    """
    return extract_doc_keywords_typed(text, doc_type="general", top_k=top_k)


def _compute_llm_score(doc_type: str, confidence: float, complexity: dict) -> tuple[int, list[str]]:
    """LLM Decision Router V2 — 企业级评分模型。

    评分维度:
      ① 文档价值 (high_value)   → +40
      ② 风险信号 (risk_hits)    → +10~20  (↓ 从 15~30 下调)
      ③ 分类置信 (low_conf)     → +20
      ④ 文档长度 (long)         → +15
      ⑤ 结构复杂度 (complex)    → +15
      ultra_long (>50k token)   → 不加分, 标记 section_summary_needed
      阈值: 50
    """
    score = 0
    reasons: list[str] = []
    tok = complexity.get("token_estimate", 0)

    # ① 文档价值（高价值类型强制 LLM）
    if doc_type in LLM_FORCED_TYPES:
        score += 40; reasons.append(f"high_value:{doc_type}(+40)")

    # ② 风险关键词命中: 只作为风险信号，不直接代表文档复杂度
    risk_hits = complexity.get("risk_keyword_hits", 0)
    if risk_hits >= 3:
        score += 20; reasons.append(f"risk_hits:{risk_hits}(+20)")
    elif risk_hits >= 1:
        score += 10; reasons.append(f"risk_hits:{risk_hits}(+10)")

    # ③ 分类不确定 — 兜底
    if confidence < 0.7:
        score += 20; reasons.append(f"low_conf:{confidence}(+20)")

    # ④ 文档长度
    if tok > 50000:
        reasons.append(f"ultra_long:{tok}tok")
        complexity["section_summary_needed"] = True   # 供后续章节级摘要
    elif tok > 10000:
        score += 15; reasons.append(f"long:{tok}tok(+15)")

    # ⑤ 结构复杂度
    struct = complexity.get("structure_score", 0)
    if struct >= 20:
        score += 15; reasons.append(f"complex_struct:{struct}(+15)")

    return score, reasons


def extract_doc_keywords_typed(text: str, doc_type: str = "general",
                                confidence: float = 0.5, complexity: dict | None = None,
                                top_k: int = 10, *, allow_llm: bool = True) -> KeywordResult:
    """关键词提取 + LLM Decision Router 评分决策。"""
    if complexity is None:
        complexity = {}
    result = KeywordResult()

    rule_words = extract_rule_keywords(text, top_k=top_k, doc_type=doc_type)
    result.rule_keywords = [{"word": w, "source": "rule"} for w in rule_words]

    if not allow_llm:
        result.llm_strategy = "deterministic"
        result.llm_decision = {
            "llm_used": False,
            "llm_score": 0,
            "llm_reason": "explicitly_disabled",
        }
        return result

    llm_score, reasons = _compute_llm_score(doc_type, confidence, complexity)
    # 强制类型始终调 LLM
    if doc_type in LLM_FORCED_TYPES:
        result.llm_strategy = "llm_force"
        should_use_llm = True
        reasons.append("forced:high_risk")
    elif doc_type in LLM_FALLBACK_TYPES:
        result.llm_strategy = "rule_first"
        should_use_llm = llm_score >= LLM_SCORE_THRESHOLD
    else:
        result.llm_strategy = "dual_merge"
        should_use_llm = llm_score >= LLM_SCORE_THRESHOLD

    if should_use_llm:
        llm_kws, tokens = extract_doc_keywords_llm(text, top_k=top_k)
        result.llm_keywords = llm_kws
        result.llm_tokens = tokens
        result.llm_decision = {"llm_used": True, "llm_score": llm_score,
                               "llm_reason": "; ".join(reasons)}
    elif len(rule_words) < 3:
        # 兜底：规则关键词太少（<3个），强制调 LLM 确保有基本关键词
        logger.info(f"[Keyword Fallback] 规则仅{len(rule_words)}个关键词，强制LLM")
        llm_kws, tokens = extract_doc_keywords_llm(text, top_k=top_k)
        result.llm_keywords = llm_kws
        result.llm_tokens = tokens
        result.llm_strategy = "llm_fallback"
        reasons.append(f"fallback:rule_sparse({len(rule_words)})")
        result.llm_decision = {"llm_used": True, "llm_score": llm_score,
                               "llm_reason": "; ".join(reasons)}
    else:
        result.llm_decision = {"llm_used": False, "llm_score": llm_score,
                               "llm_reason": f"score={llm_score}<{LLM_SCORE_THRESHOLD}"}

    return result

# 向后兼容：extract_doc_keywords 现在返回 KeywordResult
def extract_doc_keywords(text: str, top_k: int = 10) -> KeywordResult:
    """向后兼容别名 — 默认 general 类型，规则 + LLM 双线"""
    return extract_doc_keywords_typed(text, doc_type="general", top_k=top_k)
