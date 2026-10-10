"""V1 旅游版本账本巡检入口已停用。

旧表由迁移 085 删除；V2 版本历史通过 travel_v2.trip_revisions 管理，
请改用 V2 数据层验收与巡检。此脚本刻意不连接数据库，避免旧表被误创建。
"""
from __future__ import annotations

import sys


def main() -> int:
    print(
        "[reconcile] 已停用：V1 travel_plan_versions / "
        "travel_decision_audit 不再使用。"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
