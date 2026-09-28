"""validate_schema_consistency — 发布 preflight：生产关键对象实存校验（STOP A2）

只信真实 schema，不信 schema_migrations 台账（台账已证明会与真实 schema
脱节：018 标 applied 但 prompts 三表被测试 teardown 删除）。

用法（仓库根，宿主机直跑需指权威库端口）：
    PGPORT=5433 python backend/scripts/validate_schema_consistency.py
退出码：0 = ok；1 = drift（缺对象）；2 = unknown（连接失败等）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import backend.app.schema_consistency as sc  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args()

    result = sc.check_critical_objects()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"status={result['status']} checked={result['checked']}")
        for db, info in result["databases"].items():
            print(f"  {db}: {info['status']} (checked={info.get('checked', 0)})")
            for m in info.get("missing", []):
                print(f"    MISSING {m}")
        for m in result["missing"]:
            print(f"MISSING {m}")
    if result["status"] == "ok":
        return 0
    return 1 if result["status"] == "drift" else 2


if __name__ == "__main__":
    raise SystemExit(main())
