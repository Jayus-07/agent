#!/usr/bin/env python
"""gateway_rate_limit_check.py — 网关限流专项回归（纯标准库，无外部依赖）

对 APISIX 测试实例（9081）的 /limited 专项路由逐场景验证限流行为，
产出结构化 JSON 报告（.workbuddy/gateway_rate_limit_report.json）。

/limited 配额（apisix-test/apisix.yaml，刻意取小让断言快）：
  limit-conn  key=http_x_user_id  conn=2 burst=1  rejected 429
  limit-count key=http_x_user_id  count=5 / 60s   rejected 429

场景清单（2026-09-16 P1 增强）：
  1  count_limit         第 6 次请求触发 429（前 5 次 200）
  2  per_user_isolation  另一用户首请求 200（配额按用户分桶，不互相挤占）
  3  conn_limit          4 个并发慢请求（?delay=3）→ 至少 1 个 429、至少 2 个 200
                         （容量 = conn 2 + burst 1 = 3，第 4 个并发被拒）
  4  ip_fallback         无 Bearer 仅 X-API-Key 的通道（网关不注入用户头）→
                         limit-count 键回退 remote_addr → 第 6 次触发 429

前置：bash scripts/run_gateway_test_stack.sh 起台架（echo 桩 8099 + 测试实例 9081）。

用法：
  python scripts/gateway_rate_limit_check.py --base http://127.0.0.1:9081 \
      --secret "$SECRET64"

退出码：任一场景失败 → 1。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from urllib import error, request

# 复用认证矩阵的 JWT 生成逻辑（合同一致：HS512 / iss=hongmeng-oa / type=access）
sys.path.insert(0, __file__.rsplit("/", 1)[0].replace("\\", "/") or ".")
from gateway_auth_matrix import access_claims, make_jwt, valid_token  # noqa: E402

UA = "gateway-rate-limit-check/1.0"


def http_get(base: str, path: str, token: str | None = None,
             api_key: str | None = None) -> tuple[int, dict, float]:
    """GET 请求，返回 (状态码, 响应头, 耗时秒)。连接失败返回 (0, {}, 耗时)。"""
    req = request.Request(base + path, method="GET", headers={"User-Agent": UA})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if api_key:
        req.add_header("X-API-Key", api_key)
    started = time.monotonic()
    try:
        with request.urlopen(req, timeout=35) as resp:
            return resp.status, dict(resp.headers), time.monotonic() - started
    except error.HTTPError as e:
        return e.code, dict(e.headers or {}), time.monotonic() - started
    except Exception:
        return 0, {}, time.monotonic() - started


RESULTS: list[dict] = []


def record(name: str, expect: str, actual: str, ok: bool, note: str = "") -> None:
    RESULTS.append({"name": name, "expect": expect, "actual": actual,
                    "pass": ok, "note": note})
    print(f"  {'OK  ' if ok else 'FAIL'} {name}: {actual}"
          + (f"  [{note}]" if note and not ok else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:9081")
    ap.add_argument("--secret", required=True, help="测试用 JWT_SECRET（与测试容器 env 一致）")
    ap.add_argument("--issuer", default="hongmeng-oa")
    ap.add_argument("--report", default=".workbuddy/gateway_rate_limit_report.json")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    # userId 随机化：limit-count 窗口 60s，固定 id 会因上一轮运行残留配额而假红
    uid = 20000 + (int(time.time()) % 1000) * 10
    user_a = valid_token(args.secret, userId=uid, username="rl-user-a", iss=args.issuer)
    user_b = valid_token(args.secret, userId=uid + 1, username="rl-user-b", iss=args.issuer)
    user_c = valid_token(args.secret, userId=uid + 2, username="rl-user-c", iss=args.issuer)
    api_key = "rl-check-service-key"

    print(f"目标: {base}  /limited 配额: count=5/60s, conn=2+burst=1")

    # ── 1 计数限流：第 6 次 429 ──────────────────────────────
    statuses = []
    for i in range(6):
        st, headers, _ = http_get(base, "/limited", token=user_a)
        statuses.append(st)
    ok = statuses[:5] == [200] * 5 and statuses[5] == 429
    record("count_limit", "200×5 → 429", str(statuses), ok)

    # ── 2 用户隔离：另一用户首请求不受 user-a 配额影响 ────────
    st, _, _ = http_get(base, "/limited", token=user_b)
    record("per_user_isolation", "200（user-b 独立配额）", str(st), st == 200)

    # ── 3 并发限流：4 个并发慢请求，容量 3 → 至少 1 个 429 ────
    outcomes: list[int] = []
    lock = threading.Lock()

    def hit() -> None:
        st, _, _ = http_get(base, "/limited?delay=3", token=user_c)
        with lock:
            outcomes.append(st)

    threads = [threading.Thread(target=hit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = outcomes.count(429) >= 1 and outcomes.count(200) >= 2
    record("conn_limit", "≥1×429 且 ≥2×200（容量3）", str(sorted(outcomes)), ok)

    # ── 4 IP 回退：api-key 通道无用户头 → 键回退 remote_addr ──
    statuses = []
    for _ in range(8):
        st, _, _ = http_get(base, "/limited", api_key=api_key)
        statuses.append(st)
        if st == 429:
            break
    # 本机单 IP 发起：第 6 次应 429（前 5 次共享同一 IP 桶）
    ok = statuses[:5] == [200] * 5 and 429 in statuses
    record("ip_fallback", "200×5 → 429（IP 桶）", str(statuses), ok)

    passed = [r for r in RESULTS if r["pass"]]
    failed = [r for r in RESULTS if not r["pass"]]
    report = {
        "mode": "rate-limit",
        "base": base,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": f"{len(passed)}/{len(RESULTS)} passed",
        "results": RESULTS,
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告: {args.report}  结果: {len(passed)}/{len(RESULTS)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
