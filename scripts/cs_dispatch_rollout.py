"""cs_dispatch_rollout.py — P9 放量控制器（shadow → 5% → 20% → 50% → 100%）。

生效机制：``CS_DISPATCH_ROLLOUT_PERCENT`` 已登记进 sys_config 白名单，
DB 覆盖值经 TTL（15s）刷新进程内缓存，**免重启放量**；env 为兜底缺省。
派单侧按 conversation_id 稳定哈希分桶，同一会话要么一直在桶内、要么一直
在桶外（放量过程不会把已派单的会话召回，也不会重复入桶）。

用法：
    python scripts/cs_dispatch_rollout.py --show
    python scripts/cs_dispatch_rollout.py --set 5   --operator <你的标识>
    python scripts/cs_dispatch_rollout.py --set 20  --operator <你的标识>
    python scripts/cs_dispatch_rollout.py --set 50  --operator <你的标识>
    python scripts/cs_dispatch_rollout.py --set 100 --operator <你的标识>

前置校验：``--set`` 前 DB 必须可写、当前模式非 off、且上一档位已稳定
运行（由调用方人工判断；脚本只校验档位合法与记录审计）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from backend.config import cs_dispatch as config  # noqa: E402
from backend.services import sys_config  # noqa: E402

REPORT_DIR = REPO_ROOT / "docs" / "reports"
KEY = "CS_DISPATCH_ROLLOUT_PERCENT"


def _show() -> dict:
    return {
        "key": KEY,
        "env_value": config.CS_DISPATCH_ROLLOUT_PERCENT,
        "ladder": list(config.CS_DISPATCH_ROLLOUT_LADDER),
        "dispatch_mode": config.CS_DISPATCH_MODE,
        "hint": "先 CS_DISPATCH_MODE=shadow 观察算法输出，再按梯子放量",
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--set", type=int, choices=None, metavar="0-100")
    parser.add_argument("--operator", default="")
    args = parser.parse_args()

    await sys_config.refresh_once()

    if args.show or args.set is None:
        info = sys_config.get_info(KEY)
        print(json.dumps({"current": info, **_show()}, ensure_ascii=False, indent=2))
        return 0

    percent = args.set
    if not 0 <= percent <= 100:
        print(f"非法档位 {percent}：合法范围 0-100；推荐梯子 {config.CS_DISPATCH_ROLLOUT_LADDER}")
        return 1
    if not args.operator.strip():
        print("--operator 必填（审计留痕，如 user:mint 或 changeticket-123）")
        return 1
    if config.CS_DISPATCH_MODE == "off":
        print("CS_DISPATCH_MODE=off：先切 shadow 观察后再放量，拒绝直接从 off 跳到 enforce 档位")
        return 1

    result = await sys_config.set_value(KEY, str(percent), args.operator.strip())
    audit = {
        "action": "rollout_set",
        "key": KEY,
        "old": result.get("old"),
        "new": result.get("new"),
        "operator": args.operator.strip(),
        "mode": config.CS_DISPATCH_MODE,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = audit["at"][:19].replace(":", "").replace("-", "")
    out = REPORT_DIR / f"cs-dispatch-rollout-{stamp}.json"
    out.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    print(f"audit -> {out}")
    print("生效：API 实例最坏 1 个 TTL（15s）后按新百分比放量；dispatcher 不需要重启。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
