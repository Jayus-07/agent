"""
配置管理模块
从环境变量加载配置，提供统一的配置访问接口
"""
import os
import re
from typing import List, Dict

from dotenv import load_dotenv

load_dotenv()

# ====================================
# 模型配置
# ====================================
EMBEDDING_MODEL_PATH = os.getenv(
    "EMBEDDING_MODEL_PATH",
    "C:/Users/wh/.cache/modelscope/hub/models/BAAI/bge-small-zh-v1___5"
)

RERANKER_MODEL_PATH = os.getenv(
    "RERANKER_MODEL_PATH",
    "C:/Users/wh/.cache/modelscope/hub/models/BAAI/bge-reranker-base"
)

LLM_MODEL = os.getenv("LLM_MODEL", "MiniMax-M3")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_CONTEXT_LENGTH = int(os.getenv("LLM_CONTEXT_LENGTH", "4096"))

# DeepSeek 配置（用于多 LLM provider 切换）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

# MiniMax 配置（OpenAI 兼容协议）
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "sk-cp-FEc5W-609t1aGLfk85LPy8_uYDmCULbNCUC1H1YUhdyCNcPEaIM2OzRSw0yyu-w15Kjl1p7ePxfX-p8WrHHAXDBl3Pt8vXAb6EHb2OSEVJvcCfmxG9L4BeE")
MINIMAX_API_BASE = os.getenv("MINIMAX_API_BASE", "https://api.minimax.chat/v1")


# ====================================
# 检索配置
# ====================================
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))

# ====================================
# 文档类型感知分块配置
# ====================================
# project/report: header-first split, max chars per section; only sub-chunk if exceeded
PROJECT_CHUNK_SIZE = int(os.getenv("PROJECT_CHUNK_SIZE", "1500"))

# general/fallback: RecursiveCharacterTextSplitter params (overrides CHUNK_SIZE/CHUNK_OVERLAP)
GENERAL_CHUNK_SIZE = int(os.getenv("GENERAL_CHUNK_SIZE", "1000"))
GENERAL_CHUNK_OVERLAP = int(os.getenv("GENERAL_CHUNK_OVERLAP", "100"))

# manual/policy & resume: NO size cap — section integrity first (no config needed)

# ====================================
# Knowledge Base 隔离配置
# ====================================
DEFAULT_KB_ID = os.getenv("DEFAULT_KB_ID", "default")

BM25_SEARCH_K = int(os.getenv("BM25_SEARCH_K", "20"))
HYBRID_SEARCH_K = int(os.getenv("HYBRID_SEARCH_K", "20"))
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "8"))
RERANK_SCORE_THRESHOLD = float(os.getenv("RERANK_SCORE_THRESHOLD", "0.3"))

# Citation Filter: chunk 支撑答案的最低 CrossEncoder 分数（高于检索的 rerank 阈值，更严格）
CITATION_SUPPORT_THRESHOLD = float(os.getenv("CITATION_SUPPORT_THRESHOLD", "0.4"))


# ====================================
# 数据库路径
# ====================================
CHROMA_PATH = os.getenv("CHROMA_PATH", "data/chroma")
DOC_DB_PATH = os.getenv("DOC_DB_PATH", "data/doc_db")
DOCS_DIRECTORY = os.getenv("DOCS_DIRECTORY", "data/docs")
DOC_REGISTRY_PATH = os.getenv("DOC_REGISTRY_PATH", "data/doc_registry.db")
ENABLE_INCREMENTAL_INDEXING = os.getenv("ENABLE_INCREMENTAL_INDEXING", "true").lower() == "true"


# ====================================
# 自适应检索配置
# ====================================
# 单文档在 top-k chunks 中占比超过此值，触发文档补全
ADAPTIVE_CLUSTER_THRESHOLD = float(os.getenv("ADAPTIVE_CLUSTER_THRESHOLD", "0.3"))
# 聚类文档数 ≤ 此值才补全（避免上下文爆炸）
ADAPTIVE_MAX_CLUSTER_DOCS = int(os.getenv("ADAPTIVE_MAX_CLUSTER_DOCS", "2"))

# 电商品牌/平台/关键实体名册（用于实体提取 + 倒排索引匹配）
KNOWN_PERSON_NAMES = [
    "MeridiHome", "ZenNest", "TechGleam", "EcoLiving", "PetPal",
    "BabyJoy", "OutdoorPro", "SmartChef",
    "Amazon", "Shopify", "TikTok Shop", "eBay", "Walmart",
]

DEFAULT_KEYWORDS: List[str] = [
    # ═══════ 商品管理 ═══════
    "SKU", "SPU", "Listing", "上架", "下架", "变体", "品类", "类目",
    "品牌", "规格", "条码", "标题", "五点", "A+", "主图", "附图",
    "关键词策略", "搜索词", "排名", "BSR", "BestSeller",
    # ═══════ 订单履约 ═══════
    "订单", "下单", "付款", "发货", "签收", "取消", "退款", "退货",
    "拆单", "合单", "包裹", "面单", "拣货", "包装", "出库",
    # ═══════ 库存管理 ═══════
    "库存", "FBA", "海外仓", "3PL", "国内仓", "调拨", "在途",
    "安全库存", "预警", "滞销", "动销率", "周转", "盘点",
    # ═══════ 物流追踪 ═══════
    "头程", "尾程", "清关", "报关", "HS编码", "关税", "追踪号",
    "时效", "运费", "DHL", "FedEx", "UPS", "USPS",
    # ═══════ 广告投放 ═══════
    "ACoS", "ROAS", "CTR", "CPC", "CPM", "TACoS", "Campaign",
    "竞价", "投放", "广告组", "关键词", "否定词", "匹配类型",
    "曝光", "点击", "转化", "归因", "预算",
    # ═══════ 客户服务 ═══════
    "客户", "买家", "投诉", "差评", "好评", "Review", "Feedback",
    "FAQ", "售后", "保修", "退换", "索赔", "AZ", "Chargeback",
    # ═══════ 经营分析 ═══════
    "日报", "周报", "月报", "同比", "环比", "毛利率", "净利润",
    "ROI", "客单价", "复购率", "LTV", "转化率",
    # ═══════ 平台/市场 ═══════
    "Amazon", "Shopify", "TikTok", "eBay", "Walmart",
    "美国站", "欧洲站", "日本站", "北美", "欧盟", "英国", "德国",
]

# 信号词规则 — 跨境电商 9 领域信号分类
SIGNAL_RULES: Dict[str, List[str]] = {
    "商品管理": ["sku", "spu", "listing", "变体", "上架", "下架", "品类", "类目", "品牌备案"],
    "订单履约": ["订单", "发货", "签收", "取消", "退款", "拆单", "状态机", "履约"],
    "库存管理": ["fba", "海外仓", "3pl", "调拨", "安全库存", "滞销", "盘点", "仓库"],
    "物流追踪": ["头程", "尾程", "清关", "追踪号", "时效", "运费", "承运商"],
    "广告投放": ["acos", "roas", "cpc", "campaign", "竞价", "否定词", "归因", "广告"],
    "客户服务": ["退货", "差评", "投诉", "faq", "售后", "保修", "索赔", "review"],
    "供应商管理": ["供应商", "po", "交期", "验货", "对账", "采购", "比价"],
    "经营分析": ["日报", "周报", "毛利率", "净利润", "roi", "客单价", "复购率"],
    "平台渠道": ["amazon", "shopify", "tiktok", "ebay", "walmart", "账号", "店铺"],
}

# 文档类型识别规则 — 跨境电商知识库文档分类
DOC_TYPE_RULES: Dict[str, List[str]] = {
    "listing": [
        r"(?<!\w)Listing(?!\w)",
        r"五点描述",
        r"A\+内容",
        r"关键词策略",
        r"标题公式",
        r"主图规范",
    ],
    "sop": [
        r"(?<!\w)SOP(?!\w)",
        r"标准操作",
        r"操作流程",
        r"标准作业",
        r"作业指导",
    ],
    "ad_policy": [
        r"广告政策",
        r"投放规则",
        r"Amazon Ads",
        r"竞价策略",
        r"广告规范",
    ],
    "faq": [
        r"(?<!\w)FAQ(?!\w)",
        r"常见问题",
        r"退货政策",
        r"物流时效",
        r"售后流程",
    ],
    "product_spec": [
        r"产品规格",
        r"材质说明",
        r"使用手册",
        r"保养指南",
        r"故障排查",
    ],
    "training": [
        r"培训",
        r"新人手册",
        r"上岗",
        r"考核",
    ],
    "policy": [
        r"制度",
        r"规范",
        r"审批",
        r"规定",
        r"管理条例",
    ],
}

# 异步并发控制
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "4"))

# 时间引用正则（编译后复用）
TIME_PATTERNS = [
    re.compile(r'(19|20)\d{2}年'),
    re.compile(r'\d{4}-\d{2}-\d{2}'),
    re.compile(r'(?:1[0-2]|0?[1-9])月'),
    re.compile(r'Q[1-4]'),
    re.compile(r'最近一个月'),
    re.compile(r'最近两周'),
    re.compile(r'昨天'),
    re.compile(r'今年'),
    re.compile(r'上季度'),
    re.compile(r'第一季度'),
]

# 业务领域识别规则（带权重）— 跨境电商 9 大领域
DOMAIN_RULES: Dict[str, Dict[str, int]] = {
    "product": {
        "SKU": 3, "SPU": 3, "Listing": 3, "上架": 2, "下架": 2,
        "变体": 2, "品类": 2, "类目": 2, "品牌": 2, "条码": 2,
    },
    "order": {
        "订单": 3, "发货": 3, "签收": 2, "取消": 2, "退款": 2,
        "退货": 2, "拆单": 2, "履约": 2, "包裹": 1,
    },
    "inventory": {
        "库存": 3, "FBA": 3, "海外仓": 2, "调拨": 2, "在途": 2,
        "安全库存": 2, "滞销": 2, "周转": 2, "盘点": 2,
    },
    "logistics": {
        "头程": 3, "尾程": 3, "清关": 3, "追踪号": 2, "时效": 2,
        "运费": 2, "承运商": 2, "HS编码": 2, "报关": 2,
    },
    "advertising": {
        "ACoS": 3, "ROAS": 3, "CPC": 3, "Campaign": 2, "广告": 2,
        "竞价": 2, "投放": 2, "曝光": 1, "点击": 1, "转化": 1,
    },
    "customer": {
        "退货": 2, "差评": 3, "投诉": 3, "Review": 2, "Feedback": 2,
        "售后": 2, "索赔": 3, "保修": 2, "复购": 2,
    },
    "supplier": {
        "供应商": 3, "PO": 2, "交期": 3, "验货": 2, "对账": 2,
        "采购": 2, "比价": 2, "工厂": 2,
    },
    "analytics": {
        "日报": 3, "周报": 3, "月报": 3, "毛利率": 3, "净利润": 3,
        "ROI": 2, "客单价": 2, "转化率": 2, "同比": 2, "环比": 2,
    },
    "knowledge": {
        "SOP": 3, "FAQ": 3, "培训": 2, "政策": 2, "规范": 2,
        "制度": 2, "流程": 1, "操作手册": 2,
    },
}

# 关键词过滤
blacklist = {"系统", "进行", "问题", "公司", "我们", "已经", "可以", "这个", "那个"}

# 摘要长度限制
SUMMARY_MAX_LENGTH = 250

# ====================================
# 日志配置
# ====================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "rag_system.log")

# ====================================
# 超时配置（秒）
# ====================================
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "30"))
RERANK_TIMEOUT = int(os.getenv("RERANK_TIMEOUT", "15"))
# retrieval/pipeline.py 用作软超时告警阈值（elapsed > 0.8 * OVERALL_REQUEST_TIMEOUT 触发告警）
OVERALL_REQUEST_TIMEOUT = int(os.getenv("OVERALL_REQUEST_TIMEOUT", "60"))

# =====================================================
# Plan Critique 配置
# =====================================================
ENABLE_PLAN_CRITIQUE = True    # 启用 Plan Critique 自我纠错（+1 LLM 调用）
RERANKER_THRESHOLD = 0.35      # Context Filter 最低相关度阈值（0.0-1.0）

# ====================================
# 资源监控配置
# ====================================
ENABLE_RESOURCE_MONITOR = os.getenv("ENABLE_RESOURCE_MONITOR", "true").lower() == "true"

# ====================================
# 会话记忆配置 (L2)
# ====================================
ENABLE_HISTORY_AWARE_RETRIEVAL = os.getenv("ENABLE_HISTORY_AWARE_RETRIEVAL", "true").lower() == "true"

# ====================================
# 短期记忆配置 (L1)
# ====================================
SHORT_TERM_MAX_MESSAGES = int(os.getenv("SHORT_TERM_MAX_MESSAGES", "20"))

# ====================================
# 会话记忆配置 (L2)
# ====================================
SESSION_MAX_MESSAGES = int(os.getenv("SESSION_MAX_MESSAGES", "50"))

# ====================================
# 长期记忆配置 (L3)
# ====================================
ENABLE_LONG_TERM_MEMORY = os.getenv("ENABLE_LONG_TERM_MEMORY", "true").lower() == "true"

# L3 PostgreSQL + pgvector 后端 (已替代 ChromaDB)

# PII 过滤器: 写入前自动脱敏身份证号/手机号/银行卡/邮箱等
L3_PII_FILTER_ENABLED = os.getenv("L3_PII_FILTER_ENABLED", "true").lower() == "true"

# 去重阈值 (余弦相似度)
L3_DEDUP_COSINE_THRESHOLD = float(os.getenv("L3_DEDUP_COSINE_THRESHOLD", "0.85"))
L3_SUPERSEDE_THRESHOLD = float(os.getenv("L3_SUPERSEDE_THRESHOLD", "0.92"))

# 时间衰减: 由 MemoryDecayService 直接读取常量；L3_DECAY_RATE 配置项已废弃
# （如需重新启用衰减可在此恢复 L3_DECAY_RATE 配置 + decay.py 中的读取）

# ====================================
# Memory — Enterprise PostgreSQL
# ====================================
MEMORY_ASYNC_POOL_SIZE = int(os.getenv("MEMORY_ASYNC_POOL_SIZE", "20"))
MEMORY_ASYNC_MAX_OVERFLOW = int(os.getenv("MEMORY_ASYNC_MAX_OVERFLOW", "10"))

DB_CONFIG = {
    "host":     os.getenv("PGHOST", "localhost"),
    "port":     int(os.getenv("PGPORT", "5432")),
    "dbname":   os.getenv("PGDATABASE", "demo"),
    "user":     os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "123456"),
}

# ====================================
# 文档清洗配置（P0-1）
# ====================================
CLEAN_REMOVE_CONTROL_CHARS = os.getenv("CLEAN_REMOVE_CONTROL_CHARS", "false").lower() == "true"
CLEAN_NORMALIZE_FULLWIDTH = os.getenv("CLEAN_NORMALIZE_FULLWIDTH", "false").lower() == "true"
CLEAN_MERGE_BLANK_LINES = os.getenv("CLEAN_MERGE_BLANK_LINES", "false").lower() == "true"
CLEAN_STRIP_HTML = os.getenv("CLEAN_STRIP_HTML", "false").lower() == "true"
CLEAN_REMOVE_PDF_HEADERS = os.getenv("CLEAN_REMOVE_PDF_HEADERS", "false").lower() == "true"
CLEAN_REMOVE_PDF_FOOTERS = os.getenv("CLEAN_REMOVE_PDF_FOOTERS", "false").lower() == "true"
CLEAN_URL_ACTION = os.getenv("CLEAN_URL_ACTION", "keep")           # keep | remove | placeholder
CLEAN_EMAIL_ACTION = os.getenv("CLEAN_EMAIL_ACTION", "keep")       # keep | remove | placeholder

