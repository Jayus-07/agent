"""电商领域知识数据 — 从 config/rag.py 抽出（PR-2.x 分离配置与业务规则）。

关键词列表、文档分类规则、信号规则、业务领域映射等。
"""
import re
from typing import List, Dict

# ====================================
# 电商品牌/平台/关键实体名册
# ====================================
KNOWN_PERSON_NAMES = [
    "MeridiHome", "ZenNest", "TechGleam", "EcoLiving", "PetPal",
    "BabyJoy", "OutdoorPro", "SmartChef",
    "Amazon", "Shopify", "TikTok Shop", "eBay", "Walmart",
]

# ====================================
# 时间引用正则（编译后复用）
# ====================================
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

# ====================================
# 关键词 + 业务领域规则
# ====================================
DEFAULT_KEYWORDS: List[str] = [
    # 商品管理
    "SKU", "SPU", "Listing", "上架", "下架", "变体", "品类", "类目",
    "品牌", "规格", "条码", "标题", "五点", "A+", "主图", "附图",
    "关键词策略", "搜索词", "排名", "BSR", "BestSeller",
    # 订单履约
    "订单", "下单", "付款", "发货", "签收", "取消", "退款", "退货",
    "拆单", "合单", "包裹", "面单", "拣货", "包装", "出库",
    # 库存管理
    "库存", "FBA", "海外仓", "3PL", "国内仓", "调拨", "在途",
    "安全库存", "预警", "滞销", "动销率", "周转", "盘点",
    # 物流追踪
    "头程", "尾程", "清关", "报关", "HS编码", "关税", "追踪号",
    "时效", "运费", "DHL", "FedEx", "UPS", "USPS",
    # 广告投放
    "ACoS", "ROAS", "CTR", "CPC", "CPM", "TACoS", "Campaign",
    "竞价", "投放", "广告组", "关键词", "否定词", "匹配类型",
    "曝光", "点击", "转化", "归因", "预算",
    # 客户服务
    "客户", "买家", "投诉", "差评", "好评", "Review", "Feedback",
    "FAQ", "售后", "保修", "退换", "索赔", "AZ", "Chargeback",
    # 经营分析
    "日报", "周报", "月报", "同比", "环比", "毛利率", "净利润",
    "ROI", "客单价", "复购率", "LTV", "转化率",
    # 平台/市场
    "Amazon", "Shopify", "TikTok", "eBay", "Walmart",
    "美国站", "欧洲站", "日本站", "北美", "欧盟", "英国", "德国",
]

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

# 停用词（关键词提取时过滤）
STOPWORDS = {"系统", "进行", "问题", "公司", "我们", "已经", "可以", "这个", "那个"}

# 文档类型 / 文件名 / 目录 / 业务域规则**不在此文件定义**。
# 唯一事实源是 preprocessing/metadata_taxonomy.yaml（经 taxonomy_spec.py 加载并
# 严格校验），下方 `# ── 版本化 taxonomy 兼容导出` 段落把旧模块名重导出。
# 历史上这里手写过一整套同名表（DOC_TYPE_RULES / FILENAME_TYPE_HINTS /
# FOLDER_TYPE_HINTS / DOMAIN_RULES，共 180 行），随后被下方的兼容导出**整体覆盖**——
# 即那份手写表从来没有生效过，属纯死代码，已于本次结构病治理中删除。


# ── 版本化 taxonomy 兼容导出 ───────────────────────────────────────
# 保留旧模块名，实际内容以 metadata_taxonomy.yaml 为唯一事实源。
from backend.rag.preprocessing.taxonomy_spec import get_taxonomy

_TAXONOMY = get_taxonomy()
DOC_TYPE_RULES = _TAXONOMY.legacy_doc_type_rules()
FILENAME_TYPE_HINTS = dict(_TAXONOMY.filename_hints)
FOLDER_TYPE_HINTS = dict(_TAXONOMY.folder_hints)
DOMAIN_RULES = {
    domain: dict(rules)
    for domain, rules in _TAXONOMY.domain_rules.items()
    if domain != "general"
}

# ====================================
# 财务指标识别模式（用于 QueryAnalyzer 提取财务指标）
# ====================================
FINANCIAL_METRIC_PATTERNS: List[tuple] = [
    (r"营业收入|营收|revenue|总收入", "revenue"),
    (r"净利润|净亏损|net.?profit|net.?loss", "net_profit"),
    (r"毛利率|gross.?margin|gross.?profit.?ratio", "gross_margin"),
    (r"净利率|净收益率|net.?margin", "net_margin"),
    (r"资产负债率|debt.?ratio|asset.?liability.?ratio", "debt_ratio"),
    (r"现金流|cash.?flow|经营性现金流|operating.?cash.?flow", "cash_flow"),
    (r"\bROE\b|净资产收益率", "roe"),
    (r"\bROI\b|投资回报率", "roi"),
    (r"\bROA\b|总资产收益率", "roa"),
    (r"\bEBITDA\b|息税折旧摊销前利润", "ebitda"),
    (r"营业收入成本|营业成本|operating.?cost", "operating_cost"),
    (r"销售费用|管理费用|财务费用|期间费用", "operating_expense"),
    (r"总资产|净资产|total.?assets|net.?assets", "total_assets"),
    (r"总负债|total.?liabilities", "total_liabilities"),
    (r"存货周转率|inventory.?turnover", "inventory_turnover"),
    (r"应收账款周转|receivable.?turnover", "receivable_turnover"),
    (r"流动比率|current.?ratio", "current_ratio"),
    (r"速动比率|quick.?ratio", "quick_ratio"),
]

# 财务数值比较条件模式（用于 QueryAnalyzer 提取数值条件）
# 匹配“毛利率超过/大于/高于 30%”等表达
FINANCIAL_NUMERIC_CONDITION_RE = re.compile(
    r"([\u4e00-\u9fff\w]+?)\s*"
    r"(超过|大于|高于|不低于|至少|≥|>|超过|不低于以上)\s*"
    r"(\d+\.?\d*)\s*(%|万|亿|百万|千万)?"
)
FINANCIAL_NUMERIC_CONDITION_RE_LTE = re.compile(
    r"([\u4e00-\u9fff\w]+?)\s*"
    r"(低于|小于|不超过|最多|至多|≤|<|以下)\s*"
    r"(\d+\.?\d*)\s*(%|万|亿|百万|千万)?"
)

# ====================================
# 财务专用 PII 脱敏规则
# ====================================
FINANCIAL_PII_PATTERNS: List[tuple] = [
    # 銀行卡号（16-19位数字）
    (re.compile(r"(?<!\d)(\d{16,19})(?!\d)"), "bank_card"),
    # 统一社会信用代码（18位字母数字混合）
    (re.compile(r"(?<![A-Za-z0-9])([0-9A-HJ-NPQRTUWXY]{18})(?![A-Za-z0-9])"), "tax_id"),
    # 身份证号（18位，末位可能为X）
    (re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)"), "id_card"),
    # 纳税人识别号（15-20位字母数字）
    (re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{15,20})(?![A-Za-z0-9])"), "tax_number"),
]
