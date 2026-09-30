"""tests/rag/test_chunk_type_contract.py — 表格 chunk 类型判定收口（P3-1）

病史：判定「这条 chunk 是不是表格」有三套口径 —— 生产点（chunking）写字符串
字面量、reporting_period 回填用元组白名单、`filter.py` 内联
``startswith("table_")``。任一处改名，其余静默失配；filter 失配的后果是
**财务表格的 PII 不再强制脱敏**（隐私方向必须 fail-closed），且没有任何报错。

现在：类型值只定义在 chunking 一处，判定一律走 ``is_table_chunk()``。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.rag.preprocessing.chunking import (
    CHUNK_TYPE_TABLE_FALLBACK,
    CHUNK_TYPE_TABLE_ROW,
    CHUNK_TYPE_TABLE_SUMMARY,
    TABLE_CHUNK_TYPES,
    is_table_chunk,
)

_PREPROCESSING = Path(__file__).resolve().parents[2] / "rag" / "preprocessing"
_CHUNKING_PY = _PREPROCESSING / "chunking.py"
_FILTER_PY = _PREPROCESSING / "filter.py"


class TestTypeTokens:
    def test_tokens_are_the_known_three(self):
        """锁字面量：当前表格 chunk 类型就是这三种（改名会让本用例变红）。"""
        assert CHUNK_TYPE_TABLE_ROW == "table_row"
        assert CHUNK_TYPE_TABLE_SUMMARY == "table_summary"
        assert CHUNK_TYPE_TABLE_FALLBACK == "table_fallback"
        assert TABLE_CHUNK_TYPES == ("table_row", "table_summary", "table_fallback")

    @pytest.mark.parametrize("ct", ["table_row", "table_summary", "table_fallback"])
    def test_known_types_are_table(self, ct):
        assert is_table_chunk(ct) is True

    def test_future_table_type_is_accepted_fail_closed(self):
        """将来新增表格类型自动被认作表格 —— 漏判会让财务表格 PII 漏脱敏。"""
        assert is_table_chunk("table_header") is True

    @pytest.mark.parametrize("ct", ["sql_result", "faq_match", "paragraph", "", None])
    def test_non_table_types(self, ct):
        assert is_table_chunk(ct) is False


class TestProducersUseTheTokens:
    def test_chunking_defines_each_token_exactly_once(self):
        """生产端只允许「常量定义」这一处出现类型名。

        用 AST 取字符串常量而不是扫文本：注释/文档字符串里提到旧写法
        （说明病史）是允许的 —— 扫文本的守护会被自己写的注释咬红。
        每个值恰好出现一次 = 既无调用点字面量，也无重复定义。
        """
        tree = ast.parse(_CHUNKING_PY.read_text(encoding="utf-8"))
        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for value in TABLE_CHUNK_TYPES:
            assert literals.count(value) == 1, (
                f"{value!r} 在 chunking.py 里出现 {literals.count(value)} 次"
                "（应只在常量定义处出现一次）"
            )

    def test_filter_has_no_inline_prefix_check(self):
        """防回退：filter 不得再内联 startswith("table_")。

        同为 AST 判定（只看真实调用），注释里提到旧写法不算。
        """
        tree = ast.parse(_FILTER_PY.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "startswith"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)):
                continue
            prefix = node.args[0].value
            if isinstance(prefix, str) and prefix.startswith("table_"):
                offenders.append(node.lineno)
        assert not offenders, f"filter.py 仍内联表格前缀判定（行 {offenders}）"
        assert "is_table_chunk" in _FILTER_PY.read_text(encoding="utf-8")


class TestFinancialPiiMasking:
    """端到端：财务文档的表格 chunk 必须强制脱敏 PII（本病的用户可见后果）。"""

    _TEXT = ("深圳某某科技有限公司 2026 年第二季度利润表第 12 行："
             "联系人邮箱 finance.lead@example.com，直线电话 13800138000，"
             "本期营业收入 486.5 万元，较上期增长 12.3%。")

    def test_table_chunk_of_financial_doc_masks_pii(self):
        from backend.rag.preprocessing.filter import ChunkFilter

        f = ChunkFilter()
        meta = {"doc_type": "financial", "chunk_type": "table_row"}
        keep, reason = f.should_keep(self._TEXT, meta)

        assert keep is True, f"财务表格 chunk 不该被过滤掉（reason={reason}）"
        assert meta.get("pii_masked"), "财务表格 chunk 的 PII 未被脱敏"

    def test_plain_table_chunk_of_non_financial_doc_also_masks(self):
        """非财务文档的表格 chunk 同样按「表格」处理（前缀判定的原意）。"""
        from backend.rag.preprocessing.filter import ChunkFilter

        f = ChunkFilter()
        meta = {"doc_type": "product_spec", "chunk_type": "table_fallback"}
        f.should_keep(self._TEXT, meta)
        assert meta.get("pii_masked"), "表格 chunk 的 PII 未被脱敏"

    def test_unknown_chunk_type_does_not_force_mask(self):
        """非表格、非财务 → 不因「表格」而强制脱敏（判据不能放宽成恒真）。"""
        from backend.rag.preprocessing.filter import ChunkFilter
        import backend.config as config

        if config.FILTER_ENABLE_PII_MASK:
            pytest.skip("全局 PII 开关打开时所有 chunk 都会脱敏，本条不适用")
        f = ChunkFilter()
        meta = {"doc_type": "policy", "chunk_type": "paragraph"}
        f.should_keep(self._TEXT, meta)
        assert not meta.get("pii_masked")


def test_preprocessing_modules_parse():
    """前置自检：本文件引用的两个模块语法可解析（防手工编辑破坏）。"""
    for path in (_CHUNKING_PY, _FILTER_PY):
        ast.parse(path.read_text(encoding="utf-8"))
