"""customer_service/pii.py — C11 PII 掩码（实现已上移 shared/pii_mask.py）。

保留 re-export：既有接线（experts/knowledge.py 等）与测试按本路径 import，
语义零变化；新增消费方请直接用 shared/pii_mask.py。
"""

from backend.shared.pii_mask import (  # noqa: F401 — re-export
    PiiVault,
    contains_placeholder,
    mask_pii,
    unmask_text,
)

__all__ = ["PiiVault", "contains_placeholder", "mask_pii", "unmask_text"]
