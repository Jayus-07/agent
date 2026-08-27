"""把扩充后的文档类型关键词批量灌入动态词库 keyword_rules.db。

运行方式（务必从 backend/ 目录执行，使 db 落在 backend/data/keyword_rules.db，
与分类器 classify_with_confidence 读取的路径一致）：

    cd backend
    python ../scripts/expand_keyword_store.py

说明：
- 这些词与 domain_data.DOC_TYPE_RULES 同步扩充，但落到「动态词库」后
  可在管理页在线增删、热加载（60s 生效），无需改代码。
- 已有的种子词会被 upsert 更新（source 标记为 manual），不会丢。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(os.path.dirname(HERE), "backend")
# backend 是包，需要从「工作区根目录」导入 backend.rag...
WORKSPACE = os.path.dirname(HERE)
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from backend.rag.preprocessing.keyword_store import get_keyword_store

# (keyword, doc_type, weight) —— 与 DOC_TYPE_RULES 保持同步
_ITEMS = [
    # listing
    ("listing", "listing", 2), ("五点描述", "listing", 2), ("A+内容", "listing", 2),
    ("关键词策略", "listing", 2), ("标题公式", "listing", 2), ("主图规范", "listing", 2),
    ("附图规范", "listing", 2), ("搜索词", "listing", 2), ("搜索排名", "listing", 2),
    ("bsr", "listing", 2), ("bestseller", "listing", 2), ("商品标题", "listing", 2),
    ("卖点", "listing", 2), ("详情页", "listing", 2), ("类目节点", "listing", 2),
    ("变体", "listing", 2), ("父体", "listing", 2), ("子体", "listing", 2),
    ("bullet point", "listing", 2), ("五点", "listing", 2), ("listing优化", "listing", 2),
    # sop
    ("sop", "sop", 2), ("标准操作", "sop", 2), ("操作流程", "sop", 2), ("标准作业", "sop", 2),
    ("作业指导书", "sop", 2), ("作业指导", "sop", 2), ("操作手册", "sop", 2),
    ("标准流程", "sop", 2), ("操作规程", "sop", 2), ("工作指引", "sop", 2),
    ("操作规范", "sop", 2), ("作业标准", "sop", 2), ("步骤说明", "sop", 2),
    ("岗位操作", "sop", 2), ("标准作业程序", "sop", 2), ("岗位规范", "sop", 2),
    # ad_policy
    ("广告政策", "ad_policy", 2), ("投放规则", "ad_policy", 2), ("amazon ads", "ad_policy", 2),
    ("竞价策略", "ad_policy", 2), ("广告规范", "ad_policy", 2), ("广告活动", "ad_policy", 2),
    ("广告组", "ad_policy", 2), ("投放策略", "ad_policy", 2), ("广告合规", "ad_policy", 2),
    ("推广规则", "ad_policy", 2), ("ppc", "ad_policy", 2), ("广告政策违规", "ad_policy", 2),
    ("广告投放政策", "ad_policy", 2), ("品牌推广", "ad_policy", 2), ("广告合规要求", "ad_policy", 2),
    # faq
    ("faq", "faq", 2), ("常见问题", "faq", 2), ("售后faq", "faq", 2), ("问答文档", "faq", 2),
    ("退货政策", "faq", 2), ("物流时效", "faq", 2), ("售后流程", "faq", 2), ("退换货", "faq", 2),
    ("退款流程", "faq", 2), ("帮助中心", "faq", 2), ("客户问答", "faq", 2), ("问题解答", "faq", 2),
    ("退换货政策", "faq", 2), ("售后指南", "faq", 2), ("答疑", "faq", 2), ("问答", "faq", 2),
    ("客服问答", "faq", 2),
    # product_spec
    ("产品规格", "product_spec", 2), ("材质说明", "product_spec", 2), ("使用手册", "product_spec", 2),
    ("保养指南", "product_spec", 2), ("故障排查", "product_spec", 2), ("规格参数", "product_spec", 2),
    ("技术参数", "product_spec", 2), ("产品说明书", "product_spec", 2), ("参数表", "product_spec", 2),
    ("产品特性", "product_spec", 2), ("功能介绍", "product_spec", 2), ("规格书", "product_spec", 2),
    ("配置清单", "product_spec", 2), ("型号对照", "product_spec", 2), ("产品参数", "product_spec", 2),
    ("技术规格", "product_spec", 2),
    # training
    ("培训", "training", 2), ("新人手册", "training", 2), ("上岗", "training", 2),
    ("考核", "training", 2), ("培训材料", "training", 2), ("培训资料", "training", 2),
    ("入职培训", "training", 2), ("岗前培训", "training", 2), ("操作培训", "training", 2),
    ("培训课件", "training", 2), ("学习手册", "training", 2), ("培训计划", "training", 2),
    ("带教", "training", 2), ("实习手册", "training", 2), ("培训课程", "training", 2),
    ("新人培训", "training", 2),
    # policy
    ("制度", "policy", 2), ("规范", "policy", 2), ("审批", "policy", 2), ("规定", "policy", 2),
    ("管理条例", "policy", 2), ("管理办法", "policy", 2), ("管理规定", "policy", 2),
    ("管理制度", "policy", 2), ("规章制度", "policy", 2), ("实施细则", "policy", 2),
    ("规则", "policy", 2), ("守则", "policy", 2), ("工作制度", "policy", 2),
    ("管控要求", "policy", 2), ("暂行", "policy", 2), ("工作条例", "policy", 2),
    ("管理办法实施细则", "policy", 2),
    # compliance
    ("合规", "compliance", 2), ("法规", "compliance", 2), ("监管", "compliance", 2),
    ("gdpr", "compliance", 2), ("ccpa", "compliance", 2), ("数据保护", "compliance", 2),
    ("个人信息", "compliance", 2), ("隐私政策", "compliance", 2), ("数据安全法", "compliance", 2),
    ("等保", "compliance", 2), ("网络安全法", "compliance", 2), ("合规审查", "compliance", 2),
    ("监管要求", "compliance", 2), ("审计", "compliance", 2), ("备案", "compliance", 2),
    ("反垄断", "compliance", 2), ("iso", "compliance", 2), ("网信办", "compliance", 2),
    ("合规风险", "compliance", 2), ("行政处罚", "compliance", 2), ("监管检查", "compliance", 2),
    ("合规管理", "compliance", 2),
    # legal
    ("合同", "legal", 2), ("条款", "legal", 2), ("违约责任", "legal", 2), ("赔偿", "legal", 2),
    ("知识产权", "legal", 2), ("保密协议", "legal", 2), ("法律", "legal", 2), ("协议", "legal", 2),
    ("诉讼", "legal", 2), ("仲裁", "legal", 2), ("纠纷", "legal", 2), ("侵权", "legal", 2),
    ("章程", "legal", 2), ("要约", "legal", 2), ("担保", "legal", 2), ("执照", "legal", 2),
    ("法律意见", "legal", 2), ("法律文书", "legal", 2), ("调解书", "legal", 2), ("判决书", "legal", 2),
    ("法律顾问", "legal", 2), ("合规协议", "legal", 2), ("契约", "legal", 2), ("法务", "legal", 2),
    # security
    ("安全", "security", 2), ("权限", "security", 2), ("访问控制", "security", 2),
    ("加密", "security", 2), ("漏洞", "security", 2), ("认证", "security", 2),
    ("信息安全", "security", 2), ("数据安全", "security", 2), ("网络安全", "security", 2),
    ("身份认证", "security", 2), ("防火墙", "security", 2), ("渗透测试", "security", 2),
    ("安全策略", "security", 2), ("零信任", "security", 2), ("数据防泄漏", "security", 2),
    ("安全审计", "security", 2), ("权限管理", "security", 2), ("安全合规", "security", 2),
    ("访问控制策略", "security", 2),
    # financial
    ("财务", "financial", 2), ("报销", "financial", 2), ("预算", "financial", 2),
    ("发票", "financial", 2), ("付款审批", "financial", 2), ("对账", "financial", 2),
    ("坏账", "financial", 2), ("账务", "financial", 2), ("账户", "financial", 2),
    ("账簿", "financial", 2), ("账目", "financial", 2), ("利润表", "financial", 2),
    ("资产负债表", "financial", 2), ("现金流量表", "financial", 2), ("营收", "financial", 2),
    ("税务", "financial", 2), ("薪酬", "financial", 2), ("成本", "financial", 2),
    ("报表", "financial", 2), ("审计", "financial", 2), ("资金", "financial", 2),
    ("核算", "financial", 2), ("毛利", "financial", 2), ("净利", "financial", 2),
    ("决算", "financial", 2), ("税务申报", "financial", 2), ("财务报表", "financial", 2),
    # customer_data
    ("客户数据", "customer_data", 2), ("个人信息", "customer_data", 2), ("用户隐私", "customer_data", 2),
    ("数据收集", "customer_data", 2), ("用户画像", "customer_data", 2), ("客户信息", "customer_data", 2),
    ("用户数据", "customer_data", 2), ("隐私数据", "customer_data", 2), ("个人数据", "customer_data", 2),
    ("数据主体", "customer_data", 2), ("数据泄露", "customer_data", 2), ("数据脱敏", "customer_data", 2),
    ("个人信息保护", "customer_data", 2), ("用户资料", "customer_data", 2), ("隐私保护", "customer_data", 2),
    # contract_template
    ("合同模板", "contract_template", 2), ("协议模板", "contract_template", 2),
    ("标准条款", "contract_template", 2), ("模板", "contract_template", 2),
    ("合同范本", "contract_template", 2), ("协议范本", "contract_template", 2),
    ("范本", "contract_template", 2), ("标准合同", "contract_template", 2),
    ("格式合同", "contract_template", 2), ("模板库", "contract_template", 2),
    ("合同范本库", "contract_template", 2), ("协议范本库", "contract_template", 2),
]


def main():
    import json
    store = get_keyword_store()
    before = len(store.list_all())
    items = [
        {"keyword": kw, "doc_type": dt, "weight": w, "category": dt, "enabled": 1}
        for kw, dt, w in _ITEMS
    ]
    res = store.batch_upsert(items)
    after = len(store.list_all())
    print(f"submitted={len(items)} before={before} after={after} delta={after - before}")
    # 按 doc_type 统计
    print("per doc_type (动态词库):")
    for dt in sorted(store.list_doc_types()):
        n = len(store.list_all(doc_type=dt))
        print(f"  {dt}: {n}")


if __name__ == "__main__":
    main()
