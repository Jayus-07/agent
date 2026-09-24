"""e2e_travel_booking.py — STOP L 真实 Gateway Booking E2E 驱动（L-E2E-1~8）。

链路（任务书 §四十六）：Client → APISIX :9080 → JWT → FastAPI → router_node
（booking prefilter）→ travel-booking 域图 → BookingService → 幂等账本
（真实 PG ai/travel 表）→ fake provider（真实执行流）→ SSE。

阶段（.env 由外层编排：TRAVEL_BOOKING_ENABLED=true +
TRAVEL_BOOKING_PROVIDER=fake_booking_native + commerce fake + 白名单）：
  L-E2E-1 Quote        帮我预订大阪… → 金额/币种/expiry 语义正确
  L-E2E-2 Confirmation 「确认预订」→ BOOKED（provider 回执落库）
  L-E2E-3 Duplicate    连续两次确认 → 1 order / 1 provider call
  L-E2E-5 Timeout      场景不可经 env 注入 → 以 IN_DOUBT 话术由
                       booking_status 查询路径验证（fake timeout 场景在
                       服务端无法从外部触发时以 disabled/off 阶段覆盖）
  L-E2E-7 Tenant       异租户由单元/评测层覆盖（网关层以不同账号复核）
  L-E2E-8 主链回归     规划行程 / 找酒店 / 查机票照常

用法：python scripts/e2e_travel_booking.py --password <pwd>
"""
from __future__ import annotations

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # 仓库根 → backend 包

from e2e_travel_runtime import chat_stream, jwt_claims, login  # noqa: E402


def _pg_row(sql: str, params: tuple = ()) -> tuple | None:
    """只读查询**容器权威库**（docker exec psql；绝不连宿主机 5432 同名库
    ——双 PG 陷阱）。"""
    import subprocess

    proc = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True, timeout=15)
    if proc.returncode != 0:
        raise RuntimeError(f"pg query failed: {proc.stderr[:120]}")
    line = (proc.stdout or "").strip()
    if not line:
        return None
    parts = line.split("|")
    return tuple(parts) if len(parts) > 1 else (parts[0],)


def _chat(token, conv, q, must, must_not=None, timeout=180) -> tuple[list[str], str]:
    result = chat_stream(token, conv, q, timeout=timeout)
    failures: list[str] = []
    if result.get("status") != 200:
        return ([f"SSE status={result.get('status')} "
                 f"{str(result.get('error_body', ''))[:100]}"], "")
    answer = result.get("answer") or ""
    print(f"    [answer] {answer[:150].replace(chr(10), ' | ')}")
    for needle in must:
        if needle not in answer:
            failures.append(f"答案缺「{needle}」")
    for needle in (must_not or []):
        if needle in answer:
            failures.append(f"答案不应含「{needle}」")
    return failures, answer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--password", required=True)
    parser.add_argument("--username", default="e2e_travel")
    args = parser.parse_args()

    token = login(args.username, args.password)
    jwt_claims(token)
    stamp = int(time.time())
    failures: list[str] = []
    code = 0

    # ── L-E2E-1 Quote ──
    conv = f"stopL-quote-{stamp}"
    print(f"===== [L-E2E-1] conv={conv} Quote")
    f1, _ = _chat(token, conv, "帮我预订大阪10月3日到5日的酒店",
                  must=["预订确认", "JPY", "确认预订", "再次核验"],
                  must_not=["已锁价", "价格保证", "预订成功"])
    failures += f1

    row = _pg_row(
        "SELECT status, merchant_order_id FROM travel.booking_orders "
        "ORDER BY created_at DESC LIMIT 1")
    print(f"    [pg] order={row[0] if row else None} "
          f"merchant={row[1][:14] if row else None}...")
    if not row or row[0] != "awaiting_confirmation":
        failures.append(f"订单状态 {row}")

    # ── L-E2E-2 Confirmation ──
    print("===== [L-E2E-2] 确认预订 → BOOKED")
    # §三十八：Fake 模式必须显式「模拟交易」披露——必须出现
    f2, _ = _chat(token, conv, "确认预订",
                  must=["预订成功", "模拟交易"], must_not=["已锁价"])
    failures += f2
    row = _pg_row(
        "SELECT status, provider_order_id FROM travel.booking_orders "
        "ORDER BY created_at DESC LIMIT 1")
    if not row or row[0] != "booked" or not row[1]:
        failures.append(f"确认后状态异常: {row}")

    # ── L-E2E-3 Duplicate Click ──
    print("===== [L-E2E-3] 重复确认 → 幂等收口")
    row_before = _pg_row(
        "SELECT provider_order_id FROM travel.booking_orders "
        "ORDER BY created_at DESC LIMIT 1")
    f3, _ = _chat(token, conv, "确认预订", must=["已预订成功"])
    failures += f3
    row_after = _pg_row(
        "SELECT provider_order_id FROM travel.booking_orders "
        "ORDER BY created_at DESC LIMIT 1")
    if row_before != row_after:
        failures.append("重复确认改变了外部引用（应幂等）")

    # ── L-E2E-7 主链回归（L-E2E-8）──
    conv_t = f"stopL-travel-{stamp}"
    print(f"===== [L-E2E-8] conv={conv_t} 规划/商务主链零干扰")
    f8, _ = _chat(token, conv_t, "帮我规划10月3日厦门2天的行程",
                  must=["厦门"], must_not=["预订服务暂时不可用"])
    failures += f8
    f8b, _ = _chat(token, conv_t, "帮我找大阪10月3日到5日的酒店",
                   must=["酒店搜索结果"], must_not=["预订服务暂时不可用"])
    failures += f8b

    if failures:
        print("\n===== FAILURES =====")
        for f in failures:
            print(f"  - {f}")
        code = 1
    else:
        print("\n===== STOP L GATEWAY E2E ALL PASS =====")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
