"""config/rag.py — RAG 检索配置

包含 chunk、citation、multi_query、自适应检索、文档分类、清洗等。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ── 业务领域数据（已迁至 preprocessing/domain_data.py，此处 re-export 保持兼容）──
from backend.rag.preprocessing.domain_data import (  # noqa: F401
    KNOWN_PERSON_NAMES, TIME_PATTERNS, DEFAULT_KEYWORDS, SIGNAL_RULES,
    DOC_TYPE_RULES, FILENAME_TYPE_HINTS, FOLDER_TYPE_HINTS, DOMAIN_RULES,
    STOPWORDS,
    FINANCIAL_METRIC_PATTERNS,
    FINANCIAL_NUMERIC_CONDITION_RE,
    FINANCIAL_NUMERIC_CONDITION_RE_LTE,
    FINANCIAL_PII_PATTERNS,
)
# 向后兼容别名
blacklist = STOPWORDS  # noqa: F811

# ====================================
# Chunk 级 LLM 关键词配置
# ====================================
# 使用本地 Ollama 模型做 chunk 级关键词提取（仅 LLM_FORCED_TYPES 文档）
CHUNK_LLM_MODEL = os.getenv("CHUNK_LLM_MODEL", "qwen2.5:3b")

# ====================================
# 文件上传限制 (P0-1 流式上传)
# ====================================
# 单文件最大 50MB,企业可调到 100MB;超过 1GB 应改用对象存储
RAG_MAX_FILE_SIZE = int(os.getenv("RAG_MAX_FILE_SIZE", "50"))  # 单位 MB
# 临时文件目录 (atomic rename 前存这里, Docker 容器内安全)
# 用 RAG_DATA_DIR 派生绝对路径，消除相对路径的 CWD 依赖
from backend.config.database import RAG_DATA_DIR
RAG_TMP_DIR = os.getenv("RAG_TMP_DIR", os.path.join(RAG_DATA_DIR, "rag", "tmp"))
# 流式读块大小 (1MB 平衡内存和 syscall 次数)
RAG_UPLOAD_CHUNK_SIZE = int(os.getenv("RAG_UPLOAD_CHUNK_SIZE", str(1024 * 1024)))
# SSE 进度推送间隔: 每 5MB 或 500ms 触发一次
RAG_UPLOAD_EMIT_BYTES = int(os.getenv("RAG_UPLOAD_EMIT_BYTES", str(5 * 1024 * 1024)))
RAG_UPLOAD_EMIT_MS = int(os.getenv("RAG_UPLOAD_EMIT_MS", "500"))

# 文档级关键词 LLM 模型 — 设了用本地 Ollama（免费），不设走 _LLMProxy（当前 DeepSeek）
DOC_LLM_MODEL = os.getenv("DOC_LLM_MODEL", "")

# ====================================
# Chunk 配置
# ====================================
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))

# 文档类型感知分块
POLICY_MAX_CHUNK_SIZE = int(os.getenv("POLICY_MAX_CHUNK_SIZE", "2000"))
PROJECT_CHUNK_SIZE = int(os.getenv("PROJECT_CHUNK_SIZE", "1500"))
GENERAL_CHUNK_SIZE = int(os.getenv("GENERAL_CHUNK_SIZE", "1000"))
GENERAL_CHUNK_OVERLAP = int(os.getenv("GENERAL_CHUNK_OVERLAP", "100"))

# 切分重构（token 计数 + 语义/LLM 切分开关）
LEAF_CHUNK_TOKENS = int(os.getenv("LEAF_CHUNK_TOKENS", "500"))
PARENT_CHUNK_TOKENS = int(os.getenv("PARENT_CHUNK_TOKENS", "2000"))
STRUCTURE_COMPLETE_THRESHOLD = float(os.getenv("STRUCTURE_COMPLETE_THRESHOLD", "0.7"))
ENABLE_SEMANTIC_CHUNKING = os.getenv("ENABLE_SEMANTIC_CHUNKING", "false").lower() == "true"
ENABLE_LLM_CHUNKING = os.getenv("ENABLE_LLM_CHUNKING", "false").lower() == "true"
LLM_CHUNK_MIN_CHARS = int(os.getenv("LLM_CHUNK_MIN_CHARS", "2000"))

# Semantic 语义切分（Phase 3）：仅对「无结构长文档」启用，避免短文档过度调用 embedding
# 文档 token 数超过此阈值才走语义切分，否则递归切分足够
SEMANTIC_CHUNK_MIN_TOKENS = int(os.getenv("SEMANTIC_CHUNK_MIN_TOKENS", "500"))
# 相邻句子余弦相似度低于此阈值视为主题边界（语义断层）
SEMANTIC_SIMILARITY_THRESHOLD = float(os.getenv("SEMANTIC_SIMILARITY_THRESHOLD", "0.45"))
# 语义切分句子级 embedding 加固：分批大小 + 每批重试次数（对齐 indexer EMBED_RETRY_MAX 模式）
SEMANTIC_EMBED_BATCH_SIZE = int(os.getenv("SEMANTIC_EMBED_BATCH_SIZE", "32"))
SEMANTIC_EMBED_RETRY = int(os.getenv("SEMANTIC_EMBED_RETRY", "3"))
# 索引主路径 embedding 批量化（P2）：embed_documents 批调用走本地模型矩阵运算，
# 比逐条 embed_query 快数倍；批失败降级逐条以隔离失败点
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "32"))
# 单文档 chunk 数量上限（生产防护）：异常超长/解析失控文档超限时截断 + 告警，
# 防止无界产出撑爆 embedding/向量库
MAX_CHUNKS_PER_DOC = int(os.getenv("MAX_CHUNKS_PER_DOC", "5000"))

# ====================================
# Knowledge Base 隔离配置
# ====================================
DEFAULT_KB_ID = os.getenv("DEFAULT_KB_ID", "default")

BM25_SEARCH_K = int(os.getenv("BM25_SEARCH_K", "10"))
HYBRID_SEARCH_K = int(os.getenv("HYBRID_SEARCH_K", "8"))
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "8"))
# Rerank 阈值（sigmoid 归一化后）：CrossEncoder 输出的 logit 经 sigmoid 映射到 0-1。
# 0.3 对应 logit ≈ -0.85，可召回弱相关文档。
# 调高 → 更严格（噪音少，召回少）；调低 → 更宽松（召回多，噪音多）。
# 经验值范围：0.2（宽松）~ 0.5（严格）。
RERANK_SCORE_THRESHOLD = float(os.getenv("RERANK_SCORE_THRESHOLD", "0.3"))

# ⭐ NEW: Adaptive Thresholds (Phase 1)
VEC_MIN_SCORE_DEFAULT = float(os.getenv("VEC_MIN_SCORE_DEFAULT", "0.25"))
ADAPTIVE_THRESHOLD_ENABLED = os.getenv("ADAPTIVE_THRESHOLD_ENABLED", "true").lower() == "true"
CONFIDENCE_AGGREGATOR_ENABLED = os.getenv("CONFIDENCE_AGGREGATOR_ENABLED", "true").lower() == "true"
FAQ_VEC_THRESHOLD = float(os.getenv("FAQ_VEC_THRESHOLD", "0.35"))
POLICY_VEC_THRESHOLD = float(os.getenv("POLICY_VEC_THRESHOLD", "0.30"))
FINANCIAL_QUERY_THRESHOLD = float(os.getenv("FINANCIAL_QUERY_THRESHOLD", "0.45"))

# Doc-type specific thresholds matrix
ADAPTIVE_VEC_THRESHOLDS = {
    "faq": FAQ_VEC_THRESHOLD,
    "policy": POLICY_VEC_THRESHOLD,
    "financial": FINANCIAL_QUERY_THRESHOLD,
    "legal": 0.32,
    "general": VEC_MIN_SCORE_DEFAULT,
}

# ⭐ Phase 3 Improvement #1: Risk-level Faithfulness rejection thresholds
FAITHFULNESS_REJECT_SCORE_HIGH_RISK = float(os.getenv("FAITHFULNESS_REJECT_SCORE_HIGH_RISK", "0.7"))
FAITHFULNESS_REJECT_SCORE_MED_RISK = float(os.getenv("FAITHFULNESS_REJECT_SCORE_MED_RISK", "0.5"))
FAITHFULNESS_REJECT_SCORE_LOW_RISK = float(os.getenv("FAITHFULNESS_REJECT_SCORE_LOW_RISK", "0.3"))

# Default threshold (backward compatible with existing config)
FAITHFULNESS_REJECT_SCORE = FAITHFULNESS_REJECT_SCORE_MED_RISK

# Citation Filter: chunk 支撑答案的最低 CrossEncoder 分数
CITATION_SUPPORT_THRESHOLD = float(os.getenv("CITATION_SUPPORT_THRESHOLD", "0.4"))

# ====================================
# MultiQuery 检索配置
# ====================================
# mode: "auto"(自动判断复杂问题) | "always"(强制开启) | "off"(关闭)
MULTI_QUERY_MODE = os.getenv("MULTI_QUERY_MODE", "auto")
MULTI_QUERY_COUNT = int(os.getenv("MULTI_QUERY_COUNT", "3"))
MULTI_QUERY_TEMPERATURE = float(os.getenv("MULTI_QUERY_TEMPERATURE", "0.2"))
MULTI_QUERY_MAX_TOKENS = int(os.getenv("MULTI_QUERY_MAX_TOKENS", "200"))
MULTI_QUERY_TOP_K_PER = int(os.getenv("MULTI_QUERY_TOP_K_PER", "5"))
MULTI_QUERY_DEDUP = os.getenv("MULTI_QUERY_DEDUP", "true").lower() == "true"
MULTI_QUERY_SIMILARITY = float(os.getenv("MULTI_QUERY_SIMILARITY", "0.9"))
MULTI_QUERY_MIN_LENGTH = int(os.getenv("MULTI_QUERY_MIN_LENGTH", "3"))

# ====================================
# 自适应检索
# ====================================
ADAPTIVE_CLUSTER_THRESHOLD = float(os.getenv("ADAPTIVE_CLUSTER_THRESHOLD", "0.3"))
ADAPTIVE_MAX_CLUSTER_DOCS = int(os.getenv("ADAPTIVE_MAX_CLUSTER_DOCS", "2"))
# 自适应 K 扩展：覆盖面不足时自动扩大 Top-K
ADAPTIVE_RETRIEVAL_ENABLED = os.getenv("ADAPTIVE_RETRIEVAL_ENABLED", "true").lower() == "true"
ADAPTIVE_MIN_CHUNKS = int(os.getenv("ADAPTIVE_MIN_CHUNKS", "1"))         # 覆盖文档数/有效 chunk 低于此触发扩展
ADAPTIVE_K_STEPS = [int(x) for x in os.getenv("ADAPTIVE_K_STEPS", "8,12,16").split(",")]  # 扩展步长

# 摘要长度限制
SUMMARY_MAX_LENGTH = 250

# 以下业务数据已在 domain_data.py 定义，通过顶部 re-export 保持兼容
# ====================================
# Plan Critique + Resource Monitor
# ====================================
ENABLE_PLAN_CRITIQUE = True    # 启用 Plan Critique 自我纠错
# Context Filter 最低相关度阈值（agents/reporter/context_filter.py 使用）
RERANKER_THRESHOLD = float(os.getenv("RERANKER_THRESHOLD", "0.35"))
ENABLE_RESOURCE_MONITOR = os.getenv("ENABLE_RESOURCE_MONITOR", "true").lower() == "true"

# ====================================
# 文档清洗配置
# ====================================
CLEAN_REMOVE_CONTROL_CHARS = os.getenv("CLEAN_REMOVE_CONTROL_CHARS", "false").lower() == "true"
CLEAN_NORMALIZE_FULLWIDTH = os.getenv("CLEAN_NORMALIZE_FULLWIDTH", "false").lower() == "true"
CLEAN_MERGE_BLANK_LINES = os.getenv("CLEAN_MERGE_BLANK_LINES", "false").lower() == "true"
CLEAN_STRIP_HTML = os.getenv("CLEAN_STRIP_HTML", "false").lower() == "true"
CLEAN_REMOVE_PDF_HEADERS = os.getenv("CLEAN_REMOVE_PDF_HEADERS", "false").lower() == "true"
CLEAN_REMOVE_PDF_FOOTERS = os.getenv("CLEAN_REMOVE_PDF_FOOTERS", "false").lower() == "true"
CLEAN_URL_ACTION = os.getenv("CLEAN_URL_ACTION", "keep")
CLEAN_EMAIL_ACTION = os.getenv("CLEAN_EMAIL_ACTION", "keep")

# ====================================
# 脏数据过滤配置
# ====================================
FILTER_MIN_CHUNK_LENGTH = int(os.getenv("FILTER_MIN_CHUNK_LENGTH", "10"))
FILTER_MAX_SYMBOL_RATIO = float(os.getenv("FILTER_MAX_SYMBOL_RATIO", "0.8"))
FILTER_MIN_CHINESE_RATIO = float(os.getenv("FILTER_MIN_CHINESE_RATIO", "0.3"))
FILTER_SIMHASH_THRESHOLD = int(os.getenv("FILTER_SIMHASH_THRESHOLD", "3"))
FILTER_ENABLE_PII_MASK = os.getenv("FILTER_ENABLE_PII_MASK", "false").lower() == "true"

# ====================================
# Faithfulness 检测（NLI 答案验证）
# ====================================
# 默认 True（对齐企业生产实践，§0.2 对标：Vertex AI / AWS Bedrock / RAGAS），
# 关闭用 ENABLE_FAITHFULNESS=false
ENABLE_FAITHFULNESS = os.getenv("ENABLE_FAITHFULNESS", "true").lower() == "true"
# Faithfulness 跳过阈值：unsupported 比例超过此值时跳过 rewrite
FAITHFULNESS_SKIP_THRESHOLD = float(os.getenv("FAITHFULNESS_SKIP_THRESHOLD", "0.5"))
# 2026-08-11：LLM-as-Judge 开关（Qwen 整体评估，2026-08-12 起为唯一路径）
NLI_USE_LLM = os.getenv("NLI_USE_LLM", "true").lower() == "true"

# ====================================
# Evidence Gate — RAG 主动拒答
# 设计与 RAGFlow / Vertex AI / LangGraph CRAG 对齐
# 详见 docs/architecture/rag-evidence-gate.md
# ====================================

# 总开关：false 时全部 Gate 旁路（与 Faithfulness 默认 true 独立）
EVIDENCE_GATE_ENABLED = os.getenv("EVIDENCE_GATE_ENABLED", "true").lower() == "true"

# --- Retrieval Gate（对齐 RAGFlow 默认 0.2） ---
VEC_MIN_SCORE = float(os.getenv("VEC_MIN_SCORE", "0.2"))
# 是否要求召回 doc_type 覆盖 QueryAnalyzer 推导的 doc_types
DOC_TYPE_COVERAGE_REQUIRED = os.getenv("DOC_TYPE_COVERAGE_REQUIRED", "true").lower() == "true"
# 查询实体覆盖校验（P2，2026-08-21）：问题核心实体（含同义词闭包）不在 top-3 召回
# 文本中 → “主题相近但无答案” → 拒答。离线评测已验证（拒答 75%→100%，正样本零误伤）
GATE_ENTITY_CHECK_ENABLED = os.getenv("GATE_ENTITY_CHECK_ENABLED", "true").lower() == "true"

# --- Rerank Gate（多维，与 RAGFlow 单阈值不同，更严格但有上限控制） ---
RERANK_MIN_TOP1 = float(os.getenv("RERANK_MIN_TOP1", "0.35"))
RERANK_MIN_AVG = float(os.getenv("RERANK_MIN_AVG", "0.25"))
RERANK_MIN_GAP = float(os.getenv("RERANK_MIN_GAP", "0.05"))
# 高风险问题额外要求 top1 提高到 0.55
RERANK_HIGH_RISK_MIN_TOP1 = float(os.getenv("RERANK_HIGH_RISK_MIN_TOP1", "0.55"))

# --- Evaluation Gate（Faithfulness 拒答门槛） ---
# 整体 Faithfulness 分数低于此阈值触发 HALLUCINATION_REJECT
FAITHFULNESS_REJECT_SCORE = float(os.getenv("FAITHFULNESS_REJECT_SCORE", "0.5"))
# 高风险问题更高门槛
HIGH_RISK_REJECT_SCORE = float(os.getenv("HIGH_RISK_REJECT_SCORE", "0.7"))

# --- Self-Correction：拒答后 query rewrite 重试 ---
SELF_CORRECTION_ENABLED = os.getenv("SELF_CORRECTION_ENABLED", "true").lower() == "true"
SELF_CORRECTION_MAX_RETRIES = int(os.getenv("SELF_CORRECTION_MAX_RETRIES", "1"))

# --- KB 反向驱动（cron 任务触发） ---
KNOWLEDGE_GAP_MIN_OCCURRENCES = int(os.getenv("KNOWLEDGE_GAP_MIN_OCCURRENCES", "3"))
KNOWLEDGE_GAP_WINDOW_HOURS = int(os.getenv("KNOWLEDGE_GAP_WINDOW_HOURS", "24"))

# ====================================
# 财务表格专用配置
# ====================================
# 财务表格行级切分：每批最多多少行 chunk（防止超大表撑爆 embedding）
FINANCIAL_TABLE_ROWS_PER_CHUNK = int(os.getenv("FINANCIAL_TABLE_ROWS_PER_CHUNK", "20"))
# 财务文档 chunk 数量上限（覆盖 MAX_CHUNKS_PER_DOC，因行级切分会产更多 chunk）
FINANCIAL_MAX_CHUNKS_PER_DOC = int(os.getenv("FINANCIAL_MAX_CHUNKS_PER_DOC", "10000"))
# 财务文档强制 PII 脱敏（覆写全局 FILTER_ENABLE_PII_MASK）
FINANCIAL_PII_MASK_FORCE = os.getenv("FINANCIAL_PII_MASK_FORCE", "true").lower() == "true"
# 财务 SQL 旁路检索开关：查询含财务指标 + 数值条件时走 SQL 精确检索
FINANCIAL_SQL_BYPASS_ENABLED = os.getenv("FINANCIAL_SQL_BYPASS_ENABLED", "true").lower() == "true"

# ====================================
# Metadata 规则指纹 — 改任何规则文件自动变化
# ====================================
import hashlib as _hashlib

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../agent/backend/
_METADATA_RULE_FILES = [
    os.path.join(_BACKEND_DIR, "config", "rag.py"),
    os.path.join(_BACKEND_DIR, "rag", "preprocessing", "keyword.py"),
    os.path.join(_BACKEND_DIR, "rag", "preprocessing", "metadata.py"),
    os.path.join(_BACKEND_DIR, "rag", "preprocessing", "domain_data.py"),
    os.path.join(_BACKEND_DIR, "rag", "preprocessing", "financial_normalizer.py"),
    os.path.join(_BACKEND_DIR, "rag", "indexing", "indexer.py"),
]

def compute_metadata_fingerprint() -> str:
    """SHA256 前 12 位：hash 4 个规则源文件，改任何一行自动变化。"""
    h = _hashlib.sha256()
    for fp in _METADATA_RULE_FILES:
        try:
            with open(fp, "rb") as f:
                h.update(f.read())
        except FileNotFoundError:
            pass
    return h.hexdigest()[:12]

METADATA_SCHEMA_FINGERPRINT = compute_metadata_fingerprint()