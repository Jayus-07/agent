# -*- coding: utf-8 -*-
"""backend/scripts/cleanup_tool_stats_keys.py — Redis Tool 统计日键脏成员清理

背景（2026-10-01 /tools 页行全 0 根因修复的配套运维步骤）：
1. 治理埋点曾把 capability 名当指标键，评测探针流量（probe.{uuid}）与
   测试残留（test.*）在 Redis 日键里留下一批 lock 永远匹配不上的成员；
2. 埋点侧已按前缀隔离（core/tool_runtime/metrics.py
   _NON_PRODUCT_KEY_PREFIXES），本脚本清历史存量，防口径继续失真。

用法（幂等，可重复执行）::
    python -m backend.scripts.cleanup_tool_stats_keys            # dry-run 只报告
    python -m backend.scripts.cleanup_tool_stats_keys --apply    # 实际 HDEL

只动 agent:tool_stats:* 日键内匹配 probe.*/test.* 前缀的 field，
不碰任何业务键。
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    apply = "--apply" in (argv or sys.argv[1:])
    from backend.config.redis import REDIS_KEY_PREFIX
    from backend.infra.redis.client import get_redis

    r = get_redis()
    if r is None:
        print("[cleanup] Redis 不可用，无事可做")
        return 0

    pattern = f"{REDIS_KEY_PREFIX or 'agent:'}tool_stats:*"
    removed_total = 0
    for key in r.scan_iter(match=pattern, count=100):
        names = [f.decode() if isinstance(f, bytes) else f for f in r.hkeys(key)]
        dirty = [f for f in names if f.startswith(("probe.", "test."))]
        if not dirty:
            continue
        print(f"[cleanup] {key.decode() if isinstance(key, bytes) else key}: {len(dirty)} 个脏成员")
        for n in sorted(dirty):
            print(f"    - {n}")
        if apply:
            r.hdel(key, *dirty)
        removed_total += len(dirty)

    mode = "已删除" if apply else "（dry-run，加 --apply 实际删除）"
    print(f"[cleanup] 共 {removed_total} 个脏成员 {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
