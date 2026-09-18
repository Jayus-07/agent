"""知识库定义 — KB 是知识集合（非业务域），domain 是内容标签。

字段说明:
  - owner_depts: 负责维护的部门（上传文档时可选范围）
  - audience: 内容受众标签（2026-09-14）——
      "customer": 对客安全（CS 知识问答可输出）
      "internal": 内部制度/业务（仅内部检索场景）
      "test":     测试/评测数据（虚构内容，禁止对客输出）
    跨库兜底（f17 放宽 kb_id）据此推导禁入名单，单一来源在本文件；
    未来检索侧 ACL（allowed_roles）应演进为读取同一标签，而非另立名单。
  - 未来: allowed_roles 控制访问权限，与 owner_depts 不同

用法:
    from backend.config.knowledge_base import KNOWLEDGE_BASES, validate_kb_dept
    kb = KNOWLEDGE_BASES.get("biz_inventory")
    if not validate_kb_dept("biz_inventory", "warehouse"):
        raise ValueError("部门不匹配")
"""

from typing import Dict, List
import os

# ── 知识库定义 ──
KNOWLEDGE_BASES: Dict[str, dict] = {
    "biz_inventory":  {"name": "库存业务知识库", "domain": "inventory",  "owner_depts": ["warehouse", "supply_chain"], "audience": "internal"},
    "biz_order":      {"name": "订单业务知识库", "domain": "order",      "owner_depts": ["order_dept", "customer"], "audience": "internal"},
    "biz_product":    {"name": "商品业务知识库", "domain": "product",    "owner_depts": ["product_dept"], "audience": "internal"},
    "policy_hr":      {"name": "人事制度知识库", "domain": "hr",         "owner_depts": ["hr"], "audience": "internal"},
    "policy_finance": {"name": "财务制度知识库", "domain": "finance",    "owner_depts": ["finance"], "audience": "internal"},
    # 现装的是 HR/财务内部制度文档；若要转为对客通用库，先迁走内部内容再改标签
    "policy_general": {"name": "企业公共制度知识库", "domain": "general", "owner_depts": ["all"], "audience": "internal"},
    # 统一评测库为合成数据，绝不可进入生产兜底检索或对客授权集合。
    "rag_eval_kb":    {"name": "统一 RAG 评测知识库", "domain": "general", "owner_depts": ["all"], "audience": "test"},
    # 旧库只读兼容一个迁移周期；新写入应由评测 profile 转向 rag_eval_kb。
    "rag_test_kb":    {"name": "RAG 小型评测知识库（兼容）", "domain": "general", "owner_depts": ["all"], "audience": "test", "deprecated": True, "read_only": True, "alias_for": "rag_eval_kb"},
    "rag_100_docs":   {"name": "RAG 100 文档评测知识库（兼容）", "domain": "general", "owner_depts": ["all"], "audience": "test", "deprecated": True, "read_only": True, "alias_for": "rag_eval_kb"},
    "cs_faq":         {"name": "客服FAQ", "domain": "customer_service", "owner_depts": ["customer"], "audience": "customer"},
    "cs_product":     {"name": "产品知识库", "domain": "customer_service", "owner_depts": ["customer", "product_dept"], "audience": "customer"},
    "cs_policy":      {"name": "政策知识库", "domain": "customer_service", "owner_depts": ["customer"], "audience": "customer"},
    "cs_aftersales":  {"name": "售后知识库", "domain": "customer_service", "owner_depts": ["customer"], "audience": "customer"},
    "cs_complaint":   {"name": "投诉处理知识库", "domain": "customer_service", "owner_depts": ["customer"], "audience": "customer"},
    "cs_scripts":     {"name": "话术知识库", "domain": "customer_service", "owner_depts": ["customer"], "audience": "customer"},
}


def cross_kb_fallback_excluded() -> list[str]:
    """跨库兜底禁入名单：由 audience 标签推导（单一来源，非人肉名单）。

    audience="test" 的库（现有及未来新建）禁止经 f17 放宽路径召回——
    虚构数据对客输出等同信息安全事故。属性驱动的好处：新建测试库只要
    打上标签自动被挡，不依赖名单维护。

    一键回滚：CROSS_KB_FALLBACK_EXCLUDE_TEST=false 恢复"跨库全放行"旧行为。
    """
    if os.getenv("CROSS_KB_FALLBACK_EXCLUDE_TEST", "true").strip().lower() != "true":
        return []
    return [kb_id for kb_id, info in KNOWLEDGE_BASES.items()
            if info.get("audience") == "test"]


def authorized_kbs(subject_type: str, department: str = "") -> list[str] | None:
    """主体属性 → 可见知识库集合（检索侧授权的单一来源，确定性计算）。

    属性驱动授权（ABAC）：
      - subject_type="customer"：仅 audience=="customer" 的库（cs_*）。
        对客会话（含 CS 知识问答与漏进主图的客服流量）的兜底/显式范围
        都被收敛到这个集合。
      - subject_type="employee"：复用 owner_depts 上传期同一矩阵——
        owner_depts 含其部门或 "all" 的库；test 库对任何主体不可见。
        员工未带部门时按 fail-safe 只见 "all" 库（policy_general）。
      - 其他/空（未声明主体）：返回 None，表示授权未启用——调用方保持
        旧行为（评测/内部直调等显式 kb 场景不受影响）。

    原则：授权是确定性计算（本函数），路由/LLM 只能在授权集合内挑选；
    主体属性在入口解析一次，下游只读。
    """
    if subject_type == "customer":
        return [kb_id for kb_id, info in KNOWLEDGE_BASES.items()
                if info.get("audience") == "customer"]
    if subject_type == "employee":
        out: list[str] = []
        for kb_id, info in KNOWLEDGE_BASES.items():
            if info.get("audience") == "test":
                continue
            depts = info.get("owner_depts", [])
            if "all" in depts or (department and department in depts):
                out.append(kb_id)
        return out
    return None

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
