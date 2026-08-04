"""KB 路由规则 — 关键词 → kb_id 映射。

升级路径:
  v1: 关键词权重计分 (<5ms, 零成本)
  v2: embedding similarity (升级时接口不变)
  v3: 小模型 classifier (升级时接口不变)
"""

from typing import Dict, List

# 每个 KB 的关键词列表（匹配到任一关键词即得分）
KB_ROUTING_RULES: Dict[str, List[str]] = {
    "biz_inventory":  ["库存", "仓库", "盘点", "入库", "出库", "调拨", "安全库存", "FBA", "滞销", "周转"],
    "biz_order":      ["订单", "退款", "发货", "签收", "售后", "履约", "拆单", "退货", "包裹"],
    "biz_product":    ["商品", "SKU", "上架", "下架", "Listing", "规格", "变体", "品类", "条码"],
    "policy_hr":      ["请假", "考勤", "入职", "离职", "绩效", "薪酬", "培训", "晋升"],
    "policy_finance": ["报销", "预算", "财务", "发票", "付款", "账务", "审计"],
    "policy_general": ["制度", "规范", "标准", "流程", "合规"],
}

# 兜底 KB（无关键词命中时使用）
FALLBACK_KB = "policy_general"

# 最大候选 KB 数（避免全库检索）
MAX_KB_CANDIDATES = 5
