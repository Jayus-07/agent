"""ChromaDB KB filter 封装 — 隔离数据库语法差异。

用法:
    from backend.rag.retrieval.kb_filter import build_kb_filter
    f = build_kb_filter(["biz_inventory", "policy_hr"])
    # → {"$or": [{"kb_id": "biz_inventory"}, {"kb_id": "policy_hr"}]}
"""

from typing import List


def build_kb_filter(kb_ids: List[str]) -> dict | None:
    """将 KB ID 列表转为 ChromaDB 兼容的 metadata filter。

    单 KB:  {"kb_id": "biz_inventory"}
    多 KB:  {"$or": [{"kb_id": "a"}, {"kb_id": "b"}]}
    空列表: None（不过滤）
    全选:   None（不过滤）
    """
    if not kb_ids:
        return None

    unique = list(dict.fromkeys(kb_ids))  # 去重保序

    if len(unique) == 1:
        return {"kb_id": unique[0]}

    return {"$or": [{"kb_id": kid} for kid in unique]}
