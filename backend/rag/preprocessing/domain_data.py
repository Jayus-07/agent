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

# ====================================
# 文档类型分类规则（V2 加权计分）
# ====================================
# 文档类型分类规则（V2 加权计分）。
# 每个类型一组 (正则, 权重)；命中累加分数。2026-08 扩充：每类从 4-8 条增至 15-25 条，
# 覆盖同义词 / 常见变体 / 英文缩写，显著降低漏判为 general 的概率。
# 注：分类器同时会合并 keyword_rules.db 中的动态词（见 metadata.classify_with_confidence）。
DOC_TYPE_RULES: Dict[str, List[tuple]] = {
    "listing": [
        (r"(?<!\w)listing(?!\w)", 10), (r"五点描述", 10), (r"A\+内容", 8), (r"关键词策略", 5),
        (r"标题公式", 5), (r"主图规范", 5), (r"附图规范", 5), (r"搜索词", 4), (r"搜索排名", 4),
        (r"(?<!\w)bsr(?!\w)", 4), (r"bestseller", 4), (r"商品标题", 4), (r"卖点", 4),
        (r"详情页", 3), (r"类目节点", 3), (r"变体", 3), (r"父体", 3), (r"子体", 3),
        (r"bullet ?point", 3), (r"五点", 3),
    ],
    "sop": [
        (r"(?<!\w)sop(?!\w)", 20), (r"标准操作", 8), (r"操作流程", 8), (r"标准作业", 8),
        (r"作业指导书", 8), (r"作业指导", 5), (r"操作手册", 6), (r"标准流程", 6),
        (r"操作规程", 6), (r"工作指引", 5), (r"操作规范", 5), (r"作业标准", 5),
        (r"步骤说明", 4), (r"岗位操作", 5), (r"标准作业程序", 8), (r"岗位规范", 5),
        # ── 英文（匹配 lowercased 文本，须小写）──
        (r"standard operating procedure", 10), (r"operating procedure", 6),
        (r"work instruction", 6), (r"step[- ]by[- ]step", 4),
    ],
    "ad_policy": [
        (r"广告政策", 10), (r"投放规则", 8), (r"amazon ads", 10), (r"竞价策略", 8),
        (r"广告规范", 5), (r"广告活动", 6), (r"广告组", 5), (r"投放策略", 6),
        (r"广告合规", 6), (r"推广规则", 5), (r"(?<!\w)ppc(?!\w)", 5), (r"广告政策违规", 5),
        (r"广告投放政策", 6), (r"品牌推广", 4), (r"广告合规要求", 5),
        # ── 英文 ──
        (r"advertising policy", 8), (r"bidding strategy", 8), (r"campaign structure", 4),
        (r"(?<!\w)cpc(?!\w)", 4),
    ],
    "faq": [
        (r"(?<!\w)faq(?!\w)", 15), (r"常见问题", 15), (r"售后 FAQ", 20), (r"问答文档", 15),
        (r"退货政策", 5), (r"物流时效", 5), (r"售后流程", 5), (r"退换货", 8),
        (r"退款流程", 8), (r"帮助中心", 6), (r"客户问答", 6), (r"问题解答", 6),
        (r"退换货政策", 6), (r"售后指南", 5), (r"答疑", 4), (r"问答", 4), (r"客服问答", 5),
        # ── 英文 ──
        (r"frequently asked questions?", 15), (r"q&a", 8), (r"troubleshooting", 6),
        (r"return policy", 6), (r"refund policy", 6),
    ],
    "product_spec": [
        (r"产品规格", 10), (r"材质说明", 8), (r"使用手册", 8), (r"保养指南", 8),
        (r"故障排查", 5), (r"规格参数", 8), (r"技术参数", 8), (r"产品说明书", 8),
        (r"参数表", 8), (r"产品特性", 5), (r"功能介绍", 5), (r"规格书", 8),
        (r"配置清单", 6), (r"型号对照", 6), (r"产品参数", 8), (r"技术规格", 8),
        # ── 英文（匹配 lowercased 文本，须小写）──
        (r"product specification", 10), (r"specification", 8), (r"datasheet", 8),
        (r"user manual", 8), (r"user guide", 6), (r"technical parameters?", 6),
        (r"maintenance guide", 6), (r"technical specification", 8),
    ],
    "training": [
        (r"培训", 8), (r"新人手册", 5), (r"上岗", 5), (r"考核", 5), (r"培训材料", 6),
        (r"培训资料", 6), (r"入职培训", 6), (r"岗前培训", 6), (r"操作培训", 5),
        (r"培训课件", 5), (r"学习手册", 5), (r"培训计划", 5), (r"带教", 4), (r"实习手册", 5),
        (r"培训课程", 5), (r"新人培训", 6),
        # ── 英文 ──
        (r"onboarding", 8), (r"training (course|material|plan|program)", 6),
        (r"(?<!\w)trainee(?!\w)", 5),
    ],
    "policy": [
        (r"制度", 5), (r"规范", 3), (r"审批", 5), (r"规定", 5), (r"管理条例", 10),
        (r"管理办法", 8), (r"管理规定", 6), (r"管理制度", 6), (r"规章制度", 6),
        (r"实施细则", 6), (r"规则", 4), (r"守则", 4), (r"工作制度", 5),
        (r"管控要求", 4), (r"暂行", 3), (r"工作条例", 6), (r"管理办法实施细则", 8),
        # ── 英文 ──
        (r"policy and procedure", 8), (r"code of conduct", 8),
        (r"(?<!\w)guidelines?(?!\w)", 4), (r"(?<!\w)policies(?!\w)", 4),
    ],
    "compliance": [
        (r"合规", 10), (r"法规", 10), (r"监管", 10), (r"(?<!\w)gdpr(?!\w)", 10),
        (r"(?<!\w)ccpa(?!\w)", 10), (r"数据保护", 8), (r"个人信息", 8), (r"隐私政策", 10),
        (r"数据安全法", 8), (r"等保", 8), (r"网络安全法", 8), (r"合规审查", 8),
        (r"监管要求", 8), (r"审计", 6), (r"备案", 5), (r"反垄断", 6), (r"(?<!\w)iso(?!\w)", 5),
        (r"网信办", 6), (r"合规风险", 6), (r"行政处罚", 5), (r"监管检查", 6), (r"合规管理", 6),
        # ── 英文 ──
        (r"compliance", 8), (r"regulat(ion|ions|ory)", 8), (r"(?<!\w)hipaa(?!\w)", 8),
        (r"soc ?2", 8), (r"pci ?dss", 8), (r"data protection", 8), (r"privacy policy", 8),
        (r"(?<!\w)audit(?!\w)", 6), (r"supervis(ion|ory)", 6),
    ],
    "legal": [
        (r"合同", 10), (r"条款", 10), (r"违约责任", 10), (r"赔偿", 8), (r"知识产权", 10),
        (r"保密协议", 10), (r"法律", 8), (r"协议", 8), (r"诉讼", 8), (r"仲裁", 8),
        (r"纠纷", 8), (r"侵权", 8), (r"章程", 6), (r"要约", 6), (r"担保", 6), (r"执照", 6),
        (r"法律意见", 8), (r"法律文书", 8), (r"调解书", 6), (r"判决书", 6),
        (r"法律顾问", 6), (r"合规协议", 6), (r"契约", 6), (r"法务", 6),
        # ── 英文 ──
        (r"terms and conditions", 10), (r"(?<!\w)contract(?!\w)", 8), (r"agreement", 8),
        (r"(?<!\w)clause(?!\w)", 8), (r"liability", 8), (r"indemnity", 8),
        (r"confidentiality", 8), (r"arbitration", 8), (r"breach of contract", 10),
        (r"intellectual property", 10), (r"lawsuit", 6),
    ],
    "security": [
        (r"安全", 10), (r"权限", 8), (r"访问控制", 8), (r"加密", 8), (r"漏洞", 8),
        (r"认证", 8), (r"信息安全", 8), (r"数据安全", 6), (r"网络安全", 6),
        (r"身份认证", 6), (r"防火墙", 6), (r"渗透测试", 6), (r"安全策略", 6),
        (r"零信任", 6), (r"数据防泄漏", 6), (r"安全审计", 6), (r"权限管理", 6),
        (r"安全合规", 6), (r"访问控制策略", 6),
        # ── 英文 ──
        (r"password policy", 6), (r"encryption", 8), (r"access control", 8),
        (r"vulnerabilit(y|ies)", 8), (r"authentication", 8), (r"firewall", 6),
        (r"penetration test", 8), (r"zero trust", 6), (r"cyber ?security", 8),
        (r"information security", 8),
    ],
    "financial": [
        (r"财务", 10), (r"报销", 8), (r"预算", 8), (r"发票", 6), (r"付款审批", 8),
        (r"对账", 6), (r"坏账", 6), (r"账务", 5), (r"账户", 4), (r"账簿", 5), (r"账目", 4),
        (r"利润表", 8), (r"资产负债表", 8), (r"现金流量表", 8), (r"营收", 6), (r"税务", 6),
        (r"薪酬", 6), (r"成本", 5), (r"报表", 5), (r"审计", 5), (r"资金", 5), (r"核算", 5),
        (r"毛利", 5), (r"净利", 5), (r"决算", 5), (r"税务申报", 6), (r"财务报表", 8),
        # ── 英文 ──
        (r"reimbursement", 8), (r"budget", 8), (r"(?<!\w)invoice(?!\w)", 6),
        (r"balance sheet", 8), (r"income statement", 8), (r"cash flow", 8),
        (r"financial statement", 10), (r"(?<!\w)revenue(?!\w)", 6), (r"accounting", 8),
        (r"(?<!\w)expenses?(?!\w)", 5), (r"tax return", 6), (r"(?<!\w)payment(?!\w)", 4),
    ],
    "customer_data": [
        (r"客户数据", 10), (r"个人信息", 10), (r"用户隐私", 10), (r"数据收集", 8),
        (r"用户画像", 8), (r"客户信息", 8), (r"用户数据", 8), (r"隐私数据", 8),
        (r"个人数据", 8), (r"数据主体", 6), (r"数据泄露", 6), (r"数据脱敏", 6),
        (r"个人信息保护", 8), (r"用户资料", 6), (r"隐私保护", 6),
        # ── 英文 ──
        (r"customer data", 10), (r"personal data", 8), (r"user profile", 8),
        (r"(?<!\w)pii(?!\w)", 8), (r"data subject", 6), (r"data breach", 6),
        (r"data anonymization", 6), (r"personal information", 8),
    ],
    "contract_template": [
        (r"合同模板", 10), (r"协议模板", 10), (r"标准条款", 8), (r"模板", 5),
        (r"合同范本", 10), (r"协议范本", 10), (r"范本", 5), (r"标准合同", 8),
        (r"格式合同", 8), (r"模板库", 5), (r"合同范本库", 8), (r"协议范本库", 8),
        # ── 英文 ──
        (r"contract template", 10), (r"boilerplate", 8), (r"template library", 5),
        (r"model contract", 8), (r"sample agreement", 8),
    ],
}

FILENAME_TYPE_HINTS: Dict[str, str] = {
    "政策": "policy", "制度": "policy", "规范": "policy", "管理办法": "policy",
    "合规": "compliance", "法规": "compliance", "监管": "compliance", "隐私": "compliance",
    "合同": "legal", "条款": "legal", "协议": "legal", "NDA": "legal", "保密": "legal",
    "FAQ": "faq", "常见问题": "faq", "问答": "faq",
    "规格": "product_spec", "参数": "product_spec", "说明书": "product_spec",
    "SOP": "sop", "操作手册": "sop", "流程": "sop",
    "Listing": "listing", "广告": "ad_policy",
    "安全": "security", "权限": "security", "加密": "security",
    "财务": "financial", "报销": "financial", "发票": "financial",
    "客户数据": "customer_data", "用户隐私": "customer_data",
    "合同模板": "contract_template", "协议模板": "contract_template",
}

FOLDER_TYPE_HINTS: Dict[str, str] = {
    "legal": "legal", "contracts": "legal", "合同": "legal",
    "compliance": "compliance", "法规": "compliance", "regulatory": "compliance",
    "policy": "policy", "policies": "policy", "制度": "policy",
    "hr": "policy",
    "faq": "faq", "help": "faq", "常见问题": "faq",
    "products": "product_spec", "specs": "product_spec", "规格": "product_spec",
    "sop": "sop", "operations": "sop", "流程": "sop",
    "security": "security", "安全": "security", "infosec": "security",
    "finance": "financial", "财务": "financial", "报销": "financial",
    "customers": "customer_data", "customer_data": "customer_data",
    "templates": "contract_template", "模板": "contract_template",
}

DOMAIN_RULES: Dict[str, Dict[str, int]] = {
    "product": {"SKU": 3, "SPU": 3, "Listing": 3, "上架": 2, "下架": 2, "变体": 2, "品类": 2, "类目": 2, "品牌": 2, "条码": 2},
    "order": {"订单": 3, "发货": 3, "签收": 2, "取消": 2, "退款": 2, "退货": 2, "拆单": 2, "履约": 2, "包裹": 1, "售后": 2},
    "inventory": {"库存": 3, "FBA": 3, "海外仓": 2, "调拨": 2, "在途": 2, "安全库存": 2, "滞销": 2, "周转": 2, "盘点": 2},
    "logistics": {"头程": 3, "尾程": 3, "清关": 3, "追踪号": 2, "时效": 2, "运费": 2, "承运商": 2, "HS编码": 2, "报关": 2},
    "advertising": {"ACoS": 3, "ROAS": 3, "CPC": 3, "Campaign": 2, "广告": 2, "竞价": 2, "投放": 2, "曝光": 1, "点击": 1, "转化": 1},
    "customer": {"退货": 2, "差评": 3, "投诉": 3, "Review": 2, "Feedback": 2, "索赔": 3, "保修": 2, "复购": 2},
    "supplier": {"供应商": 2, "供货商": 2, "PO": 2, "交期": 3, "验货": 2, "对账": 2, "采购": 2, "比价": 2, "工厂": 2, "评估": 2, "准入": 2, "资质": 2, "考核": 2},
    "analytics": {"日报": 3, "周报": 3, "月报": 3, "毛利率": 3, "净利润": 3, "ROI": 2, "客单价": 2, "转化率": 2, "同比": 2, "环比": 2},
    "data": {"数据治理": 3, "数据质量": 3, "数据标准": 3, "数据管理": 3, "数据规范": 3, "数据安全": 2, "元数据": 3, "主数据": 3, "数据采集": 2, "数据仓库": 2, "ETL": 3, "数据血缘": 2, "数据目录": 2},
    "financial": {"营收": 3, "收入": 2, "利润": 3, "毛利": 3, "成本": 2, "费用": 2, "资产": 3, "负债": 3, "现金流": 3, "预算": 2, "报销": 2, "发票": 2, "对账": 2, "坏账": 3},
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
