"""评测方式映射的回归测试。"""

import pytest

from backend.evaluation.mode import resolve_evaluation_mode


def test_evaluation_modes_have_unambiguous_runtime_flags():
    assert resolve_evaluation_mode("offline").__dict__ == {
        "mode": "offline", "live": False, "ragas": False, "no_ragas": True,
    }
    assert resolve_evaluation_mode("semantic").__dict__ == {
        "mode": "semantic", "live": True, "ragas": False, "no_ragas": True,
    }
    assert resolve_evaluation_mode("ragas").__dict__ == {
        "mode": "ragas", "live": True, "ragas": True, "no_ragas": False,
    }
    assert resolve_evaluation_mode("self+ragas").__dict__ == {
        "mode": "self+ragas", "live": True, "ragas": False, "no_ragas": False,
    }


def test_empty_mode_uses_double_track_default():
    assert resolve_evaluation_mode(None).mode == "self+ragas"


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="不支持的评测方式"):
        resolve_evaluation_mode("made-up")
