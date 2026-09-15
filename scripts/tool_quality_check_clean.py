"""scripts/tool_quality_check_clean.py — 薄包装（保留旧入口）

**已废弃为薄包装**：实现统一在 ``scripts/tool_quality_check.py``。

保留原因：历史文档（``.qoder/repowiki`` 里的质量门禁说明）引用的是本文件名，
直接删除会让引用悬空。本文件不再持有任何检查逻辑 —— 两套实现各自维护
必然会漂移，这正是「Tool 漏注册长期无人发现」的成因之一。

用法::

    python scripts/tool_quality_check_clean.py     # 等价于 tool_quality_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tool_quality_check import main  # noqa: E402

if __name__ == "__main__":
    print("注：本入口已废弃，实现见 scripts/tool_quality_check.py\n")
    sys.exit(main())
