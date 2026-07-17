"""全局 pytest fixture — 让测试 import 后端模块无需 sys.path 折腾。

tracer.py 这类纯逻辑模块不需要 DB/Redis mock；将来 P0.2/P0.3 的测试
如果需要 PostgreSQL/Chroma，请在这里加 session 级 fixture。
"""
import sys
from pathlib import Path

# 把项目根加入 sys.path，使 `from backend.rag.tracer import ...` 可用
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))