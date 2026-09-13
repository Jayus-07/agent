"""MetaStreamFilter 单测 — 流式 META 尾部拦截(聚合全量/外发剔除)。"""
from backend.rag.meta_stream_filter import MetaStreamFilter

META = '<!--META{"can_answer":true,"citations":["E1"]}-->'


def _feed_all(chunks) -> tuple[str, str]:
    """依次喂入 chunks,返回 (外发增量合计, 全量聚合)。"""
    f = MetaStreamFilter()
    out = "".join(f.feed(c) for c in chunks)
    out += f.flush()
    return out, f.full


def test_no_meta_passes_through():
    chunks = ["客服审核", "时间为 1-2 个工作日。[E1]"]
    out, full = _feed_all(chunks)
    assert out == "".join(chunks)
    assert full == "".join(chunks)


def test_meta_in_single_chunk_suppressed():
    chunks = ["答案正文。[E1]", META]
    out, full = _feed_all(chunks)
    assert out == "答案正文。[E1]"
    assert META in full  # 聚合完整,下游 _verify 仍可解析


def test_meta_split_across_chunks_suppressed():
    # META 起始符被切在 chunk 边界:<!--ME | TA{...}--> | 后续文本
    chunks = ["正文。", "<!--ME", 'TA{"can_answer":true}-->', "尾巴"]
    out, full = _feed_all(chunks)
    assert out == "正文。"
    assert full == "".join(chunks)


def test_text_after_meta_never_emitted():
    chunks = ["正文。", META, "\n\n更多机读内容"]
    out, full = _feed_all(chunks)
    assert out == "正文。"
    assert "更多机读内容" not in out
    assert "更多机读内容" in full


def test_marker_like_text_kept():
    # 正文里出现 "<!--MET" 这类前缀但始终不构成完整 <!--META → 全部放行
    chunks = ["前缀 <!--M", "ETX 文本"]  # 拼起来是 <!--METX,不是 META
    out, full = _feed_all(chunks)
    assert out == "前缀 <!--METX 文本"
    assert full == "前缀 <!--METX 文本"


def test_empty_input():
    out, full = _feed_all(["", ""])
    assert out == "" and full == ""
