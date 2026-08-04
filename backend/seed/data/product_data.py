"""产品种子数据 — 从 generators/product.py 抽出。"""
# ======================= 产品名称词库 =======================

# 产品类型（中英对照，用于生成真实感名称）
PRODUCT_TYPES = [
    ("收纳盒", "Storage Box"), ("收纳筐", "Storage Basket"),
    ("置物架", "Shelf Organizer"), ("挂钩", "Hooks"),
    ("浴帘", "Shower Curtain"), ("毛巾架", "Towel Rack"),
    ("肥皂架", "Soap Dish"), ("牙刷架", "Toothbrush Holder"),
    ("快充头", "Fast Charger"), ("充电线", "Charging Cable"),
    ("无线充电板", "Wireless Charging Pad"), ("充电宝", "Power Bank"),
    ("智能插座", "Smart Plug"), ("智能灯带", "Smart LED Strip"),
    ("蓝牙耳机", "Bluetooth Earbuds"), ("便携音箱", "Portable Speaker"),
    ("USB Hub", "USB Hub"), ("笔记本支架", "Laptop Stand"),
    ("鼠标垫", "Mouse Pad"), ("瑜伽垫", "Yoga Mat"),
    ("哑铃", "Dumbbell Set"), ("拉力带", "Resistance Band"),
    ("帐篷", "Tent"), ("睡袋", "Sleeping Bag"),
    ("户外灯", "Outdoor Lantern"), ("宠物玩具", "Pet Toy"),
    ("宠物梳", "Pet Brush"), ("安全门栏", "Safety Gate"),
    ("防撞角", "Corner Protector"), ("奶瓶", "Baby Bottle"),
    ("硅胶模具", "Silicone Mold"), ("烘焙垫", "Baking Mat"),
    ("厨房秤", "Kitchen Scale"), ("密封罐", "Airtight Container"),
    ("调味瓶", "Seasoning Bottle"), ("沥水篮", "Colander"),
    ("刀具套装", "Knife Set"), ("砧板", "Cutting Board"),
    ("衣物收纳袋", "Garment Storage Bag"), ("鞋柜", "Shoe Rack"),
]

# Listing 标题模板
TITLE_PATTERNS = [
    "{adj} {product_type} - {material} {feature}",
    "{adj} {product_type} | {feature} | {material}",
    "{feature} {product_type} - {adj} {material} 材质",
    "{adj} {material} {product_type} for {use_case}",
]

FEATURES = [
    "便携折叠", "大容量", "多功能", "加厚耐用", "防水防潮",
    "速干透气", "抗菌防霉", "可调节", "静音设计", "环保安全",
    "易清洗", "免打孔", "防滑稳固", "一机多用", "轻量便携",
]

USE_CASES = [
    "Home Kitchen", "Bathroom Organization", "Office Desk",
    "Outdoor Camping", "Pet Care", "Baby Safety",
    "Travel Essentials", "Daily Storage",
]

# Bullet point 模板
BULLET_TEMPLATES = [
    "【{feature1}】{desc1}",
    "【{feature2}】{desc2}",
    "【{feature3}】{desc3}",
    "【{feature4}】{desc4}",
    "【{feature5}】{desc5}",
]

BULLET_DESCRIPTIONS = [
    ("高品质材质", "采用优质{0}制造，坚固耐用，使用寿命长达{1}年以上"),
    ("安全环保", "通过FDA/CE认证，{0}材质，无毒无味，家人使用更安心"),
    ("人性化设计", "{0}设计，{1}，使用体验极佳"),
    ("多场景适用", "适用于{0}、{1}、{2}等多种场景，一物多用"),
    ("便携收纳", "可折叠设计，节省{0}%空间，方便收纳和携带"),
    ("易于清洁", "表面{0}处理，一擦即净，免去繁琐清洁"),
    ("承载力强", "加强结构设计，最大承重可达{0}kg，稳固不摇晃"),
    ("售后服务", "30天无理由退换，{0}年质保，专业客服{1}小时响应"),
]

# 知识模板（PR-2 也用到）
KNOWLEDGE_TEMPLATES = {
    "AMAZON_SOP": [
        {
            "title": "Amazon 账号注册流程与注意事项",
            "content": (
                "# Amazon Seller Central 账号注册流程\n\n"
                "## 1. 准备工作\n"
                "- 公司营业执照（需在有效期内）\n"
                "- 法人身份证正反面扫描件\n"
                "- 双币信用卡（Visa/Mastercard）\n"
                "- 收款账户（Payoneer/WorldFirst/PingPong）\n"
                "- 专用邮箱（建议 Gmail/Outlook）\n\n"
                "## 2. 注册步骤\n"
                "1. 访问 sellercentral.amazon.com，点击\"注册\"\n"
                "2. 填写公司信息（名称、地址、统一社会信用代码）\n"
                "3. 上传营业执照和法人身份证\n"
                "4. 填写信用卡信息用于月租扣费\n"
                "5. 完成税务调查（W-8BEN 表格）\n"
                "6. 等待审核（通常 1-3 个工作日）\n\n"
                "## 3. 常见被拒原因\n"
                "- 营业执照模糊不清\n"
                "- 身份证与营业执照法人不一致\n"
                "- 信用卡信息与注册地址不匹配\n"
                "- 关联账号风险（同一 IP 多账号）\n\n"
                "## 4. KYC 审核\n"
                "欧洲站需额外通过 KYC 审核，需提供：\n"
                "- 公司注册证明\n"
                "- 主要联系人身份证明\n"
                "- 银行账户证明\n"
                "- 地址证明（近 90 天内）\n"
            ),
        },
        {
            "title": "Amazon Listing 优化完整指南",
            "content": (
                "# Amazon Listing 优化指南\n\n"
                "## 标题优化\n"
                "- 格式: [品牌] + [核心词] + [属性词] + [场景词] + [规格]\n"
                "- 长度: 150-200 字符（移动端截断前 80 字符）\n"
                "- 禁忌: 禁止价格/促销信息/主观夸大词（#1/Best/Amazing）\n\n"
                "## 五点描述\n"
                "- 每条不超过 500 字符\n"
                "- 第一条: 产品最大卖点\n"
                "- 第二条: 材质/规格/尺寸\n"
                "- 第三条: 使用场景\n"
                "- 第四条: 包装内容/兼容性\n"
                "- 第五条: 售后保障/品质承诺\n\n"
                "## A+ 内容\n"
                "- 需完成品牌备案 2.0\n"
                "- 模块类型: 标准图文/对比表/产品图集/视频\n"
                "- 图片要求: 1500×1500px 以上，白色背景占 85%\n\n"
                "## 关键词策略\n"
                "- 后端关键词: 250 字节，用空格分隔，不用逗号\n"
                "- 不要重复标题中已出现的词\n"
                "- 包含同义词/英式美式拼写/常见拼写错误\n"
                "- 搜索词报告每周下载优化\n"
            ),
        },
    ],
    "LISTING": [
        {
            "title": "Listing 五点描述写作模板",
            "content": (
                "# 五点描述（Bullet Points）写作模板\n\n"
                "## 公式\n"
                "【卖点标签】+ 产品特性 + 客户价值 + 场景/数据支撑\n\n"
                "## 模板 1: 家居收纳类\n"
                "1. 【大容量收纳】可容纳 XXX，相当于传统收纳盒的 X 倍\n"
                "2. 【折叠设计】不用时可折叠至 X cm 厚度，节省 80% 空间\n"
                "3. 【加厚材质】X mm 加厚 PP 板材，承重达 X kg，稳固不摇晃\n"
                "4. 【多场景】适用于衣柜/客厅/办公室/儿童房，随心搭配\n"
                "5. 【无忧售后】30 天无理由退换，1 年质保，终身技术支持\n\n"
                "## 模板 2: 电子产品类\n"
                "1. 【快充协议】支持 PD 3.0/QC 4.0，30 分钟充至 80%\n"
                "2. 【广泛兼容】适配 iPhone/Samsung/Pixel/平板/Switch\n"
                "3. 【安全保护】过压/过流/短路/过温 4 重保护，UL/FCC 认证\n"
                "4. 【耐用设计】X 次插拔测试，X mm 加强线径\n"
                "5. 【包装清单】充电器×1，USB-C 线×1（X m），说明书×1\n"
            ),
        },
    ],
    "CUSTOMER_SERVICE": [
        {
            "title": "物流时效常见问题 FAQ",
            "content": (
                "# 物流时效 FAQ\n\n"
                "## Q: 美国站标准配送多久送达？\n"
                "A: FBA 订单通常 2-3 个工作日送达。自发货通常 7-15 个工作日（经济线）"
                "或 5-8 个工作日（标准线）。\n\n"
                "## Q: 如何查询物流轨迹？\n"
                "A: 登录 Amazon → Your Orders → Track Package。"
                "自发货可访问承运商官网，输入追踪号查询。\n\n"
                "## Q: 显示已签收但未收到包裹？\n"
                "A: 1) 检查门口/前台/快递柜 2) 询问家人/室友 3) 联系承运商核实签收人"
                " 4) 如仍未找到，联系我们补发/退款。\n\n"
                "## Q: 欧洲站关税谁承担？\n"
                "A: FBA 订单关税已由 Amazon 代收代付（IOSS）。"
                "自发货如产生关税，请联系我们凭海关收据报销。\n\n"
                "## Q: 可以修改收货地址吗？\n"
                "A: 发货前可修改。已发货订单无法修改地址，建议联系承运商申请改派。\n"
            ),
        },
        {
            "title": "退货政策与流程",
            "content": (
                "# 退货政策与流程\n\n"
                "## 退货政策\n"
                "- 30 天无理由退换（自发货商品）\n"
                "- FBA 商品遵循 Amazon 退货政策（通常 30 天）\n"
                "- 开封/使用过的商品需扣除折旧费（最高 50%）\n"
                "- 以下情况不接受退货: 定制商品、内衣裤、已激活软件\n\n"
                "## 退货流程\n"
                "1. 买家发起退货请求（Amazon → Your Orders → Return）\n"
                "2. 系统自动审批（FBA）或客服审批（自发货）\n"
                "3. 提供退货地址（美国海外仓/国内收件地址）\n"
                "4. 买家寄回商品，提供追踪号\n"
                "5. 仓库签收 → 检测（1-3 个工作日）\n"
                "6. 检测合格 → 退款到原支付方式（3-5 个工作日）\n"
                "7. 检测不合格 → 联系买家说明原因，部分退款或拒绝\n\n"
                "## 退货地址\n"
                "- 美国: 123 Returns Center, Los Angeles, CA 90001\n"
                "- 德国: 456 Retouren Zentrum, Hamburg, DE 20457\n"
            ),
        },
    ],
    "WAREHOUSE": [
        {
            "title": "FBA 发货要求与包装规范",
            "content": (
                "# FBA 发货要求\n\n"
                "## 标签要求\n"
                "- 每个外箱贴 2 张 FBA 箱唛（相邻两侧）\n"
                "- 每件商品贴 FNSKU 标签（覆盖原 UPC/EAN）\n"
                "- 标签尺寸: 箱唛 100×150mm，FNSKU 30×50mm\n"
                "- 使用热敏纸或激光打印（禁止喷墨打印）\n\n"
                "## 包装要求\n"
                "- 外箱: 6 面完整，无破损，承重 ≥ 30kg\n"
                "- 箱重: 单箱不超过 23kg（超过需贴\"Team Lift\"标签）\n"
                "- 易碎品: 气泡膜包裹 + \"Fragile\"标识\n"
                "- 液体: 密封袋 + 吸水纸，需通过 ISTA 6 测试\n\n"
                "## 托盘要求\n"
                "- 尺寸: 1200×1000mm（欧标）或 1200×800mm\n"
                "- 高度: 含托盘不超过 1800mm\n"
                "- 缠绕膜: 纵向缠绕 ≥ 3 圈\n"
            ),
        },
    ],
    "PRODUCT": [
        {
            "title": "家居收纳产品规格说明 — 收纳盒系列",
            "content": (
                "# MeridiHome 收纳盒系列 产品规格\n\n"
                "## 材质\n"
                "- 主体: PP（聚丙烯），食品级\n"
                "- 盖子: 透明 PET，可透视内容物\n"
                "- 密封圈: 硅胶，耐温 -40°C ~ 200°C\n\n"
                "## 系列规格\n"
                "| 型号 | 尺寸 (L×W×H mm) | 容量 (L) | 重量 (g) |\n"
                "|------|-----------------|----------|----------|\n"
                "| S | 200×150×120 | 2.5 | 180 |\n"
                "| M | 260×180×150 | 5.0 | 290 |\n"
                "| L | 320×220×180 | 9.0 | 420 |\n"
                "| XL | 380×280×220 | 16.0 | 620 |\n\n"
                "## 适用场景\n"
                "- 厨房: 收纳五谷杂粮、调味料、干货\n"
                "- 冰箱: 分类食材，防串味\n"
                "- 衣柜: 收纳袜子、内衣、配饰\n"
                "- 儿童房: 收纳玩具、积木\n"
            ),
        },
    ],
    "TRAINING": [
        {
            "title": "新员工入职培训手册 — 运营岗",
            "content": (
                "# 运营岗位入职培训大纲\n\n"
                "## Day 1: 公司概览\n"
                "- 公司简介、品牌矩阵、销售渠道\n"
                "- 组织架构、汇报关系、协作工具\n"
                "- IT 账号开通（企业邮箱/Slack/Asana/Google Drive）\n\n"
                "## Day 2-3: 平台基础\n"
                "- Amazon Seller Central 界面熟悉\n"
                "- 订单管理（查看/导出/处理异常）\n"
                "- 库存管理（补货提醒/创建发货计划）\n"
                "- 广告后台基础（Campaign Manager）\n\n"
                "## Day 4-5: Listing 实操\n"
                "- Listing 创建流程（单一/变体/批量上传）\n"
                "- 标题/五点/A+/关键词 规范\n"
                "- 图片规范与审核要点\n"
                "- 上架后 Checklist 验证\n\n"
                "## Week 2: 进阶\n"
                "- 数据分析（Business Report / Brand Analytics）\n"
                "- 广告优化（关键词调价/否定词/ACoS 控制）\n"
                "- 竞品分析（Keepa / Helium 10 / Jungle Scout）\n"
                "- 差评处理与客户沟通\n"
            ),
        },
    ],
    "POLICY": [
        {
            "title": "员工差旅报销制度",
            "content": (
                "# 差旅报销制度\n\n"
                "## 适用范围\n"
                "所有因公出差的正式员工\n\n"
                "## 交通标准\n"
                "- 高铁: 二等座\n"
                "- 飞机: 经济舱（飞行 4h 以上可申请商务舱）\n"
                "- 市内交通: 实报实销（的士/网约车）\n\n"
                "## 住宿标准\n"
                "- 一线城市: ≤ 500 元/晚\n"
                "- 二线城市: ≤ 350 元/晚\n"
                "- 三线及以下: ≤ 250 元/晚\n\n"
                "## 餐补标准\n"
                "- 早餐: 30 元\n"
                "- 午餐: 50 元\n"
                "- 晚餐: 60 元\n\n"
                "## 报销流程\n"
                "1. 出差前提交 OA 申请（至少提前 2 个工作日）\n"
                "2. 出差后 5 个工作日内提交报销单\n"
                "3. 附发票原件（电子发票打印）\n"
                "4. 直属上级审批 → 财务审核 → 打款\n"
            ),
        },
    ],
}


