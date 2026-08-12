from backend.rag.preprocessing.token_counter import count_tokens


def test_empty_text_zero():
    assert count_tokens("") == 0


def test_cjk_counts_roughly_one_per_char():
    n = count_tokens("客服需要审核退货原因和凭证真实性")
    assert 8 <= n <= 20   # 中文每字约 1 token，13 字约 13 token


def test_english_counts_less_than_chars():
    text = "This is a sentence with several words."
    assert 0 < count_tokens(text) < len(text)
