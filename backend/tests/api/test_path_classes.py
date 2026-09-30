"""path_classes 单测 — 路径分类共享单一事实源（P0-1 抽取后行为零变化）。

覆盖：skip 判定（精确/前缀/非 skip）、优先级判定、以及 concurrency.py
对旧符号（_priority_of / 两个前缀清单）的 re-export 兼容性——防止后续
误删导致既有引用方（tests、潜在脚本）import 失败。
"""
from backend.app.api.middleware import concurrency, path_classes


def test_is_skip_path_exact_match():
    # 精确命中清单（清单内既有裸路径也有带子路径的前缀，两种都成立）
    assert path_classes.is_skip_path("/health") is True
    assert path_classes.is_skip_path("/metrics") is True


def test_is_skip_path_prefix_match():
    assert path_classes.is_skip_path("/rag/stats/detail") is True
    assert path_classes.is_skip_path("/chat/messages/abc") is True
    assert path_classes.is_skip_path("/evaluation/runs/123") is True


def test_is_skip_path_negative():
    # 重量端点不受 skip：对话主链路、上传、SQL
    assert path_classes.is_skip_path("/chat/stream") is False
    assert path_classes.is_skip_path("/rag/upload") is False
    assert path_classes.is_skip_path("/sql/query") is False
    # 前缀相似但不同路径不得误伤（/chat2 不是 /chat 子路径）
    assert path_classes.is_skip_path("/chat2") is False
    # /chat 本身不是 skip（对话是重量端点），与 /chat/messages 区分
    assert path_classes.is_skip_path("/chat") is False


def test_priority_of():
    assert path_classes.priority_of("/chat/stream") == "high"
    assert path_classes.priority_of("/chat") == "high"
    assert path_classes.priority_of("/cs/handoff") == "high"
    assert path_classes.priority_of("/rag/upload") == "normal"
    assert path_classes.priority_of("/data/export") == "normal"


def test_concurrency_reexports_legacy_symbols():
    """concurrency.py 必须继续暴露旧符号（既有 tests 引用 _priority_of）。"""
    assert concurrency._priority_of is path_classes.priority_of
    assert concurrency._HIGH_PRIORITY_PREFIXES is path_classes._HIGH_PRIORITY_PREFIXES
    assert concurrency._SKIP_PREFIXES is path_classes._SKIP_PREFIXES
    assert concurrency._priority_of("/chat/stream") == "high"


def test_skip_prefixes_all_start_with_slash():
    """清单卫生：前缀必须以 / 开头，否则 startswith 语义失真。"""
    for p in path_classes._SKIP_PREFIXES + path_classes._HIGH_PRIORITY_PREFIXES:
        assert p.startswith("/"), f"路径前缀缺少前导斜杠: {p}"
