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


def extract_chunk_keywords_qwen(text: str, top_k: int = 5) -> tuple:
    """Chunk 级 LLM 关键词提取 — 使用本地 Qwen2.5:3b（Ollama），免费无消耗。

    仅对 LLM_FORCED_TYPES 文档的 chunk 调用，返回 (keyword_list, model_name)。
    失败时返回空列表，不影响索引流程。
    """
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
        prompt = f"""从以下文档片段提取 {top_k} 个以内的关键词（电商/企业场景）。
要求：只提取文档中出现的核心术语、品类、品牌、条款、指标名
禁止：停用词、通用动词
输出：纯 JSON 数组，不要任何说明

片段：
{safe_text}

输出："""

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

    路由逻辑：DOC_LLM_MODEL 有值 → 本地 Ollama；否则 → _LLMProxy（DeepSeek/当前模型）。
    """
    from backend.config.rag import DOC_LLM_MODEL

    if DOC_LLM_MODEL:
        return _extract_doc_keywords_ollama(text, top_k, DOC_LLM_MODEL)
    return _extract_doc_keywords_proxy(text, top_k)


def _extract_doc_keywords_ollama(text: str, top_k: int, model: str) -> tuple:
    """本地 Ollama 提取文档级关键词（免费，zero cost）。"""
    import json as _json
    from langchain_ollama import ChatOllama

    safe_text = text[:6000]
    prompt = f"""你是跨境电商 RAG 系统的关键词提取助手。

从以下文档中提取 {top_k} 个以内的高质量检索关键词，用于电商场景的语义搜索。

要求:
- 关键词必须是文档中出现的核心术语、品类、品牌、属性、政策条款
- 输出格式: 纯 JSON 数组，不要额外说明

文档:
{safe_text}

输出:"""
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
        # 本地模型 token 不可计量
        tokens = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0}
        logger.info(f"[DocLLM Ollama] {model} 提取 {len(kw_dicts)} 个关键词")
        return kw_dicts, tokens
    except Exception as e:
        logger.warning(f"[DocLLM Ollama] 失败: {e}")
        return [], {}


def _extract_doc_keywords_proxy(text: str, top_k: int) -> tuple:
    """通过 _LLMProxy 提取文档级关键词（Cloud API，含 token 计量）。"""
    from backend.infra.llm import llm
    safe_text = text.encode("utf-8", errors="ignore")[:6000].decode("utf-8", errors="ignore")
    prompt = f"""你是跨境电商 RAG 系统的关键词提取助手。

从以下文档中提取 {top_k} 个以内的高质量检索关键词，用于电商场景的语义搜索。

要求:
- 关键词必须是文档中出现的核心术语、品类、品牌、属性、政策条款
- 优先提取: 商品类目、品牌名、合规条款、费用项、时效要求
- 禁止提取: 停用词、通用动词（"需要""包括""进行"等）
- 输出格式: 纯 JSON 数组，不要额外说明

文档:
{safe_text}

输出:"""
    try:
        # 清理旧 token 记录
        from backend.infra.llm.proxy import _last_call_meta
        for k in list(_last_call_meta.keys()):
            _last_call_meta.pop(k, None)

        result = llm.invoke(prompt)
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

        # 读取 token + 花费
        tokens = {
            "prompt_tokens": _last_call_meta.get("prompt_tokens", 0),
            "completion_tokens": _last_call_meta.get("completion_tokens", 0),
            "cost_usd": _last_call_meta.get("cost_usd", 0),
        }
        kw_dicts = [{"word": w, "source": "llm"} for w in kws[:top_k]]
        logger.info(f"[LLM Keywords] 提取 {len(kw_dicts)} 个, tokens: {tokens}")
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
                                top_k: int = 10) -> KeywordResult:
    """关键词提取 + LLM Decision Router 评分决策。"""
    if complexity is None:
        complexity = {}
    result = KeywordResult()

    rule_words = extract_rule_keywords(text, top_k=top_k, doc_type=doc_type)
    result.rule_keywords = [{"word": w, "source": "rule"} for w in rule_words]

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
    else:
        result.llm_decision = {"llm_used": False, "llm_score": llm_score,
                               "llm_reason": f"score={llm_score}<{LLM_SCORE_THRESHOLD}"}

    return result

# 向后兼容：extract_doc_keywords 现在返回 KeywordResult
def extract_doc_keywords(text: str, top_k: int = 10) -> KeywordResult:
    """向后兼容别名 — 默认 general 类型，规则 + LLM 双线"""
    return extract_doc_keywords_typed(text, doc_type="general", top_k=top_k)
