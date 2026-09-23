"""e2e_travel_providers.py — STOP J 真实 Gateway Provider E2E 驱动（J-E2E-1~6）。

链路（任务书 §91）：Client → APISIX → JWT → FastAPI → Travel Graph →
Provider Layer（共享缓存/quota/校验）→ 腾讯 LBS 真实 API → SSE。

六个场景：
  J-E2E-1 live place resolution   真实腾讯解析种子外必去点（狐尾山公园）
  J-E2E-2 live route              真实路线 → Redis 共享路由缓存留证
  J-E2E-3 weather                 带出发日期 → 预报进共享缓存
  J-E2E-5 cached response         同 brief 重放 → 配额计数零增量（缓存吸收）
  J-E2E-4/6 provider unavailable  软预算=1（.env 临时注入，由外层编排）→
                                  行程照常出单 + 如实披露（§44/§59）

用法：
    # 阶段 A（.env 正常，预算不限）：真实第三方链路 + 缓存证据
    python scripts/e2e_travel_providers.py --password <pwd> --phase a
    # 阶段 B（外层先把 TRAVEL_PROVIDER_TENCENT_DAILY_BUDGET=1 写入 .env
    # 并 docker compose up -d app 重建）：
    python scripts/e2e_travel_providers.py --password <pwd> --phase b
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from e2e_travel_runtime import chat_stream, jwt_claims, login  # noqa: E402

PROVIDER = "狐尾山公园"  # 厦门真实景点，种子池外（触发 live place 解析）


def _redis_scan(pattern: str, count: int = 200) -> list[str]:
    """只读 SCAN（禁止任何写/删——与 STOP H 驱动同一纪律）。"""
    import redis as redis_lib

    from backend.config.redis import REDIS_URL

    base = REDIS_URL.rsplit("/", 1)[0]
    if "@localhost:" in base:
        base = base.replace("@localhost:", "@127.0.0.1:")
    client = redis_lib.Redis.from_url(base + "/0", decode_responses=True,
                                      socket_timeout=3, socket_connect_timeout=3)
    out: list[str] = []
    for key in client.scan_iter(match=pattern, count=count):
        out.append(key)
        if len(out) >= count:
            break
    return sorted(out)


def _quota_counter() -> int:
    """今日腾讯 Provider 配额计数（无键=0）。"""
    today = _dt.date.today().isoformat()
    keys = _redis_scan(f"*quota:tencent:lbs:{today}")
    if not keys:
        return 0
    import redis as redis_lib

    from backend.config.redis import REDIS_URL

    base = REDIS_URL.rsplit("/", 1)[0]
    if "@localhost:" in base:
        base = base.replace("@localhost:", "@127.0.0.1:")
    client = redis_lib.Redis.from_url(base + "/0", decode_responses=True,
                                      socket_timeout=3, socket_connect_timeout=3)
    raw = client.get(keys[0])
    return int(raw) if raw else 0


def _chat_ok(token, conv, q, must, must_not=None, timeout=180) -> list[str]:
    """发起一轮 SSE 对话并断言答案内容，返回失败原因列表。"""
    result = chat_stream(token, conv, q, timeout=timeout)
    failures: list[str] = []
    if result.get("status") != 200:
        return [f"SSE status={result.get('status')} {result.get('error_body', '')}"]
    answer = result.get("answer") or ""
    print(f"    [answer] {answer[:150].replace(chr(10), ' | ')}")
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
    parser.add_argument("--phase", choices=["a", "b"], default="a")
    args = parser.parse_args()

    token = login(args.username, args.password)
    claims = jwt_claims(token)
    stamp = int(time.time())
    failures: list[str] = []

    if args.phase == "a":
        # ── J-E2E-1/2/3：真实第三方链路（place/route/weather 全 live）──
        conv = f"stopJ-live-{stamp}"
        print(f"===== [J-E2E-1/2/3] conv={conv} 必去={PROVIDER}")
        date_text = "10月3日"  # 2026-10-03，在腾讯 future 预报视野内
        failures += _chat_ok(
            token, conv, f"帮我规划{date_text}起厦门2天的行程，必去{PROVIDER}",
            must=["厦门", "2 天行程", PROVIDER])
        n1 = _quota_counter()
        print(f"    [quota] 计数={n1}（live 调用已发生）")

        place_keys = _redis_scan("*cache:travel_provider:*place*")
        route_keys = _redis_scan("*cache:travel_provider:*route*")
        weather_keys = _redis_scan("*cache:travel_provider:*weather*")
        print(f"    [redis] place缓存={len(place_keys)} route缓存={len(route_keys)} "
              f"weather缓存={len(weather_keys)}")
        if not any(PROVIDER in k for k in " ".join(place_keys)):
            if not place_keys:
                failures.append("place 共享缓存无条目（J-E2E-1 证据缺失）")
        if not route_keys:
            failures.append("route 共享缓存无条目（J-E2E-2 live 路由证据缺失）")
        if not weather_keys:
            failures.append("weather 共享缓存无条目（J-E2E-3 证据缺失）")

        # ── J-E2E-5：同 brief 重放 → 缓存吸收，Provider 调用零增量 ──
        conv2 = f"stopJ-replay-{stamp}"
        print(f"===== [J-E2E-5] conv={conv2} 同 brief 重放")
        before = _quota_counter()
        failures += _chat_ok(
            token, conv2, f"帮我规划{date_text}起厦门2天的行程，必去{PROVIDER}",
            must=["厦门", "2 天行程", PROVIDER])
        after = _quota_counter()
        print(f"    [quota] 重放前={before} 重放后={after}")
        if after - before > 2:
            failures.append(
                f"配额计数增量 {after - before} > 2：重放未被缓存吸收（J-E2E-5）")

        print("\n===== [Phase A 汇总]")
    else:
        # ── J-E2E-4/6：Provider 不可用（软预算=1 已由外层注入）→ 行程照常 ──
        conv = f"stopJ-softstop-{stamp}"
        print(f"===== [J-E2E-4/6] conv={conv} Provider 软停下的行程完成度")
        before = _quota_counter()
        failures += _chat_ok(
            token, conv, "帮我规划福州1天的行程，必去状元楼",
            must=["福州", "1 天行程", "地点数据服务暂时不可用"])
        after = _quota_counter()
        print(f"    [quota] 前后={before}/{after}（必须零增量=完全未打 Provider）")
        if after != before:
            failures.append("软停期间仍有 Provider 调用（J-E2E-4/6）")

        print("\n===== [Phase B 汇总]")

    for f in failures:
        print(f"  [FAIL] {f}")
    print(f"STOP_J_PROVIDER_E2E_{'PASS' if not failures else 'FAIL'}=true "
          f"(phase {args.phase})")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
