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
DOC_TYPE_RULES: Dict[str, List[tuple]] = {
    "listing": [(r"(?<!\w)listing(?!\w)", 10), (r"五点描述", 10), (r"A\+内容", 8), (r"关键词策略", 5), (r"标题公式", 5), (r"主图规范", 5)],
    "sop": [(r"(?<!\w)sop(?!\w)", 20), (r"标准操作", 8), (r"操作流程", 8), (r"标准作业", 8), (r"作业指导书", 8), (r"作业指导", 5)],
    "ad_policy": [(r"广告政策", 10), (r"投放规则", 8), (r"amazon ads", 10), (r"竞价策略", 8), (r"广告规范", 5)],
    "faq": [(r"(?<!\w)faq(?!\w)", 10), (r"常见问题", 10), (r"退货政策", 5), (r"物流时效", 5), (r"售后流程", 5)],
    "product_spec": [(r"产品规格", 10), (r"材质说明", 8), (r"使用手册", 8), (r"保养指南", 8), (r"故障排查", 5)],
    "training": [(r"培训", 10), (r"新人手册", 5), (r"上岗", 5), (r"考核", 5)],
    "policy": [(r"制度", 5), (r"规范", 3), (r"审批", 5), (r"规定", 5), (r"管理条例", 10)],
    "compliance": [(r"合规", 10), (r"法规", 10), (r"监管", 10), (r"gdpr", 10), (r"ccpa", 10), (r"数据保护", 8), (r"个人信息", 8), (r"隐私政策", 10)],
    "legal": [(r"合同", 10), (r"条款", 10), (r"违约责任", 10), (r"赔偿", 8), (r"知识产权", 10), (r"保密协议", 10), (r"法律", 8)],
    "security": [(r"安全", 10), (r"权限", 8), (r"访问控制", 8), (r"加密", 8), (r"漏洞", 8), (r"认证", 8)],
    "financial": [(r"财务", 10), (r"报销", 8), (r"预算", 8), (r"发票", 8), (r"付款审批", 8), (r"账[务户簿单目]|对账|坏账", 6)],
    "customer_data": [(r"客户数据", 10), (r"个人信息", 10), (r"用户隐私", 10), (r"数据收集", 8), (r"用户画像", 8)],
    "contract_template": [(r"合同模板", 10), (r"协议模板", 10), (r"标准条款", 8), (r"模板", 5)],
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
    "hr": "policy", "finance": "policy",
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
}
