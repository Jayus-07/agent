"""Phase2-G —— worker metrics multiproc 时序红线回归锁。

实机发现（P1）：残留清理晚于指标构造时，已打开的 mmap 句柄在 unlink 后
仍写已删 inode，之后所有 inc 静默丢失（collector 聚合为空）。
修复（最终版）：**运行链路上不做任何自动清理**——残留文件跨重启保留
（Prometheus counter 语义天然兼容）。原因：不仅 worker_init 的清理晚于
构造会丢数据，fork 模型下连"最早 import 点"的清理也不安全——billiard
pool 子进程会 re-import 模块链，把子进程已打开的活跃文件 unlink
（/proc/<pid>/fd 呈 deleted，实机复现）。cleanup 仅保留为停机窗口的人工
运维入口。

prometheus_client 的 ValueClass 在**进程首次 import 时**按 env 一次性锁定
（values.py: get_value_class），pytest 进程通常已锁 MutexValue——因此本
文件用 subprocess（env 先于 import 设置）走与生产 worker 完全相同的
MultiProcessValue 路径验证。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_PROBE = r"""
import glob, os, sys
d = sys.argv[1]
os.environ["PROMETHEUS_MULTIPROC_DIR"] = d
mode = sys.argv[2]

from backend.observability.worker_metrics import cleanup_multiproc_dir_early

from prometheus_client import Counter, CollectorRegistry
from prometheus_client.multiprocess import MultiProcessCollector

if mode == "good":
    # 正确时序：清理 → 构造 → inc ⇒ 聚合可见
    cleanup_multiproc_dir_early()
    c = Counter("p2g_probe", "probe", ("k",))
    c.labels(k="a").inc(2.0)
    files = glob.glob(os.path.join(d, "counter_*"))
    r = CollectorRegistry()
    MultiProcessCollector(r, d)
    vals = [s.value for m in r.collect() for s in m.samples
            if s.name == "p2g_probe_total"]
    print("RESULT", bool(files), vals)
elif mode == "bad":
    # 错误时序（P1 特征）：构造+inc → 清理 unlink → inc ⇒ 全部不可见
    c = Counter("p2g_probe", "probe", ("k",))
    c.labels(k="a").inc(1.0)
    cleanup_multiproc_dir_early()
    c.labels(k="b").inc(2.0)
    r = CollectorRegistry()
    MultiProcessCollector(r, d)
    vals = [s.value for m in r.collect() for s in m.samples
            if s.name == "p2g_probe_total"]
    print("RESULT", vals)
"""


def _run(mode: str, tmpdir: str) -> str:
    env = dict(os.environ)
    env.pop("PROMETHEUS_MULTIPROC_DIR", None)  # 由探针脚本自己控制时序
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, tmpdir, mode],
        capture_output=True, text=True, env=env, timeout=120,
        # __file__=backend/tests/observability/x.py → 仓库根（backend 的父目录）
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))),
    )
    assert out.returncode == 0, out.stderr[-800:]
    for line in out.stdout.splitlines():
        if line.startswith("RESULT"):
            return line
    raise AssertionError(f"探针无输出: {out.stdout[-400:]}")


def test_good_order_cleanup_then_construct_keeps_inc_visible(tmp_path):
    """正确时序回归锁：early cleanup 后构造的指标 inc 必须落盘可见。"""
    d = str(tmp_path / "good")
    os.makedirs(d, exist_ok=True)
    line = _run("good", d)
    assert line == "RESULT True [2.0]", line


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows cannot unlink mmapped files; loss semantics hold only on Linux workers",
)
def test_bad_order_cleanup_after_construction_silently_drops(tmp_path):
    """P1 特征文档化（Linux 语义）：构造后清理 unlink ⇒ inc 100% 丢失。

    任何把清理挪回 worker_init（import 完成后触发）的改动都会让生产
    worker 的 terminal/enqueued/lease 等运行时指标回到本用例描述的
    丢失状态——本用例锁定的就是那条被禁止的路径的后果。
    """
    d = str(tmp_path / "bad")
    os.makedirs(d, exist_ok=True)
    line = _run("bad", d)
    assert line == "RESULT []", line


def test_no_automatic_cleanup_on_import_paths():
    """回归锁：运行链路上不得有任何自动清理调用。

    防两类回归：
    1. worker_init（start_worker_metrics）里重新出现清理调用；
    2. backend/tasks/__init__（fork 子进程会 re-import）里出现清理调用
       ——会把子进程活跃文件 unlink（实机 P1 根因）。
    """
    import backend.tasks  # noqa: F401 — 触发包 import
    import inspect

    from backend.observability import worker_metrics

    assert "_cleanup_multiproc_dir()" not in inspect.getsource(
        worker_metrics.start_worker_metrics)
    pkg_src = inspect.getsource(sys.modules["backend.tasks"])
    # 只禁"实际调用/import"，注释里的运维指引不算
    assert "from backend.observability.worker_metrics import" not in pkg_src
    assert not any(
        line.strip().startswith("cleanup_multiproc_dir_early")
        for line in pkg_src.splitlines())
    # 运维入口本身仍可用且幂等
    worker_metrics.cleanup_multiproc_dir_early()
    worker_metrics.cleanup_multiproc_dir_early()


import inspect  # noqa: E402  — 顶部 import 区之外的唯一例外放最后
