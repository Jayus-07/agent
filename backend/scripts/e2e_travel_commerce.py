"""e2e_travel_commerce.py — STOP K 真实 Gateway Commerce E2E 驱动（K-E2E-C1~C3）。

链路（任务书 §三十）：Client → APISIX :9080 → JWT → FastAPI → router_node
（commerce prefilter）→ travel-commerce 域图 → commerce service →
Provider Layer（共享缓存/quota）→ fake 适配器 → SSE。

三阶段（.env 由外层编排切换 + app 重建）：
  phase a（TRAVEL_COMMERCE_ENABLED=true, MODE=fake, 白名单, 预算=10）：
    K-E2E-C2-hotel  真实链路出 offer（快照/新鲜度/币种/链接语义）+ replay
                    零增量（quota 计数不变，G17）
    K-E2E-C2-flight 航班 offer（航段/中转/舱位语义）
    K-E2E-G28       旅游规划主链不受 Commerce 影响（行程照常出单）
  phase b（ENABLED=true, MODE=off）：
    K-E2E-C1        如实「暂未开通」；无编造 offer；主链非 500
  phase c（ENABLED=true, MODE=live）：
    K-E2E-C3        live 未实现 = DISABLED 如实拒绝（BLOCKED 路径证据；
                    绝不回退 fake 冒充，§29）

用法：
    python scripts/e2e_travel_commerce.py --password <pwd> --phase a|b|c

Redis 纪律：只读 SCAN/GET；测试产物（缓存/配额键）由外层清理。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # 仓库根 → backend 包

from e2e_travel_runtime import chat_stream, jwt_claims, login  # noqa: E402


def _redis_scan(pattern: str, count: int = 200) -> list[str]:
    """只读 SCAN（禁止任何写/删）。"""
    import redis as redis_lib

    from backend.config.redis import REDIS_URL

    base = REDIS_URL.rsplit("/", 1)[0]
    if "@localhost:" in base:
        base = base.replace("@localhost:", "@127.0.0.1:")
    client = redis_lib.Redis.from_url(base + "/0", decode_responses=True,
                                      socket_timeout=3,
                                      socket_connect_timeout=3)
    out: list[str] = []
    for key in client.scan_iter(match=pattern, count=count):
        out.append(key)
        if len(out) >= count:
            break
    return sorted(out)


def _quota_counter(provider: str = "fake:commerce") -> int:
    """今日该 provider 配额计数（无键=0）。"""
    today = _dt.date.today().isoformat()
    keys = _redis_scan(f"*quota:{provider}:{today}")
    if not keys:
        return 0
    import redis as redis_lib

    from backend.config.redis import REDIS_URL

    base = REDIS_URL.rsplit("/", 1)[0]
    if "@localhost:" in base:
        base = base.replace("@localhost:", "@127.0.0.1:")
    client = redis_lib.Redis.from_url(base + "/0", decode_responses=True,
                                      socket_timeout=3,
                                      socket_connect_timeout=3)
    total = 0
    for k in keys:
        raw = client.get(k)
        total += int(raw) if raw else 0
    return total


def _chat_ok(token, conv, q, must, must_not=None, timeout=180) -> list[str]:
    result = chat_stream(token, conv, q, timeout=timeout)
    failures: list[str] = []
    if result.get("status") != 200:
        return [f"SSE status={result.get('status')} "
                f"{str(result.get('error_body', ''))[:120]}"]
    answer = result.get("answer") or ""
    print(f"    [answer] {answer[:160].replace(chr(10), ' | ')}")
    for needle in must:
        if needle not in answer:
            failures.append(f"答案缺「{needle}」")
    for needle in (must_not or []):
        if needle in answer:
            failures.append(f"答案不应含「{needle}」")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--password", required=True)
    parser.add_argument("--username", default="e2e_travel")
    parser.add_argument("--phase", choices=["a", "b", "c"], default="a")
    args = parser.parse_args()

    token = login(args.username, args.password)
    claims = jwt_claims(token)
    print(f"[auth] ok user={claims.get('sub') or claims.get('username')} "
          f"tenant={claims.get('tenant_id')}")
    stamp = int(time.time())
    failures: list[str] = []
    code = 0

    if args.phase == "a":
        # ── K-E2E-C2 hotel：完整链路 offer + 语义 + replay 零增量 ──
        conv = f"stopK-hotel-{stamp}"
        print(f"===== [K-E2E-C2-hotel] conv={conv}")
        failures += _chat_ok(
            token, conv, "帮我找大阪10月3日到5日的酒店",
            must=["酒店搜索结果", "大阪", "价格观测时间", "JPY",
                  "前往供应商查看实时价格", "测试数据"],
            must_not=["已预订", "保证有房", "最终成交价"])
        if failures:
            code = 1
        q1 = _quota_counter()
        cache_keys = _redis_scan("*cache:travel_provider:*hotel_search*")
        print(f"    [redis] hotel_search缓存={len(cache_keys)} "
              f"配额计数={q1}")

        # replay：同问题再问一遍 → 缓存吸收 → 配额计数零增量（G17）
        print("===== [K-E2E-C2-replay] 同请求重放（缓存吸收）")
        failures += _chat_ok(
            token, conv, "帮我找大阪10月3日到5日的酒店",
            must=["酒店搜索结果", "缓存"])
        q2 = _quota_counter()
        print(f"    [quota] 计数 {q1}→{q2}")
        if q2 != q1:
            failures.append(f"replay 配额增量 {q1}->{q2}（应零增量）")
            code = 1
        if not cache_keys:
            failures.append("hotel_search 缓存键缺失")
            code = 1

        # ── K-E2E-C2 flight ──
        conv_f = f"stopK-flight-{stamp}"
        print(f"===== [K-E2E-C2-flight] conv={conv_f}")
        failures += _chat_ok(
            token, conv_f, "帮我查10月3日东京到大阪的机票",
            must=["航班搜索结果", "FakeAir", "前往供应商查看实时价格"],
            must_not=["保证有票", "已锁价"])
        if failures:
            code = 1

        # ── G28：旅游规划主链不受影响 ──
        conv_t = f"stopK-travel-{stamp}"
        print(f"===== [K-E2E-G28] conv={conv_t} 行程主链回归")
        failures += _chat_ok(
            token, conv_t, "帮我规划10月3日厦门2天的行程",
            must=["厦门", "行程"], must_not=["暂未开通"])
        if failures:
            code = 1

    elif args.phase == "b":
        # ── K-E2E-C1：mode=off 如实「暂未开通」，主链非 500 ──
        conv = f"stopK-off-{stamp}"
        print(f"===== [K-E2E-C1] conv={conv} mode=off")
        failures += _chat_ok(
            token, conv, "帮我找大阪10月3日到5日的酒店",
            must=["暂未开通"],
            must_not=["梅田广场酒店", "已预订", "¥", "$"])
        if failures:
            code = 1

    elif args.phase == "c":
        # ── K-E2E-C3：mode=live（无适配器）= DISABLED 如实拒绝 ──
        conv = f"stopK-live-{stamp}"
        print(f"===== [K-E2E-C3] conv={conv} mode=live（未实现）")
        failures += _chat_ok(
            token, conv, "帮我找大阪10月3日到5日的酒店",
            must=["暂未开通", "未接入"],
            must_not=["梅田广场酒店", "已预订", "测试数据"])
        if failures:
            code = 1

    if failures:
        print("\n===== FAILURES =====")
        for f in failures:
            print(f"  - {f}")
    else:
        print(f"\n===== PHASE {args.phase.upper()} ALL PASS =====")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
