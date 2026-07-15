"""业务枚举常量 — 跨境电商 Meridian Global Commerce 的真实业务数据。

所有"固定"实体（不随 Profile 规模变化的枚举值）集中管理。
"""

# ==================== 销售渠道 ====================
CHANNELS = [
    {"code": "AMAZON_US", "name": "Amazon US", "country": "US", "default_currency": "USD", "status": "ACTIVE"},
    {"code": "AMAZON_EU", "name": "Amazon EU", "country": "DE", "default_currency": "EUR", "status": "ACTIVE"},
    {"code": "AMAZON_JP", "name": "Amazon Japan", "country": "JP", "default_currency": "JPY", "status": "ACTIVE"},
    {"code": "SHOPIFY", "name": "Shopify", "country": "US", "default_currency": "USD", "status": "ACTIVE"},
    {"code": "TIKTOK_SHOP", "name": "TikTok Shop", "country": "US", "default_currency": "USD", "status": "ACTIVE"},
    {"code": "EBAY", "name": "eBay", "country": "US", "default_currency": "USD", "status": "ACTIVE"},
    {"code": "WALMART", "name": "Walmart", "country": "US", "default_currency": "USD", "status": "ACTIVE"},
]

# ==================== 仓库 ====================
WAREHOUSES = [
    {"code": "FBA_US", "name": "Amazon FBA 美国仓", "type": "FBA", "country": "US",
     "region": "US-East", "address": "Amazon FBA ONT8, California", "is_active": True},
    {"code": "FBA_EU", "name": "Amazon FBA 欧洲仓", "type": "FBA", "country": "DE",
     "region": "EU-Central", "address": "Amazon FBA FRA3, Frankfurt", "is_active": True},
    {"code": "FBA_JP", "name": "Amazon FBA 日本仓", "type": "FBA", "country": "JP",
     "region": "JP-East", "address": "Amazon FBA NRT1, Chiba", "is_active": True},
    {"code": "3PL_USW", "name": "美西 3PL 仓", "type": "3PL", "country": "US",
     "region": "US-West", "address": "123 Logistics Way, Los Angeles, CA", "is_active": True},
    {"code": "3PL_USE", "name": "美东 3PL 仓", "type": "3PL", "country": "US",
     "region": "US-East", "address": "456 Fulfillment Dr, Newark, NJ", "is_active": True},
    {"code": "3PL_DE", "name": "德国 3PL 仓", "type": "3PL", "country": "DE",
     "region": "EU-Central", "address": "789 Lagerhaus Str, Hamburg, DE", "is_active": True},
    {"code": "3PL_JP", "name": "日本 3PL 仓", "type": "3PL", "country": "JP",
     "region": "JP-East", "address": "101 Warehouse Ave, Osaka, JP", "is_active": True},
    {"code": "DOMESTIC_SZ", "name": "深圳国内仓", "type": "DOMESTIC", "country": "CN",
     "region": "CN-South", "address": "深圳市宝安区西乡街道固戍一路 88 号", "is_active": True},
]

# ==================== 品牌 ====================
BRANDS = [
    {"name": "MeridiHome", "trademark_no": "TM-2020-MH001", "owner": "Meridian Global"},
    {"name": "ZenNest", "trademark_no": "TM-2019-ZN002", "owner": "Meridian Global"},
    {"name": "TechGleam", "trademark_no": "TM-2021-TG003", "owner": "Meridian Global"},
    {"name": "AquaPure", "trademark_no": "TM-2020-AP004", "owner": "Meridian Global"},
    {"name": "SmartNest", "trademark_no": "TM-2022-SN005", "owner": "Meridian Global"},
    {"name": "KidSafe", "trademark_no": "TM-2021-KS006", "owner": "Meridian Global"},
    {"name": "OutdoorVibe", "trademark_no": "TM-2022-OV007", "owner": "Meridian Global"},
    {"name": "PetBuddy", "trademark_no": "TM-2023-PB008", "owner": "Meridian Global"},
]

# ==================== 供应商 ====================
SUPPLIER_CITIES = [
    "深圳", "东莞", "宁波", "义乌", "广州", "佛山", "温州", "苏州",
    "中山", "惠州", "汕头", "泉州", "台州", "金华", "厦门",
]

SUPPLIER_TYPES = ["MANUFACTURER", "WHOLESALER"]

PAYMENT_TERMS = ["T/T 30% + 70%", "T/T 50% + 50%", "L/C 90 days", "NET 30", "NET 60"]

# ==================== 产品类目（3 层树） ====================
CATEGORY_TREE = {
    "家居用品": {
        "厨房用品": ["收纳整理", "炊具", "餐具", "烘焙工具"],
        "卫浴用品": ["浴室收纳", "浴帘挂钩", "毛巾架"],
        "家居装饰": ["墙面装饰", "桌面摆件", "灯具"],
        "储物整理": ["衣物收纳", "鞋柜鞋架", "杂物箱"],
    },
    "小型电子产品": {
        "充电设备": ["快充头", "充电线", "无线充电板", "充电宝"],
        "智能家居": ["智能插座", "智能灯带", "传感器"],
        "音频设备": ["蓝牙耳机", "便携音箱", "麦克风"],
        "电脑配件": ["USB Hub", "笔记本支架", "鼠标垫"],
    },
    "户外用品": {
        "露营装备": ["帐篷", "睡袋", "户外照明"],
        "运动配件": ["瑜伽垫", "哑铃", "拉力带"],
    },
    "宠物用品": {
        "宠物玩具": ["咀嚼玩具", "互动玩具", "球类"],
        "宠物护理": ["梳毛工具", "指甲剪", "洗护用品"],
    },
    "母婴用品": {
        "婴儿安全": ["安全门栏", "防撞角", "插座盖"],
        "婴儿护理": ["奶瓶", "温奶器", "婴儿湿巾"],
    },
}

# ==================== 产品名称词库 ====================
PRODUCT_ADJECTIVES = [
    "高级", "便携", "多功能", "简约", "加厚", "折叠", "静音", "防水",
    "速干", "抗菌", "大容量", "可调节", "无线", "智能", "环保", "耐用",
]
PRODUCT_MATERIALS = [
    "不锈钢", "硅胶", "竹木", "ABS", "PC", "铝合金", "TPE", "PP",
    "陶瓷", "碳钢", "玻璃", "牛津布",
]
SKU_COLORS = [
    "黑色", "白色", "灰色", "蓝色", "红色", "绿色", "粉色", "米色",
    "透明", "银色", "金色",
]
SKU_SIZES = ["S", "M", "L", "XL", "标准", "大号", "小号", "迷你"]

# ==================== 物流承运商 ====================
CARRIERS = {
    "SEA": ["COSCO", "Maersk", "CMA CGM", "MSC"],
    "AIR": ["DHL Express", "FedEx International", "UPS Air", "SF Express"],
    "EXPRESS": ["DHL", "FedEx", "UPS", "SF Express"],
    "TRUCK": ["顺丰", "中通", "DHL Freight"],
}

TAIL_CARRIERS = ["USPS", "FedEx", "UPS", "DHL", "Yamato", "Hermes", "Deutsche Post"]

# ==================== 订单状态 ====================
ORDER_STATUSES = [
    "PENDING", "PAID", "ALLOCATED", "PICKING",
    "SHIPPED", "DELIVERED", "CANCELLED", "REFUNDED",
]

# 合法状态转换
VALID_TRANSITIONS = {
    "PENDING": ["PAID", "CANCELLED"],
    "PAID": ["ALLOCATED", "CANCELLED"],
    "ALLOCATED": ["PICKING", "CANCELLED"],
    "PICKING": ["SHIPPED", "CANCELLED"],
    "SHIPPED": ["DELIVERED", "REFUNDED"],
    "DELIVERED": ["REFUNDED"],
    "CANCELLED": [],
    "REFUNDED": [],
}

# ==================== 知识文档分类 ====================
KNOWLEDGE_CATEGORIES = [
    {"code": "AMAZON_SOP", "name": "Amazon SOP"},
    {"code": "LISTING", "name": "Listing 编写规范"},
    {"code": "AD", "name": "广告规范"},
    {"code": "CUSTOMER_SERVICE", "name": "客服 FAQ"},
    {"code": "WAREHOUSE", "name": "仓储制度"},
    {"code": "PRODUCT", "name": "产品资料"},
    {"code": "TRAINING", "name": "培训文档"},
    {"code": "POLICY", "name": "公司制度"},
]

# ==================== 广告平台 ====================
AD_CHANNELS = [
    {"code": "AMAZON_ADS", "name": "Amazon Ads"},
    {"code": "GOOGLE_ADS", "name": "Google Ads"},
    {"code": "META_ADS", "name": "Meta Ads"},
    {"code": "TIKTOK_ADS", "name": "TikTok Ads"},
]

CAMPAIGN_TYPES = ["SP", "PRODUCT_DISPLAY", "BRAND", "VIDEO"]
MATCH_TYPES = ["EXACT", "PHRASE", "BROAD"]
