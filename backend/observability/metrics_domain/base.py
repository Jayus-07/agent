"""基础运行时与多进程目录初始化。"""
from __future__ import annotations

import os as _os

# multiprocess 目录必须在任何指标定义前存在
_mp_dir = _os.getenv("PROMETHEUS_MULTIPROC_DIR", "")
if _mp_dir:
    try:
        _os.makedirs(_mp_dir, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
