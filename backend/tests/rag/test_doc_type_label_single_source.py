"""RAG 域「同一份事实写多遍」治理的守护（结构病审查 P2-4 / P2-5 / P2-6）。

病史（三件同源病，均为「表还在、不报错、只是悄悄用了错的/过期的那份」）：

- **P2-4** 中文季度数字映射 `{"一":"1",...}` 在入库侧
  (`preprocessing/financial_normalizer`) 与查询侧 (`retrieval/query_analyzer`)
  各写一份，逐字相同。→ 收口到 `preprocessing/cn_numerals.CN_QUARTER_DIGITS`。
- **P2-5** `preprocessing/domain_data.py` 里手写了一整套 doc_type / 文件名 / 目录 /
  业务域规则表（180 行），**随后被同文件下方的 taxonomy 兼容导出整体覆盖**——
  即那份手写表从来没有生效过，是纯死代码。→ 已删除（改前/改后运行期取值逐字相同）。
- **P2-6** doc_type 中文标签被手写两遍且互为反表：`rag/citation.py` 的
  `_TYPE_LABEL_MAP`（正向）与 `agents/reporter/context_filter.py` 的
  `type_label_map`（反向）。两侧都只覆盖 9 类，缺 7 类 →
  展示层悄悄回落英文原始码；正向表里还留着 yaml 中不存在的 `report`/`manual`。
  → 收口到 `taxonomy_spec.DOC_TYPE_LABELS`，反表改为对唯一事实源求逆。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.rag.preprocessing import domain_data
from backend.rag.preprocessing.cn_numerals import CN_QUARTER_DIGITS
from backend.rag.preprocessing.taxonomy_spec import (
    DOC_TYPE_LABELS,
    doc_type_label,
    get_taxonomy,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND = _REPO_ROOT / "backend"

_CITATION_PY = _BACKEND / "rag" / "citation.py"
_CONTEXT_FILTER_PY = _BACKEND / "agents" / "reporter" / "context_filter.py"
_DOMAIN_DATA_PY = _BACKEND / "rag" / "preprocessing" / "domain_data.py"
_FIN_NORMALIZER_PY = _BACKEND / "rag" / "preprocessing" / "financial_normalizer.py"
_QUERY_ANALYZER_PY = _BACKEND / "rag" / "retrieval" / "query_analyzer.py"


def _string_constants(path: Path) -> list[str]:
    """模块源码里的字符串常量（自动忽略注释与文档字符串）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


# ─────────────────────── P2-6 doc_type 中文标签 ───────────────────────

def test_doc_type_labels_cover_taxonomy_exactly():
    """标签表必须与 taxonomy 的 doc_type 集合**完全一致**（多一个少一个都不行）。

    少 → 展示层回落英文原始码；多 → 表里留着已删类型的过期文案。
    修复前实测红：旧表 9 项，缺 compliance/legal/security/financial/
    customer_data/contract_template/general，多 report/manual。
    """
    taxonomy = get_taxonomy()
    assert set(DOC_TYPE_LABELS) == set(taxonomy.doc_types)


def test_doc_type_labels_are_injective():
    """两个类型不能共用一个显示名——否则反向查表会静默丢一个。"""
    labels = list(DOC_TYPE_LABELS.values())
    dupes = {v for v in labels if labels.count(v) > 1}
    assert not dupes, f"DOC_TYPE_LABELS 里有重复显示名：{sorted(dupes)}"


def test_doc_type_label_prefix_falls_back_to_input():
    """未知类型原样返回（不臆造标签）。"""
    assert doc_type_label("listing") == DOC_TYPE_LABELS["listing"]
    assert doc_type_label("no_such_type") == "no_such_type"


def test_citation_has_no_private_label_map():
    """防回退：citation.py 不得再自持一份标签表。"""
    assert "_TYPE_LABEL_MAP" not in {
        n.id for n in ast.walk(ast.parse(_CITATION_PY.read_text(encoding="utf-8")))
        if isinstance(n, ast.Name)
    }, "citation.py 回退了私有 _TYPE_LABEL_MAP"
    assert "doc_type_label" in _CITATION_PY.read_text(encoding="utf-8")
    # 旧表里的过期文案不得出现在源码字符串里
    consts = set(_string_constants(_CITATION_PY))
    assert "操作手册" not in consts and "报告" not in consts


def test_context_filter_reverse_map_is_derived():
    """防回退：context_filter 的反查表必须是对唯一事实源求逆，不得手写。"""
    src = _CONTEXT_FILTER_PY.read_text(encoding="utf-8")
    assert "DOC_TYPE_LABELS" in src, "context_filter 没引用唯一事实源"
    assert "type_label_map = {" not in src, "context_filter 回退了手写反表"

    from backend.agents.reporter.context_filter import _TYPE_LABEL_TO_DOC_TYPE

    assert _TYPE_LABEL_TO_DOC_TYPE == {
        label: doc_type for doc_type, label in DOC_TYPE_LABELS.items()
    }


def test_context_filter_round_trip():
    """端到端：citation 渲染出的标签，context_filter 必须能反查回同一个 doc_type。

    修复前实测红：compliance/financial 等 7 类渲染成原始英文码后，
    反查表里没有 → 回落 general（引用来源的类型被静默改写）。
    """
    from backend.agents.reporter.context_filter import _TYPE_LABEL_TO_DOC_TYPE

    for doc_type in get_taxonomy().doc_types:
        label = doc_type_label(doc_type)
        assert _TYPE_LABEL_TO_DOC_TYPE[label] == doc_type, (
            f"{doc_type} 渲染成 {label!r} 后反查不回自己"
        )


# ─────────────────────── P2-5 domain_data 死代码 ───────────────────────

def test_domain_data_tables_are_taxonomy_derived():
    """四张表就是 taxonomy 导出的值（证明手写那份从未生效，删除是纯死代码）。"""
    taxonomy = get_taxonomy()
    assert domain_data.DOC_TYPE_RULES == taxonomy.legacy_doc_type_rules()
    assert domain_data.FILENAME_TYPE_HINTS == dict(taxonomy.filename_hints)
    assert domain_data.FOLDER_TYPE_HINTS == dict(taxonomy.folder_hints)
    assert domain_data.DOMAIN_RULES == {
        d: dict(rules) for d, rules in taxonomy.domain_rules.items() if d != "general"
    }


def test_domain_data_has_single_assignment_per_table():
    """防回退：每张表在源码里只允许被赋值一次（旧版有两份，后者覆盖前者）。"""
    tree = ast.parse(_DOMAIN_DATA_PY.read_text(encoding="utf-8"))
    names = ("DOC_TYPE_RULES", "FILENAME_TYPE_HINTS",
             "FOLDER_TYPE_HINTS", "DOMAIN_RULES")
    counts: dict[str, int] = {n: 0 for n in names}
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        for t in targets:
            if isinstance(t, ast.Name) and t.id in counts:
                counts[t.id] += 1
    multi = {k: v for k, v in counts.items() if v > 1}
    assert not multi, f"这些表在 domain_data.py 里被赋值多次（死代码回归）：{multi}"


# ─────────────────────── P2-4 中文季度数字 ───────────────────────

@pytest.mark.parametrize(
    "path",
    [_FIN_NORMALIZER_PY, _QUERY_ANALYZER_PY],
    ids=lambda p: p.name,
)
def test_cn_quarter_map_not_redeclared(path: Path):
    """两个消费方都不得再自持一份中文数字映射。"""
    src = path.read_text(encoding="utf-8")
    assert "CN_QUARTER_DIGITS" in src, f"{path.name} 没引用共享常量"
    assert '{"一": "1"' not in src, f"{path.name} 回退了手写的季度数字映射"


def test_cn_quarter_digits_content_locked():
    """字面量锁：季度数字映射就是这四个。"""
    assert CN_QUARTER_DIGITS == {"一": "1", "二": "2", "三": "3", "四": "4"}


def test_reporting_period_parsing_uses_shared_map_both_sides():
    """入库侧与查询侧都要能把中文季度解析成阿拉伯数字（共用同一份映射）。"""
    from backend.rag.preprocessing.financial_normalizer import extract_reporting_period

    period, year = extract_reporting_period("2025年第一季度利润表.xlsx")
    assert (period, year) == ("2025-Q1", "2025")

    from backend.rag.retrieval.query_analyzer import _extract_reporting_period_from_query

    assert _extract_reporting_period_from_query("2026年第三季度营收多少", []) == "2026-Q3"
