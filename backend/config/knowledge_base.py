"""知识库定义 — KB 是知识集合（非业务域），domain 是内容标签。

字段说明:
  - owner_depts: 负责维护的部门（上传文档时可选范围）
  - 未来: allowed_roles 控制访问权限，与 owner_depts 不同

用法:
    from backend.config.knowledge_base import KNOWLEDGE_BASES, validate_kb_dept
    kb = KNOWLEDGE_BASES.get("biz_inventory")
    if not validate_kb_dept("biz_inventory", "warehouse"):
        raise ValueError("部门不匹配")
"""

from typing import Dict, List

# ── 知识库定义 ──
KNOWLEDGE_BASES: Dict[str, dict] = {
    "biz_inventory":  {"name": "库存业务知识库", "domain": "inventory",  "owner_depts": ["warehouse", "supply_chain"]},
    "biz_order":      {"name": "订单业务知识库", "domain": "order",      "owner_depts": ["order_dept", "customer"]},
    "biz_product":    {"name": "商品业务知识库", "domain": "product",    "owner_depts": ["product_dept"]},
    "policy_hr":      {"name": "人事制度知识库", "domain": "hr",         "owner_depts": ["hr"]},
    "policy_finance": {"name": "财务制度知识库", "domain": "finance",    "owner_depts": ["finance"]},
    "policy_general": {"name": "企业公共制度知识库", "domain": "general", "owner_depts": ["all"]},
    "rag_test_kb":    {"name": "RAG 评测知识库", "domain": "general", "owner_depts": ["all"]},
}

# 默认知识库（上传未选时回退）
DEFAULT_KB_ID = "policy_general"

# 可选的部门列表
DEPARTMENTS: List[str] = [
    "warehouse", "supply_chain", "order_dept", "customer",
    "product_dept", "hr", "finance", "admin", "general",
]

# 部门中文名映射
DEPT_LABELS: Dict[str, str] = {
    "warehouse":      "仓储部",
    "supply_chain":   "供应链部",
    "order_dept":     "订单部",
    "customer":       "客服部",
    "product_dept":   "商品部",
    "hr":             "人事部",
    "finance":        "财务部",
    "admin":          "行政部",
    "general":        "通用",
    "all":            "全部部门",
}


def validate_kb_dept(kb_id: str, department: str) -> bool:
    """校验 kb_id 和 department 的组合是否允许。
    owner_depts 含 "all" 表示该 KB 对所有部门开放。
    """
    kb = KNOWLEDGE_BASES.get(kb_id)
    if kb is None:
        return False
    depts = kb.get("owner_depts", [])
    if "all" in depts:
        return department in DEPARTMENTS
    return department in depts


def get_kb_list() -> list[dict]:
    """返回 KB 列表（供 API 使用），部门名已转为中文。"""
    result = []
    for kb_id, info in KNOWLEDGE_BASES.items():
        dept_ids = info["owner_depts"]
        if "all" in dept_ids:
            dept_labels = [{"id": d, "label": DEPT_LABELS.get(d, d)} for d in DEPARTMENTS]
        else:
            dept_labels = [{"id": d, "label": DEPT_LABELS.get(d, d)} for d in dept_ids]
        result.append({
            "id": kb_id,
            "name": info["name"],
            "domain": info["domain"],
            "depts": dept_labels,
        })
    return result
