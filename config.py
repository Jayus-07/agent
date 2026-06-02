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

LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:3b")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_CONTEXT_LENGTH = int(os.getenv("LLM_CONTEXT_LENGTH", "4096"))


# ====================================
# 检索配置
# ====================================
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))

BM25_SEARCH_K = int(os.getenv("BM25_SEARCH_K", "20"))
HYBRID_SEARCH_K = int(os.getenv("HYBRID_SEARCH_K", "20"))
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "8"))
RERANK_SCORE_THRESHOLD = float(os.getenv("RERANK_SCORE_THRESHOLD", "0.3"))


# ====================================
# 数据库路径
# ====================================
CHROMA_PATH = os.getenv("CHROMA_PATH", "data/chroma")
DOC_DB_PATH = os.getenv("DOC_DB_PATH", "data/doc_db")
DOCS_DIRECTORY = os.getenv("DOCS_DIRECTORY", "data/docs")


# ====================================
# 自适应检索配置
# ====================================
# 单文档在 top-k chunks 中占比超过此值，触发文档补全
ADAPTIVE_CLUSTER_THRESHOLD = float(os.getenv("ADAPTIVE_CLUSTER_THRESHOLD", "0.3"))
# 聚类文档数 ≤ 此值才补全（避免上下文爆炸）
ADAPTIVE_MAX_CLUSTER_DOCS = int(os.getenv("ADAPTIVE_MAX_CLUSTER_DOCS", "2"))

# 自定义人名词典
KNOWN_PERSON_NAMES = [
    "吴浩",
    "张三",
    "李四",
    "王小明"
]

DEFAULT_KEYWORDS: List[str] = [
    "Redis", "RocketMQ", "Kafka", "RabbitMQ", "MySQL", "PostgreSQL",
    "MongoDB", "Elasticsearch", "ClickHouse", "TiDB",
    "SpringBoot", "SpringCloud", "MyBatis", "JPA", "JWT", "RBAC",
    "Netty", "Dubbo", "gRPC", "Flask", "Django",
    "Docker", "Kubernetes", "K8s", "Istio", "Prometheus", "Grafana",
    "支付", "用户", "投诉", "报销", "绩效", "运营", "销售", "库存",
    "订单", "缓存", "限流", "熔断", "降级", "高并发", "微服务",
    "分布式", "权限", "认证", "审计", "日志", "监控", "告警",
    "数据仓库", "数据湖", "ETL", "OLAP", "特征工程", "召回", "排序",
    "大模型", "LLM", "RAG", "Embedding", "向量检索",
    "负载均衡", "CDN", "DNS", "网关", "API", "SDK", "CI/CD",
    "Jenkins", "GitLab", "Nginx", "LVS",
    "叶菜", "蔬菜", "肉类", "水产", "熟食", "水果", "鲜食", "干货",
    "保鲜", "保质期", "下架", "次日", "冷藏", "冷冻", "常温",
    "验收", "配送", "补货", "损耗", "陈列", "标价", "促销",
    "生鲜", "营运", "品控", "临期", "报损", "退货",
]

# 信号词规则
SIGNAL_RULES: Dict[str, List[str]] = {
    "技术系统": ["redis", "mysql", "kubernetes", "mq", "docker", "nginx"],
    "生鲜管理": ["叶菜", "蔬菜", "肉类", "水产", "保鲜", "保质期", "下架", "冷藏", "冷冻"],
    "零售运营": ["促销", "陈列", "标价", "补货", "损耗", "配送", "验收"],
}

# 文档类型识别规则
DOC_TYPE_RULES: Dict[str, List[str]] = {
    "resume": [
        r"(?<!\w)简历(?!\w)",
        r"求职意向",
        r"工作经验",
        r"教育背景"
    ],
    "project": [
        r"(?<!\w)项目(?!\w)",
        r"技术栈",
        r"架构设计",
        r"核心职责"
    ],
    "report": [
        r"(?<!\w)报告(?!\w)",
        r"分析",
        r"统计",
        r"同比"
    ],
    "manual": [
        r"操作步骤",
        r"处理流程",
        r"故障",
        r"报警代码"
    ],
    "policy": [
        r"制度",
        r"规范",
        r"审批",
        r"规定"
    ]
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

# 业务领域识别规则（带权重）
DOMAIN_RULES: Dict[str, Dict[str, int]] = {
    "finance": {
        "报销": 2, "预算": 2, "财务": 3, "发票": 2,
        "成本": 1, "利润": 1, "税务": 2
    },
    "hr": {
        "绩效": 2, "考勤": 2, "请假": 2, "员工": 1,
        "招聘": 2, "入职": 1, "离职": 1
    },
    "ecommerce": {
        "订单": 3, "库存": 2, "秒杀": 3, "支付": 2,
        "购物车": 2, "SKU": 2
    },
    "operation": {
        "活跃用户": 3, "转化率": 3, "运营": 2,
        "留存率": 3, "DAU": 2, "MAU": 2
    },
    "infrastructure": {
        "Kubernetes": 3, "Redis": 3, "CPU": 2, "数据库": 2,
        "Docker": 3, "微服务": 2, "容器": 2
    }
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
OVERALL_REQUEST_TIMEOUT = int(os.getenv("OVERALL_REQUEST_TIMEOUT", "60"))

# ====================================
# 资源监控配置
# ====================================
ENABLE_RESOURCE_MONITOR = os.getenv("ENABLE_RESOURCE_MONITOR", "true").lower() == "true"

# ====================================
# 会话记忆配置
# ====================================
CHAT_HISTORY_DB = os.getenv("CHAT_HISTORY_DB", "sqlite:///data/chat_history.db")
ENABLE_HISTORY_AWARE_RETRIEVAL = os.getenv("ENABLE_HISTORY_AWARE_RETRIEVAL", "true").lower() == "true"

# ====================================
# 上下文压缩配置
# ====================================
ENABLE_LLM_COMPRESSION = os.getenv("ENABLE_LLM_COMPRESSION", "false").lower() == "true"

DB_CONFIG = {
    "host":     os.getenv("PGHOST", "localhost"),
    "port":     int(os.getenv("PGPORT", "5432")),
    "dbname":   os.getenv("PGDATABASE", "demo"),
    "user":     os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "123456"),
}

