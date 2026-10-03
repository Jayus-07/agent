"""test_vocab.py — 词表单一事实源（迁移 B8）

锁定：①值零漂移（与搬移前逐字一致的核心断言）；②确认/取消词表热加载
（override 文件 mtime TTL）；③set_* 变更台账（修改有审计）。
"""
from __future__ import annotations

import json

import pytest

from backend.customer_service import vocab


class TestSingleSource:
    def test_version_present(self):
        # 2026-10-03：C7/C8/C10 门禁基线词表版本（变更须升版本，vocab_gate 守护）
        assert vocab.VOCAB_VERSION == "2026-10-03.1"

    def test_domain_rules_six_domains(self):
        assert set(vocab.CS_DOMAIN_KEYWORDS) == {
            "KNOWLEDGE", "TRANSACTION", "AFTER_SALES", "ACCOUNT",
            "COMPLAINT", "HUMAN",
        }
        assert set(vocab.CS_DOMAIN_PATTERNS) == set(vocab.CS_DOMAIN_KEYWORDS)
        # patterns 必须是已编译正则
        import re
        for pats in vocab.CS_DOMAIN_PATTERNS.values():
            assert all(isinstance(p, re.Pattern) for p in pats)

    def test_confirm_keywords_p1_fixed_authority(self):
        """P1 修正口径：无裸「不」「对」（防子串误伤），含「对的」。"""
        assert "对的" in vocab.CONFIRM_KEYWORDS
        assert "不" not in vocab.CONFIRM_KEYWORDS
        assert "对" not in vocab.CONFIRM_KEYWORDS
        assert "不要" in vocab.CANCEL_KEYWORDS
        assert "不" not in vocab.CANCEL_KEYWORDS

    def test_p0_markers_subset_of_angry(self):
        assert set(vocab.P0_ESCALATION_MARKERS) <= set(vocab.ANGRY_MARKERS)


class TestHotReload:
    @pytest.fixture()
    def override_file(self, tmp_path, monkeypatch):
        f = tmp_path / "cs_vocab_override.json"
        monkeypatch.setattr(vocab, "_OVERRIDE_FILE", f)
        vocab._override_cache.clear()
        yield f
        vocab._override_cache.clear()

    def test_base_without_override(self, override_file):
        assert vocab.get_cancel_keywords() == vocab.CANCEL_KEYWORDS

    def test_override_merges_and_hot_reloads(self, override_file):
        override_file.write_text(json.dumps(
            {"cancel_keywords": ["退了吧"]}, ensure_ascii=False),
            encoding="utf-8")
        merged = vocab.get_cancel_keywords()
        assert "退了吧" in merged
        assert "取消" in merged  # base 保留（追加式合并）

    def test_set_keywords_writes_changelog(self, override_file):
        vocab.set_cancel_keywords(["退了吧"], operator="tester")
        doc = json.loads(override_file.read_text(encoding="utf-8"))
        assert doc["cancel_keywords"] == ["退了吧"]
        assert doc["changelog"][-1]["operator"] == "tester"
        assert doc["changelog"][-1]["vocab_version"] == vocab.VOCAB_VERSION
        # set 后新词立即生效（缓存已失效）
        assert "退了吧" in vocab.get_cancel_keywords()

    def test_invalid_override_ignored(self, override_file):
        override_file.write_text("not json", encoding="utf-8")
        assert vocab.get_cancel_keywords() == vocab.CANCEL_KEYWORDS
