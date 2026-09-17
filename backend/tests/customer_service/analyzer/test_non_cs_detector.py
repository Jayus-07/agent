# -*- coding: utf-8 -*-
"""test_non_cs_detector.py — redirect_main 阶段二 LLM 语义仲裁（2026-09-18）。

覆盖：默认 OFF 零调用 / 高低置信 / 短句跳过 / 坏输出与异常软失败 /
```json 包裹解析 / confidence 限幅 / 阈值 env 覆盖 / TTL 缓存。
LLM 一律经 _get_llm() 间接层 monkeypatch，不触网。
"""
import time
from types import SimpleNamespace

import pytest

from backend.customer_service.analyzer import non_cs_detector as ncd


class _FakeLLM:
    """最小 LLM 替身：返回预置 content，记录调用次数。"""

    def __init__(self, content: str = "", sleep_s: float = 0.0, boom: bool = False):
        self.content = content
        self.sleep_s = sleep_s
        self.boom = boom
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.boom:
            raise RuntimeError("llm down")
        if self.sleep_s:
            time.sleep(self.sleep_s)
        return SimpleNamespace(content=self.content)


def _json_content(is_non_cs: bool = True, confidence: float = 0.9,
                  target_domain: str = "tech", reason: str = "技术问答") -> str:
    import json
    return json.dumps({
        "is_non_cs": is_non_cs,
        "confidence": confidence,
        "target_domain": target_domain,
        "reason": reason,
    }, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _env_off_and_cache_isolated(monkeypatch):
    """默认环境：开关关闭 + 缓存隔离（防跨测试污染）。"""
    monkeypatch.delenv(ncd.ENV_LLM_ENABLED, raising=False)
    monkeypatch.delenv(ncd.ENV_THRESHOLD, raising=False)
    monkeypatch.setattr(ncd, "_CACHE", {})
    yield


_LONG_QUERY = "帮我写一个快速排序的代码"  # 12 字符，超过 _MIN_QUERY_LEN


class TestDetectNonCs:
    def test_disabled_by_default_no_llm_call(self, monkeypatch):
        """默认 OFF：不调 LLM，直接 None。"""
        fake = _FakeLLM(_json_content())
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        assert ncd.detect_non_cs_cached(_LONG_QUERY) is None
        assert fake.calls == 0

    def test_enabled_high_confidence_detects(self, monkeypatch):
        """ON + LLM 高置信判非客服 → 返回检测结果。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        fake = _FakeLLM(_json_content(is_non_cs=True, confidence=0.9))
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        det = ncd.detect_non_cs_cached(_LONG_QUERY)
        assert det is not None
        assert det.is_non_cs is True
        assert det.target_domain == "tech"
        assert ncd.should_redirect(det) is True
        assert fake.calls == 1

    def test_low_confidence_no_redirect(self, monkeypatch):
        """ON + 置信度低于默认阈值 0.75 → 不转出。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        fake = _FakeLLM(_json_content(confidence=0.3))
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        det = ncd.detect_non_cs_cached(_LONG_QUERY)
        assert det is not None
        assert ncd.should_redirect(det) is False

    def test_cs_judgment_never_redirects(self, monkeypatch):
        """LLM 判为客服（is_non_cs=false）无论置信度多高都不转出。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        fake = _FakeLLM(_json_content(is_non_cs=False, confidence=0.99))
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        det = ncd.detect_non_cs_cached(_LONG_QUERY)
        assert det is not None
        assert ncd.should_redirect(det) is False

    def test_short_query_skips_llm(self, monkeypatch):
        """短句（<6 字符）不值得一次 LLM 调用：None 且零调用。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        fake = _FakeLLM(_json_content())
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        assert ncd.detect_non_cs_cached("你好") is None
        assert fake.calls == 0

    def test_bad_output_soft_fails(self, monkeypatch):
        """LLM 回复无 JSON → 软失败 None（留守 CS 是安全侧）。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        fake = _FakeLLM("我觉得这不是客服问题吧")
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        assert ncd.detect_non_cs_cached(_LONG_QUERY) is None

    def test_llm_exception_soft_fails(self, monkeypatch):
        """LLM 抛异常 → 软失败 None。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        fake = _FakeLLM(boom=True)
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        assert ncd.detect_non_cs_cached(_LONG_QUERY) is None

    def test_timeout_soft_fails(self, monkeypatch):
        """LLM 超时（线程级限时）→ 软失败 None。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        monkeypatch.setattr(ncd, "_TIMEOUT_S", 0.05)
        fake = _FakeLLM(_json_content(), sleep_s=0.3)
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        assert ncd.detect_non_cs_cached(_LONG_QUERY) is None


class TestParseResponse:
    def test_code_fence_wrapped_json_parsed(self):
        """容忍 ```json ... ``` 包裹。"""
        resp = SimpleNamespace(content=f"```json\n{_json_content()}\n```")
        det = ncd._parse_response(resp)
        assert det is not None
        assert det.is_non_cs is True

    def test_json_embedded_in_prose_parsed(self):
        """容忍 JSON 前后夹杂说明文字（取首尾花括号）。"""
        resp = SimpleNamespace(content=f'好的，结果如下：{_json_content()} 以上。')
        det = ncd._parse_response(resp)
        assert det is not None
        assert det.confidence == pytest.approx(0.9)

    def test_confidence_clamped(self):
        """confidence 越界（>1 / <0）限幅到 [0, 1]。"""
        resp = SimpleNamespace(content=_json_content(confidence=1.5))
        assert ncd._parse_response(resp).confidence == 1.0
        resp2 = SimpleNamespace(content=_json_content(confidence=-0.5))
        assert ncd._parse_response(resp2).confidence == 0.0

    def test_none_like_response_soft_fails(self):
        """空回复 / 无花括号 → None。"""
        assert ncd._parse_response(SimpleNamespace(content="")) is None
        assert ncd._parse_response(SimpleNamespace(content="没有花括号")) is None


class TestThreshold:
    def test_threshold_env_override(self, monkeypatch):
        """阈值可经 env 覆盖：0.95 时 0.9 不转出。"""
        monkeypatch.setenv(ncd.ENV_THRESHOLD, "0.95")
        det = ncd.NonCSDetection(is_non_cs=True, confidence=0.9)
        assert ncd.should_redirect(det) is False

    def test_threshold_invalid_env_falls_back(self, monkeypatch):
        """非法阈值 env 回落默认 0.75。"""
        monkeypatch.setenv(ncd.ENV_THRESHOLD, "not-a-number")
        assert ncd.should_redirect(
            ncd.NonCSDetection(is_non_cs=True, confidence=0.75)
        ) is True
        assert ncd.should_redirect(
            ncd.NonCSDetection(is_non_cs=True, confidence=0.74)
        ) is False


class TestCache:
    def test_same_query_hits_cache(self, monkeypatch):
        """同 query 第二次不调 LLM。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        fake = _FakeLLM(_json_content())
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        ncd.detect_non_cs_cached(_LONG_QUERY)
        ncd.detect_non_cs_cached(_LONG_QUERY)
        assert fake.calls == 1

    def test_different_query_no_cross_hit(self, monkeypatch):
        """不同 query 各自调 LLM。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        fake = _FakeLLM(_json_content())
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        ncd.detect_non_cs_cached("帮我写一个快速排序")
        ncd.detect_non_cs_cached("今天天气不错想出去玩")
        assert fake.calls == 2

    def test_failure_not_cached(self, monkeypatch):
        """软失败（None）不缓存：LLM 恢复后同 query 可重试。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        bad = _FakeLLM("不是 JSON")
        monkeypatch.setattr(ncd, "_get_llm", lambda: bad)
        assert ncd.detect_non_cs_cached(_LONG_QUERY) is None
        good = _FakeLLM(_json_content())
        monkeypatch.setattr(ncd, "_get_llm", lambda: good)
        det = ncd.detect_non_cs_cached(_LONG_QUERY)
        assert det is not None
        assert good.calls == 1

    def test_fifo_eviction_bounded(self, monkeypatch):
        """缓存超上限时淘汰最旧条目，不无界膨胀。"""
        monkeypatch.setenv(ncd.ENV_LLM_ENABLED, "true")
        fake = _FakeLLM(_json_content())
        monkeypatch.setattr(ncd, "_get_llm", lambda: fake)
        for i in range(ncd._CACHE_MAX + 2):
            ncd.detect_non_cs_cached(f"完全不同的查询内容{i}")
        assert len(ncd._CACHE) <= ncd._CACHE_MAX
