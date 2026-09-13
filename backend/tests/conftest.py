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


# ── 数据目录隔离（治理 C，2026-09-13）────────────────────────────
# 在任何 backend 模块导入前，把 RAG_DATA_DIR 重定向到临时目录，并从 git
# 跟踪的 data/ 文件重建"仓库快照"。此前 golden 评测跑在真实工作区索引上，
# 结果反映的是"索引当时的快照"而非代码质量（干净 worktree 上 105/107
# 失败、本机 10/107 失败，同一份代码），不可复现。
#
# 必须在 backend.config 导入前执行：config/database.py 的 load_dotenv()
# 不覆盖已有环境变量，此处预设值会胜过 .env 的 RAG_DATA_DIR。
# 逃逸口：RAG_EVAL_NO_ISOLATION=1 时使用真实 data/（调试工作区索引用）。
def _prepare_isolated_data_dir() -> None:
    if os.getenv("RAG_EVAL_NO_ISOLATION") == "1":
        print("[conftest] 数据目录隔离已禁用（RAG_EVAL_NO_ISOLATION=1），使用真实 data/")
        return
    import shutil
    import subprocess
    import tempfile

    try:
        out = subprocess.run(
            ["git", "-C", str(_ROOT), "ls-files", "-z", "data/"],
            capture_output=True, check=True, timeout=30,
        ).stdout
        tracked = [p for p in out.decode("utf-8", "replace").split("\0") if p]
    except Exception as e:
        print(f"[conftest] git 不可用（{e}），放弃数据目录隔离，使用真实 data/")
        return

    tmp_root = tempfile.mkdtemp(prefix="agent_test_data_")
    copied = 0
    for rel in tracked:
        # git 路径带 data/ 前缀，而 tmp_root 本身就是 RAG_DATA_DIR（data 根）
        rel_in_data = Path(rel).parts[1:] if Path(rel).parts[0] == "data" else Path(rel).parts
        src = _ROOT / rel
        dst = Path(tmp_root).joinpath(*rel_in_data)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
        except OSError:
            pass  # 个别文件缺失/被锁，跳过（快照允许缺运行态文件）
    os.environ["RAG_DATA_DIR"] = tmp_root
    print(
        f"[conftest] 数据目录隔离: RAG_DATA_DIR={tmp_root} "
        f"({copied}/{len(tracked)} 个 git 跟踪文件已重建)"
    )


_prepare_isolated_data_dir()


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