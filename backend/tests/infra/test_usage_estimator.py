"""usage_estimator 单测——估算兜底的字符统计口径（2026-10-02）。

估算器是零依赖纯函数：中文 ≈0.6 token/字、其他 ≈0.25 token/字符
（DeepSeek 官方近似换算率）。强断言具体数值，防止换算率被无意改动。
"""
from __future__ import annotations

import math

from backend.infra.llm.usage_estimator import (
    StreamTextMeter,
    estimate_prompt_tokens,
    estimate_text_tokens,
)


class _Msg:
    """最小 BaseMessage 形状（只带 content），避免测试依赖 langchain。"""

    def __init__(self, content):
        self.content = content


def test_estimate_text_tokens_cjk_and_ascii():
    # 10 个汉字 × 0.6 = 6
    assert estimate_text_tokens("你好呀世界测验汉字" [:10]) == 6
    # 8 个 ASCII × 0.25 = 2
    assert estimate_text_tokens("abcdefgh") == 2
    # 混合：4 汉字(2.4) + 4 ASCII(1.0) = 3.4 → ceil 4
    assert estimate_text_tokens("你好ab世界cd") == 4


def test_estimate_text_tokens_empty_and_min_one():
    assert estimate_text_tokens("") == 0
    # 非空最少 1 token（单个标点也计费）
    assert estimate_text_tokens("a") == 1
    assert estimate_text_tokens("。") == 1


def test_full_width_punctuation_counts_as_cjk():
    # 全角逗号属于 CJK 区段，按 0.6 计
    assert estimate_text_tokens("，，，，，") == 3  # 5 × 0.6


def test_meter_incremental_matches_batch():
    text = "Hello 世界，this is 一段 mixed 文本 with tokens 12345"
    meter = StreamTextMeter()
    # 分片喂入（流式增量），结果必须与整段一次估算一致
    step = 7
    for i in range(0, len(text), step):
        meter.add(text[i:i + step])
    assert meter.completion_tokens == estimate_text_tokens(text)
    assert meter.chars == len(text)


def test_meter_empty_stream_is_zero():
    meter = StreamTextMeter()
    meter.add("")
    assert meter.chars == 0 and meter.completion_tokens == 0


def test_estimate_prompt_tokens_shapes():
    # str
    assert estimate_prompt_tokens("abcdefgh") == 2
    # BaseMessage 形状：6 汉字 × 0.6 = 3.6 → ceil 4
    assert estimate_prompt_tokens(_Msg("你好世界测试")) == 4
    # list of messages
    total = estimate_prompt_tokens([_Msg("abcd"), _Msg("efgh")])
    assert total == 2
    # {"role","content"} dict
    assert estimate_prompt_tokens({"role": "user", "content": "abcd"}) == 1
    # {"messages": [...]}（prompt template 渲染产物）
    assert estimate_prompt_tokens(
        {"messages": [_Msg("abcd"), "efgh"]}
    ) == 2
    # 多模态 content list
    assert estimate_prompt_tokens(
        _Msg([{"type": "text", "text": "abcdefgh"}])
    ) == 2


def test_estimate_prompt_tokens_unknown_shapes_are_zero():
    # 识别不了的形态按 0（宁少勿错，缺失由 estimated 标记暴露）
    assert estimate_prompt_tokens(None) == 0
    assert estimate_prompt_tokens(12345) == 0
    assert estimate_prompt_tokens({"foo": "bar"}) == 0
    assert estimate_prompt_tokens(_Msg(None)) == 0


def test_cjk_ratio_never_undercounts_chinese_sentence():
    """中文句子估算不得低于字符数的一半（换算率回归护栏）。"""
    sentence = "预算治理的待对账队列在企业实践里应当按聚合比率监控"
    tokens = estimate_text_tokens(sentence)
    assert tokens == math.ceil(len(sentence) * 0.6)
