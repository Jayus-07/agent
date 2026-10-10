"""跨端节点标识一致性守护（结构病 P1-2 / P1-3）。

前端在少数几处**必须**写死后端节点标识（用于识别 SSE 事件的 `node` 字段）。
这些字面量的真源在后端；后端一改名而前端没跟上，功能就**静默失效**
（不抛异常、日志正常，只是永远匹配不上）。本文件把这条变成可执行的断言：
**前端写下的每个节点名，都必须真实存在于后端节点集。**

实测事故：
  - `SqlViz.tsx` 过滤 `sql_worker`（真实为 `sql_skill`）→ SQL 结果 UI 从未渲染；
  - `cs/constants.ts` 用工号前的旧架构名 `cs_knowledge` 系（真实为 `cs_graph_node` 系）
    → 客服状态栏显示「正在 cs_graph_node...」、时间轴图标恒为 `•`。

口径：注释一律剥离后再扫（历史说明里会提到旧名，那不是「写死了旧名」）。
"""
from __future__ import annotations

import re
from pathlib import Path

import backend.domains  # noqa: F401  # import 即触发域图自注册
from backend.orchestration.graph.builder import _NODE_LABELS

_REPO = Path(__file__).resolve().parents[2]  # backend/tests/x.py → 仓库根

# 需校验的前端文件
_SQLVIZ_FILES = (
    "frontend/src/components/chat/SqlViz.tsx",
    "frontend-cs/src/components/chat/SqlViz.tsx",
)
_CS_ID_FILES = (
    "frontend/src/components/cs/constants.ts",
    "frontend/src/store/csChat.ts",
)


def _real_node_ids() -> set[str]:
    """后端真实节点集：主图内置 + 已注册 Skill + 域图节点 + CS 子图节点常量。"""
    from backend.customer_service import graph_state as gs
    from backend.orchestration.capability_registry import tool_registry
    from backend.orchestration.domain_registry import domain_graph_registry

    ids: set[str] = set(_NODE_LABELS) | set(tool_registry.get_skill_nodes())
    ids |= {g.node_name for g in domain_graph_registry.get_all().values()}
    for attr in dir(gs):
        if attr.startswith("CS_"):
            value = getattr(gs, attr)
            if isinstance(value, str) and value.startswith("cs_"):
                ids.add(value)
    return ids


def _strip_comments(src: str) -> str:
    """剥离 JS/TS 注释（含块注释与行注释）——只校验真实代码里的字面量。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line.split("//", 1)[0] for line in src.splitlines())


def _read(rel: str) -> str:
    return _strip_comments((_REPO / rel).read_text(encoding="utf-8"))


# ── P1-2：SqlViz 过滤的节点名 ──────────────────────────────

def test_sqlviz_node_filter_targets_real_node():
    real = _real_node_ids()
    pattern = re.compile(r"e\.data\.node\s*===\s*'([^']+)'")
    for rel in _SQLVIZ_FILES:
        found = pattern.findall(_read(rel))
        assert found, f"{rel}: 未找到节点过滤字面量（正则失效？）"
        for node in found:
            assert node in real, (
                f"{rel} 过滤的节点名在后端不存在: {node!r}（后端真实节点集含 sql_skill 等）"
            )


# ── P1-3：CS 节点名（常量表键 + 意图映射键） ────────────────

def test_cs_frontend_node_ids_all_exist_in_backend():
    real = _real_node_ids()
    # 形如 `cs_xxx:` 的对象键，以及 `'cs_xxx'` 字符串字面量
    key_pat = re.compile(r"\b(cs_[a-z0-9_]+)\s*:")
    lit_pat = re.compile(r"'(cs_[a-z0-9_]+)'")
    for rel in _CS_ID_FILES:
        src = _read(rel)
        found = set(key_pat.findall(src)) | set(lit_pat.findall(src))
        found -= {"cs_"}  # startsWith('cs_') 的裸前缀不算
        assert found, f"{rel}: 未提取到任何 CS 节点标识（正则失效？）"
        for node in sorted(found):
            assert node in real, (
                f"{rel} 使用的 CS 节点名在后端不存在: {node!r}"
                f"（后端真源见 customer_service/graph_state.py 与 register.py）"
            )


def test_cs_frontend_no_stale_architecture_names():
    """防回退：前端代码不得再出现旧架构节点名（修复前实测全量存在）。

    用词边界匹配**完整名**——既覆盖键位置（`cs_knowledge:`）也覆盖字符串位置
    （`'cs_knowledge'`），同时不误伤 `cs_knowledge_expert` 这类真实节点
    （其后继 `_` 是单词字符，不构成边界）。
    """
    stale = (
        "cs_knowledge",
        "cs_business_query",
        "cs_business_action",
        "cs_complaint",
        "cs_handoff",
        "cs_pending",
        "cs_handoff_intercept",
    )
    for rel in _CS_ID_FILES:
        src = _read(rel)
        for name in stale:
            assert not re.search(rf"\b{name}\b", src), (
                f"{rel} 回退到旧架构节点名: {name}"
            )


def test_sqlviz_no_stale_worker_name():
    """防回退：SqlViz 不得再过滤 sql_worker。"""
    for rel in _SQLVIZ_FILES:
        assert "sql_worker" not in _read(rel), f"{rel} 回退到过期节点名 sql_worker"
