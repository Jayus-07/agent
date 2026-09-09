"""全局 pytest fixture — 让测试 import 后端模块无需 sys.path 折腾。

tracer.py 这类纯逻辑模块不需要 DB/Redis mock；将来 P0.2/P0.3 的测试
如果需要 PostgreSQL/Chroma，请在这里加 session 级 fixture。
"""
import os
import sys
from pathlib import Path

import pytest

# 把项目根加入 sys.path，使 `from backend.observability.tracer import ...` 可用
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Windows cp936 环境下强制 UTF-8 模式，防止含中文的源文件解析失败
os.environ["PYTHONUTF8"] = "1"
# 测试环境禁用 Langfuse 上报/读取：保证用例确定性（不依赖外部服务、不联网），
# tracer 自动降级回 SQLite 路径。必须在任何模块导入前生效。
os.environ["LANGFUSE_ENABLED"] = "false"
# 测试环境禁用 P0 结构化分析层双写，防止污染真实 data/analytics.db
os.environ["OBS_ANALYTICS_ENABLED"] = "false"


@pytest.fixture(autouse=True)
def _reset_langfuse_exporter(monkeypatch):
    """逐用例重置 exporter 单例，防止模块加载时缓存的 enabled 状态泄漏。"""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("OBS_ANALYTICS_ENABLED", "false")
    import backend.observability.langfuse_exporter as lf_mod
    import backend.observability.analytics_store as as_mod
    lf_mod._exporter = None
    as_mod._analytics_store = None
    yield
    lf_mod._exporter = None
    as_mod._analytics_store = None


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """每个测试前后复位全局熔断器 — 隔离进程级单例状态。

    背景：熔断器是模块级单例，故意触发熔断的测试（合法行为）会污染
    后续假设 CLOSED 的测试（全量运行时 flaky）。前后各复位一次：
    前面防被前序测试污染，后面把干净状态还给后续测试。
    """
    from backend.infra.circuit_breaker import get_all_breakers
    for breaker in get_all_breakers().values():
        breaker.reset()
    yield
    for breaker in get_all_breakers().values():
        breaker.reset()