"""一次性脚本：orchestration.tool_registry → orchestration.capability_registry 全仓 import 替换。

排除：
- backend/orchestration/capability_registry.py（新本体，头部已手改）
- backend/orchestration/tool_registry.py（废弃 shim，自身要保留旧路径引用能力）
"""
import sys
from pathlib import Path

REPO = Path(r"D:\Program Files\workplace\agent")
ROOTS = ["backend", "scripts", "tests", "mcp_servers"]
EXCLUDE = {
    REPO / "backend/orchestration/capability_registry.py",
    REPO / "backend/orchestration/tool_registry.py",
}
PAIRS = [
    ("backend.orchestration.tool_registry", "backend.orchestration.capability_registry"),
    ("orchestration/tool_registry", "orchestration/capability_registry"),
]

changed = []
for root in ROOTS:
    for py in (REPO / root).rglob("*.py"):
        if py in EXCLUDE or "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        new = text
        for old, repl in PAIRS:
            new = new.replace(old, repl)
        if new != text:
            py.write_text(new, encoding="utf-8", newline="\n")
            changed.append(str(py.relative_to(REPO)))

print(f"changed {len(changed)} files:")
for c in sorted(changed):
    print("  ", c)
sys.exit(0)
