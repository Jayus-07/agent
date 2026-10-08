"""C7/C8/C10 词表门禁回归（2026-10-03）。

三层断言：
  1. 三份黄金集基线达门槛（C7 ≥0.90 / C8 漏转=0 且误转<10% / C10 ≥0.95）；
  2. classify_with 与生产 detect_confirmation_intent 逐例 parity；
  3. 词表热更门 fail-closed：坏变更被拒、良性变更放行、未知键拒绝、
     门禁基建故障 fail-open；黄金集文件与 manifest sha256 一致（防手改）。
"""

import hashlib
import json
from pathlib import Path

import pytest

from backend.customer_service import vocab, vocab_gate
from backend.customer_service.confirmation import ConfirmationIntent, detect_confirmation_intent
from backend.customer_service.vocab_gate import VocabGateRejected

_GATE_DIR = Path(vocab_gate.__file__).resolve().parent.parent / "evaluation" / "datasets" / "cs" / "gate"


# ── 1. 基线门槛 ──────────────────────────────────────────────────────

class TestGoldenBaselines:
    def test_triage_accuracy_above_target(self):
        """C7：意图分诊黄金集准确率 ≥90%（演进分析目标值）。"""
        r = vocab_gate.evaluate_triage()
        assert r["total"] >= 300
        assert r["accuracy"] >= 0.90, f"基线 {r['accuracy']:.3f} 跌破目标，fails={r['fails'][:5]}"

    def test_handoff_no_missed_low_false(self):
        """C8：应转全转（漏转=0）、误转 <10%。"""
        r = vocab_gate.evaluate_handoff()
        assert r["total"] >= 50
        assert r["missed"] == 0, f"漏转 {r['missed_detail'][:3]}"
        assert r["false_rate"] < 0.10, f"误转率 {r['false_rate']:.3f}"

    def test_confirm_cancel_accuracy(self):
        """C10：确认/取消词表回放 ≥95%。"""
        r = vocab_gate.evaluate_confirm_cancel()
        assert r["accuracy"] >= 0.95, f"基线 {r['accuracy']:.3f} fails={r['fails'][:5]}"


# ── 2. parity（镜像函数与生产判定一致）───────────────────────────────

class TestClassifyParity:
    def test_mirror_matches_production_on_golden_cases(self):
        cases = json.loads((_GATE_DIR / "confirm_cancel_golden.json").read_text(encoding="utf-8"))["cases"]
        confirm_kws = vocab.get_confirm_keywords()
        cancel_kws = vocab.get_cancel_keywords()
        for c in cases:
            mirrored = vocab_gate.classify_with(c["text"], confirm_kws, cancel_kws)
            production = detect_confirmation_intent(c["text"]).name
            assert mirrored == production, f"{c['id']} {c['text']!r}: mirror={mirrored} prod={production}"

    def test_mirror_matches_production_on_live_vocab(self):
        samples = ["确认不要了", "这个可以取消吗", "好的没问题", "取消吗？", "不要", "ok 就这样"]
        for text in samples:
            assert vocab_gate.classify_with(
                text, vocab.get_confirm_keywords(), vocab.get_cancel_keywords()
            ) == detect_confirmation_intent(text).name


# ── 3. 热更门 fail-closed ────────────────────────────────────────────

class TestVocabChangeGate:
    def test_bad_change_rejected(self):
        """植入坏变更演练（C10 验收）：把「可以」加进取消词表 → 确认句翻转为 CANCEL → 拒。"""
        report = vocab_gate.evaluate_vocab_change({"cancel_keywords": ["可以"]})
        assert report.ok is False
        assert "回归" in report.reason or "准确率" in report.reason

    def test_benign_change_accepted(self):
        report = vocab_gate.evaluate_vocab_change({"confirm_keywords": ["中"]})
        assert report.ok is True, report.reason

    def test_unknown_key_rejected(self):
        report = vocab_gate.evaluate_vocab_change({"domain_keywords": ["foo"]})
        assert report.ok is False
        assert "未知" in report.reason

    def test_non_string_list_rejected(self):
        assert vocab_gate.evaluate_vocab_change({"confirm_keywords": [1, 2]}).ok is False


class TestWriteOverrideGate:
    @pytest.fixture(autouse=True)
    def _tmp_override_file(self, monkeypatch, tmp_path):
        self.override_file = tmp_path / "cs_vocab_override.json"
        monkeypatch.setattr(vocab, "_OVERRIDE_FILE", self.override_file)
        vocab._override_cache.clear()
        yield
        vocab._override_cache.clear()

    def test_rejected_change_not_written(self, monkeypatch):
        class FakeReport:
            ok = False
            reason = "候选词表引入回归（测试桩）"

        monkeypatch.setattr(vocab_gate, "evaluate_vocab_change", lambda update: FakeReport())
        with pytest.raises(VocabGateRejected):
            vocab.set_cancel_keywords(["不存在的词"], operator="tester")
        assert not self.override_file.exists(), "被拒变更不得落盘"

    def test_infra_failure_failopen_writes(self, monkeypatch):
        def boom(update):
            raise RuntimeError("数据集缺失（测试桩）")

        monkeypatch.setattr(vocab_gate, "evaluate_vocab_change", boom)
        vocab.set_cancel_keywords(["退了"], operator="tester")
        assert self.override_file.exists(), "门禁基建故障按 fail-open 放行"
        doc = json.loads(self.override_file.read_text(encoding="utf-8"))
        assert "退了" in doc["cancel_keywords"]
        assert doc["changelog"][-1]["gated"] is True

    def test_passed_change_written_with_audit(self):
        vocab.set_cancel_keywords(["不要了这个"], operator="tester")
        doc = json.loads(self.override_file.read_text(encoding="utf-8"))
        assert "不要了这个" in doc["cancel_keywords"]
        assert doc["changelog"][-1]["operator"] == "tester"


# ── 4. 黄金集完整性（防手改漂移）────────────────────────────────────

class TestGoldenIntegrity:
    def test_manifest_sha256_matches_files(self):
        manifest = json.loads((_GATE_DIR / "manifest.json").read_text(encoding="utf-8"))
        for name, expected in manifest["sha256"].items():
            # Git autocrlf and Windows text writes may change only line endings.
            # Hash canonical LF bytes so the manifest is platform-independent.
            content = (_GATE_DIR / name).read_bytes().replace(b"\r\n", b"\n")
            actual = hashlib.sha256(content).hexdigest()
            assert actual == expected, f"{name} 被手改未重新生成（跑 generate_golden.py）"

    def test_manifest_counts_match_files(self):
        manifest = json.loads((_GATE_DIR / "manifest.json").read_text(encoding="utf-8"))
        counts = {
            "intent_triage": len(vocab_gate._load("intent_triage_golden.json")),
            "handoff_timing": len(vocab_gate._load("handoff_timing_golden.json")),
            "confirm_cancel": len(vocab_gate._load("confirm_cancel_golden.json")),
        }
        assert manifest["counts"] == counts
