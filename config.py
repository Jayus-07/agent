"""
配置管理模块
从环境变量加载配置，提供统一的配置访问接口
"""
import os
import re
from typing import List, Set, Dict

from dotenv import load_dotenv

# 加载 .env 文件
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

VECTOR_SEARCH_K = int(os.getenv("VECTOR_SEARCH_K", "5"))
BM25_SEARCH_K = int(os.getenv("BM25_SEARCH_K", "20"))
HYBRID_SEARCH_K = int(os.getenv("HYBRID_SEARCH_K", "20"))
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "8"))
RERANK_SCORE_THRESHOLD = float(os.getenv("RERANK_SCORE_THRESHOLD", "0.3"))

MULTI_QUERY_NUM = int(os.getenv("MULTI_QUERY_NUM", "3"))

# 重写相似度阈值
REWRITE_SIMILARITY_THRESHOLD = float(os.getenv("REWRITE_SIMILARITY_THRESHOLD", "0.80"))


# ====================================
# 数据库路径
# ====================================
CHROMA_PATH = os.getenv("CHROMA_PATH", "data/chroma")
DOC_DB_PATH = os.getenv("DOC_DB_PATH", "data/doc_db")
DOCS_DIRECTORY = os.getenv("DOCS_DIRECTORY", "data/docs")


# ====================================
# 路由关键词
# ====================================
DOC_LEVEL_KEYWORDS = [
     # 人物相关
    "这个人", "是谁", "做过什么", "介绍", "简历", "项目经历", "做了",
    "负责什么", "履历", "背景", "擅长领域", "联系方式", "邮箱", "电话",
    # 总结/概览
    "总结", "概括", "简述", "要点", "核心内容", "主要工作", "关键成果",
    # 技术/系统
    "怎么实现", "原理", "架构", "设计思路", "技术选型", "方案对比",
    "故障处理", "报错", "原因分析", "解决方案",
    # 流程/规范
    "怎么操作", "步骤", "流程", "规范", "制度", "审批", "权限",
    # 数据/统计
    "统计", "报表", "趋势", "同比", "环比", "排名", "占比",
]

# =============================================
# 自定义人名词典（根据你的实际人员维护）
# =============================================
KNOWN_PERSON_NAMES = [
    "吴浩",
    "张三",
    "李四",
    "王小明"
    # 添加你实际需要识别的所有人名
]

global_keywords = [
    # 总结类
    "所有", "全部", "整体", "全面",
    "总结", "汇总", "概括",

    # 对比类
    "比较", "对比", "区别",
    "差异", "不同",

    # 聚合类
    "哪些", "列举",
    "完整", "完整资料",
    "全部资料",
    "详细",
    "相关内容",
    "所有信息"
]

need_multi_query_keywords = [
    "分析", "总结", "原因", "为什么", "为何",
    "优化", "方案", "建议",
    "比较", "对比", "区别", "差异", "不同",
    "如何", "怎么", "步骤", "方法",
    "影响", "作用", "效果",
    "哪些", "列举", "所有",
    "解释", "说明", "描述"

]

DEFAULT_KEYWORDS: List[str] = [
    # 中间件 & 数据库
    "Redis", "RocketMQ", "Kafka", "RabbitMQ", "MySQL", "PostgreSQL",
    "MongoDB", "Elasticsearch", "ClickHouse", "TiDB",
    # 框架 & 库
    "SpringBoot", "SpringCloud", "MyBatis", "JPA", "JWT", "RBAC",
    "Netty", "Dubbo", "gRPC", "Flask", "Django",
    # 容器 & 云原生
    "Docker", "Kubernetes", "K8s", "Istio", "Prometheus", "Grafana",
    # 业务词汇
    "支付", "用户", "投诉", "报销", "绩效", "运营", "销售", "库存",
    "订单", "缓存", "限流", "熔断", "降级", "高并发", "微服务",
    "分布式", "权限", "认证", "审计", "日志", "监控", "告警",
    # 数据 & AI
    "数据仓库", "数据湖", "ETL", "OLAP", "特征工程", "召回", "排序",
    "大模型", "LLM", "RAG", "Embedding", "向量检索",
    # 运维 & 网络
    "负载均衡", "CDN", "DNS", "网关", "API", "SDK", "CI/CD",
    "Jenkins", "GitLab", "Nginx", "LVS",
    # 零售 & 生鲜
    "叶菜", "蔬菜", "肉类", "水产", "熟食", "水果", "鲜食", "干货",
    "保鲜", "保质期", "下架", "次日", "冷藏", "冷冻", "常温",
    "验收", "配送", "补货", "损耗", "陈列", "标价", "促销",
    "生鲜", "营运", "品控", "临期", "报损", "退货",
]


# 信号词规则：匹配到子串 → 打上层标签
SIGNAL_RULES: Dict[str, List[str]] = {
    "技术系统": ["redis", "mysql", "kubernetes", "mq", "docker", "nginx"],
    "生鲜管理": ["叶菜", "蔬菜", "肉类", "水产", "保鲜", "保质期", "下架", "冷藏", "冷冻"],
    "零售运营": ["促销", "陈列", "标价", "补货", "损耗", "配送", "验收"],
}


# =====================================================
# 常量配置（便于维护和扩展）
# =====================================================

# 文档类型识别规则（正则表达式，避免子串误匹配）
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
# ====================================
# 异步并发控制
# ====================================
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "4"))  # 最大并发 LLM 请求数

# 人名提取正则（编译后复用）
CHINESE_NAME_PATTERN = [
    re.compile(r'(?:负责人|经理|总监|员工|主管|CEO|CTO|作者|工程师)[:：]?\s*([\u4e00-\u9fa5]{2,4})(?![\u4e00-\u9fa5])'),
    re.compile(r'([\u4e00-\u9fa5]{2,4})(?:负责|表示|提出|开发|设计)(?![\u4e00-\u9fa5])')
]

# 人名黑名单（常见非人名的2-4字词）
PERSON_BLACKLIST: Set[str] = {
    # 团队/组织
    "技术团队", "研发团队", "运营团队", "客服部门",
    "数据库", "支付模块", "缓存机制", "公司", "系统",
    "项目", "产品", "需求", "流程", "方案", "功能", "性能",
    "问题", "方案", "版本", "用户", "订单",
    
    # 技术术语（从日志误识别中添加）
    "静态化", "拉取", "审批", "叶菜类", "麦菜", "叶菜",
    "文件夹", "手机号", "需在每月", "每月允许",
    "模块详细", "项目架构", "终一致性", "整体架构", "性与防刷",
    "蒙蝴蝶云", "分防篡改", "细心", "日常办公",
    
    # 常见动词/名词
    "负责", "开发", "设计", "测试", "部署", "运维",
    "分析", "优化", "重构", "迭代", "上线",
    "接口", "服务", "组件", "配置", "环境",
    
    # 业务词汇
    "库存", "商品", "分类", "规格", "价格",
    "财务", "报销", "预算", "成本", "利润",
    "绩效", "考勤", "请假", "招聘", "入职",
}

# 时间引用正则（编译后复用）
TIME_PATTERNS = [
    re.compile(r'(19|20)\d{2}年'),  # 2023年
    re.compile(r'\d{4}-\d{2}-\d{2}'),  # 2023-06-01
    re.compile(r'(?:1[0-2]|0?[1-9])月'),  # 1-12月
    re.compile(r'Q[1-4]'),  # Q1-Q4
    re.compile(r'最近一个月'),
    re.compile(r'最近两周'),
    re.compile(r'昨天'),
    re.compile(r'今年'),
    re.compile(r'上季度'),
    re.compile(r'第一季度')
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

PERSON_QUERY_PATTERNS = [
    "是谁",
    "做了什么",
    "干了什么",
    "简介",
    "经历",
    "项目",
    "成就",
    "负责",
    "参与",
    "贡献"
]

# 关键词提取 清洗过滤词
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
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "30"))  # LLM调用超时
EMBEDDING_TIMEOUT = int(os.getenv("EMBEDDING_TIMEOUT", "10"))  # Embedding计算超时
RERANK_TIMEOUT = int(os.getenv("RERANK_TIMEOUT", "15"))  # 重排序超时
OVERALL_REQUEST_TIMEOUT = int(os.getenv("OVERALL_REQUEST_TIMEOUT", "60"))  # 整体请求超时

# ====================================
# 资源监控配置
# ====================================
MAX_DOCS_IN_MEMORY = int(os.getenv("MAX_DOCS_IN_MEMORY", "1000"))  # 内存中最大文档数
MEMORY_WARNING_THRESHOLD = float(os.getenv("MEMORY_WARNING_THRESHOLD", "0.85"))  # 内存警告阈值(85%)
CPU_WARNING_THRESHOLD = float(os.getenv("CPU_WARNING_THRESHOLD", "0.90"))  # CPU警告阈值(90%)
ENABLE_RESOURCE_MONITOR = os.getenv("ENABLE_RESOURCE_MONITOR", "true").lower() == "true"  # 是否启用资源监控

# ====================================
# 会话记忆配置
# ====================================
CHAT_HISTORY_DB = os.getenv("CHAT_HISTORY_DB", "sqlite:///data/chat_history.db")  # 会话记忆持久化路径
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "5"))  # 保留最近 N 轮完整对话
ENABLE_HISTORY_AWARE_RETRIEVAL = os.getenv("ENABLE_HISTORY_AWARE_RETRIEVAL", "true").lower() == "true"  # 启用历史感知检索

# ====================================
# 上下文压缩配置
# ====================================
ENABLE_LLM_COMPRESSION = os.getenv("ENABLE_LLM_COMPRESSION", "false").lower() == "true"  # 启用LLM提取式压缩（较慢但更精准）
COMPRESSION_TARGET_TOKENS = int(os.getenv("COMPRESSION_TARGET_TOKENS", "3000"))  # 压缩后目标 token 数
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "4096"))  # LLM 上下文窗口上限
