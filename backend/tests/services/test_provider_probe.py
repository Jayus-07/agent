"""services/provider_probe.py —— 分级探测契约（P1b / §B.14）

锁定四件事：

1. **私网放行只能来自显式 `network_scope`**，且云元数据地址即便放行也拦（B.6）。
   这是防 DNS rebinding 的关键，不能靠「解析出来是私网就自动放行」。
2. **探测链只剩 L0 + L2**（2026-09-21 起）：判定完全由 L2 给出，只有 L0 失败短路。
   原 L1（`GET /models`）已移出链路、改为按需的 `fetch_model_catalog()`；若哪天有人
   把它塞回探测链，本文件不会直接报错，但 `test_probe_chain_has_no_l1` 会红 ——
   那是刻意的：它不仅拖慢成功路径，还会让「拿不到清单」被误读成「供应商不可用」。
3. **探测不经 proxy**（B.4 硬约束 3）：用 AST 断言 import 列表，避免「探测烧预算
   / 污染 token 统计」这类只在运行时才暴露的问题。
4. **给用户看的 `summary` 不得出现机器原文**：英文异常类名 / JSON / `Error code:`
   一律只准出现在 `detail`。这组用例是文案纪律的门禁（用户明确要求过）。
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import provider_probe as P


def _run(coro):
    return asyncio.run(coro)


def _async_step(level: str, status: str, summary: str):
    async def _f(*_a, **_k) -> P.ProbeStep:
        return P.ProbeStep(level=level, status=status, summary=summary)
    return _f


class _HttpError(Exception):
    """带 `status_code` 的异常，模拟 openai SDK 的 APIStatusError。

    放在文件前部：下面有 `@pytest.mark.parametrize` 在**导入期**就要构造它。
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── L0：私网策略 ────────────────────────────────────────────────────────


def test_l0_blocks_private_when_scope_is_public():
    """默认 public → 私网照拦（自托管场景必须显式勾选才放行）。"""
    step = _run(P.probe_l0("http://127.0.0.1:8000/v1", allow_private=False))
    assert step.status == P.STATUS_FAIL
    assert "拦截" in step.summary


def test_l0_allows_private_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(P, "_tcp_tls_ok", lambda *_a, **_k: None)
    step = _run(P.probe_l0("http://127.0.0.1:8000/v1", allow_private=True))
    assert step.status == P.STATUS_PASS


def test_l0_metadata_address_blocked_even_when_private_allowed(monkeypatch):
    """云元数据地址是「取实例凭据」入口，放行私网也必须拦。"""
    monkeypatch.setattr(P, "_tcp_tls_ok", lambda *_a, **_k: None)
    step = _run(P.probe_l0("http://169.254.169.254/latest/meta-data",
                           allow_private=True))
    assert step.status == P.STATUS_FAIL
    assert "元数据" in step.summary


def test_l0_unreachable_is_fail_with_actionable_hint(monkeypatch):
    monkeypatch.setattr(P, "assert_url_allowed", lambda url, **_k: url)

    def _boom(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(P, "_tcp_tls_ok", _boom)
    step = _run(P.probe_l0("https://api.example.com/v1", allow_private=False))
    assert step.status == P.STATUS_FAIL
    assert "不可达" in step.summary


# ── 模型目录：按需资源（原 L1 的能力，已移出探测链）─────────────────────
#
# 这组用例守的是「探测链只剩 L0 + L2」之后的新契约：
#   1. 拿不到清单**永远不算探测失败** —— 它只影响「要不要手打模型名」。
#   2. 地址疑似填成厂商原生端点时，**清单照给但要点破地址**。
#   3. `summary` 只讲人话，英文异常/JSON 一律进 `detail`。


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, *_a, **_k):
        return self._response


_catalog_calls = {"n": 0}


def _patch_httpx(monkeypatch, response: _FakeResponse) -> None:
    import httpx

    def _factory(**_k):
        _catalog_calls["n"] += 1
        return _FakeClient(response)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@pytest.fixture(autouse=True)
def _clear_catalog_state(monkeypatch):
    """缓存与调用计数逐个用例清零 —— 否则本文件会互相污染（顺序敏感）。"""
    _catalog_calls["n"] = 0
    P.reset_catalog_cache_for_tests()
    yield
    P.reset_catalog_cache_for_tests()


def test_catalog_200_openai_shape_lists_models(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResponse(
        200, '{"object":"list","data":[{"id":"qwen3.7-plus"},{"id":"bge-m3"}]}'))
    cat = _run(P.fetch_model_catalog("https://api.example.com/v1", "sk-x"))

    assert cat.ok is True
    assert cat.shape_ok is True
    assert [i.id for i in cat.items] == ["qwen3.7-plus", "bge-m3"]
    assert cat.items[0].kind == "chat"
    assert cat.items[1].kind == "embedding"


def test_catalog_404_hints_missing_v1_and_is_not_a_probe_failure(monkeypatch):
    """404 只说明「这个站点没有清单接口」，不是「供应商不可用」。"""
    _patch_httpx(monkeypatch, _FakeResponse(404, "not found"))
    cat = _run(P.fetch_model_catalog("https://api.example.com", "sk-x"))

    assert cat.ok is False
    assert cat.status == P.STATUS_DEGRADED      # 不是 fail —— 手打模型名照常可用
    assert "/v1" in cat.summary
    assert cat.reason == P.REASON_BASE_URL


def test_catalog_401_points_at_key(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResponse(401, "unauthorized"))
    cat = _run(P.fetch_model_catalog("https://api.example.com/v1", "sk-bad"))

    assert cat.ok is False
    assert cat.reason == P.REASON_API_KEY
    assert "API Key" in cat.summary


# 百炼原生 `/api/v1/models` 的真实响应形状（实测 200，507 个模型）。
_NATIVE_ENVELOPE = (
    '{"code":null,"message":null,"success":true,'
    '"output":{"total":507,"page_no":1,"page_size":20,'
    '"models":[{"model":"qwen3.8-max","name":"Qwen3.8-Max"}]}}'
)


def test_catalog_native_envelope_still_lists_names_but_flags_base_url(monkeypatch):
    """地址填成「厂商原生协议端点」时：名字照给，但必须当场点破地址问题。

    实测：`{base}/models` 在原生前缀下**也**是 200（还能读出模型名），但 OpenAI 协议
    要打的 `/chat/completions` 在该前缀下不存在（404 且 body 为空）。若这里不点破，
    用户就会拿着正确的模型名、在错误的地址上反复 404。
    """
    _patch_httpx(monkeypatch, _FakeResponse(200, _NATIVE_ENVELOPE))
    cat = _run(P.fetch_model_catalog("https://maas.example.com/api/v1", "sk-x"))

    assert cat.shape_ok is False
    assert [i.id for i in cat.items] == ["qwen3.8-max"]   # 名字照样给
    assert cat.total == 507
    assert "/compatible-mode" in cat.summary


def test_catalog_opaque_body_does_not_blame_the_address(monkeypatch):
    """形状判断不了时保持宽容 —— 宁可漏报，不可误报「地址错了」。"""
    _patch_httpx(monkeypatch, _FakeResponse(200, "<html>not json</html>"))
    cat = _run(P.fetch_model_catalog("https://api.example.com/v1", "sk-x"))

    assert cat.shape_ok is None
    assert cat.ok is False
    assert cat.reason != P.REASON_BASE_URL


def test_catalog_truncates_beyond_limit(monkeypatch):
    huge = json.dumps({"data": [{"id": f"m{i}"} for i in range(P._CATALOG_LIMIT + 5)]})
    _patch_httpx(monkeypatch, _FakeResponse(200, huge))
    cat = _run(P.fetch_model_catalog("https://api.example.com/v1", "sk-x"))

    assert len(cat.items) == P._CATALOG_LIMIT
    assert cat.truncated is True


def test_catalog_second_call_hits_cache_and_skips_http(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResponse(200, '{"data":[{"id":"m"}]}'))

    first = _run(P.fetch_model_catalog("https://api.example.com/v1", "sk-x"))
    second = _run(P.fetch_model_catalog("https://api.example.com/v1", "sk-x"))

    assert first.cached is False
    assert second.cached is True
    assert _catalog_calls["n"] == 1        # 第二次没有再打上游
    assert [i.id for i in second.items] == ["m"]


def test_catalog_cache_is_keyed_by_api_key(monkeypatch):
    """换 Key 必须重新拉 —— 否则会把上一个 Key 的可见范围错给下一个。"""
    _patch_httpx(monkeypatch, _FakeResponse(200, '{"data":[{"id":"m"}]}'))

    _run(P.fetch_model_catalog("https://api.example.com/v1", "sk-a"))
    _run(P.fetch_model_catalog("https://api.example.com/v1", "sk-b"))

    assert _catalog_calls["n"] == 2


def test_catalog_rejects_private_host_without_explicit_scope(monkeypatch):
    """目录会带着用户的 Key 出站 —— 同样必须过 url_guard（唯一拦 SSRF 的地方）。"""
    _patch_httpx(monkeypatch, _FakeResponse(200, '{"data":[{"id":"m"}]}'))
    cat = _run(P.fetch_model_catalog("http://127.0.0.1:8000/v1", "sk-x"))

    assert cat.ok is False
    assert cat.reason == P.REASON_BLOCKED
    assert _catalog_calls["n"] == 0        # 被拦的地址根本不该出站


@pytest.mark.parametrize("model_id,kind", [
    ("qwen3.7-plus", "chat"),
    ("text-embedding-v4", "embedding"),
    ("BAAI/bge-reranker-v2-m3", "rerank"),   # rerank 规则必须排在 embedding 的 bge 之前
    ("bge-m3", "embedding"),
    ("qwen3-omni-flutter", "vision"),
    ("cosyvoice-v2", "speech"),
])
def test_catalog_model_kind_heuristics(model_id, kind):
    assert P._classify_model_kind(model_id) == kind


def test_catalog_kind_is_never_used_to_reject_a_name():
    """分错用途只是分组问题 —— 默认一律保守落回 chat，不参与任何判定。"""
    assert P._classify_model_kind("某个中文名模型") == "chat"
    assert P._classify_model_kind("") == "chat"


# ── 文案纪律：给用户看的 summary 不得出现看不懂的英文 ────────────────────

_FORBIDDEN_IN_SUMMARY = (
    "Error code:", "Traceback", "Exception", "openai", "langchain",
    "OpenAI", "HTTPError", "status_code", "{", "}",
)


def _assert_human_readable(summary: str) -> None:
    for token in _FORBIDDEN_IN_SUMMARY:
        assert token not in summary, f"summary 泄漏了机器原文：{token} in {summary!r}"
    # 中文结论：至少含一个汉字，避免退化成纯英文
    assert any("\u4e00" <= ch <= "\u9fff" for ch in summary), summary


@pytest.mark.parametrize("exc,api_key", [
    (_HttpError("Error code: 404", status_code=404), "sk-secret"),
    (_HttpError("Error code: 401 - invalid api key", status_code=401), "sk-secret"),
    (RuntimeError(
        "OpenAIModelNotFoundError: Error code: 404 - {'error': {'code': 'model_not_found'}}"), ""),
    (RuntimeError("connection reset by peer"), ""),
    (RuntimeError("APITimeoutError: Request timed out."), ""),
])
def test_l2_failure_summary_is_human_readable(exc, api_key):
    """用户明确要求：失败提示不能是看不懂的英文 —— 原文只准进 detail。"""
    failure = P._classify_l2_failure(exc, api_key=api_key)
    _assert_human_readable(failure.summary)
    assert exc.__class__.__name__ in failure.raw      # 原文没丢，只是换了字段


def test_step_summary_never_carries_exception_chatter(monkeypatch):
    """端到端再确认一次：探测步骤的 summary 干净、detail 才放原文。"""
    monkeypatch.setattr(P, "assert_url_allowed", lambda url, **_k: url)

    def _boom(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(P, "_tcp_tls_ok", _boom)
    step = _run(P.probe_l0("https://api.example.com/v1", allow_private=False))

    _assert_human_readable(step.summary)
    assert "OSError" in step.detail
    assert step.reason == P.REASON_UNREACHABLE


# ── L2：Key 错 vs 模型名错 ──────────────────────────────────────────────


def test_fast_l2_only_waits_for_first_stream_chunk(monkeypatch):
    """快速模式只证明请求已被模型接受，不应等待完整生成结束。"""
    class _FirstChunkStream:
        def __init__(self) -> None:
            self.calls = 0
            self.closed = False

        def __iter__(self):
            return self

        def __next__(self):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="首片")
            raise AssertionError("快速 L2 不应继续消费完整流")

        def close(self) -> None:
            self.closed = True

    stream = _FirstChunkStream()

    class _Client:
        def stream(self, *_args, **_kwargs):
            return stream

        def invoke(self, *_args, **_kwargs):
            raise AssertionError("快速 L2 不应调用非流式 invoke")

    monkeypatch.setattr(P, "build_probe_client", lambda *_a, **_k: _Client())

    step = _run(P.probe_l2(
        "openai", model_name="m", api_key="k", base_url="https://x/v1",
        fast=True,
    ))

    assert step.status == P.STATUS_PASS
    assert "首个流式分片" in step.summary
    assert stream.calls == 1
    assert stream.closed is True


@pytest.mark.parametrize("message,expected", [
    ("Error code: 401 - invalid api key provided", "Key"),
    ("NotFoundError: model not found: gpt-9", "模型名"),
    ("Model `foo` does not exist", "模型名"),
    ("connection reset by peer", "最小调用失败"),
    # langchain_openai 把任何 404 都包成这个类名；名字里的 CamelCase
    # "ModelNotFound" 无空格无下划线，早期提示词表匹配不到 → 退化成「最小调用失败」。
    ("OpenAIModelNotFoundError: Error code: 404 - {'error': {'code': 'model_not_found'}}",
     "模型名"),
])
def test_l2_failure_attribution(message, expected):
    failure = P._classify_l2_failure(RuntimeError(message))
    assert expected in failure.summary


def test_l2_404_with_empty_body_blames_base_url_not_model_name():
    """空 body 的 404 = 路由不存在 → 必须指向 base_url，不能把人骗去改模型名。

    实测真身：`OpenAIModelNotFoundError: Error code: 404`（body 为空）。上游在鉴权
    之前就 404，说明该前缀下根本没有 `/chat/completions`；此时改模型名永远无效。
    """
    exc = _HttpError("Error code: 404", status_code=404)
    failure = P._classify_l2_failure(exc, api_key="sk-secret")
    assert "/compatible-mode" in failure.summary
    assert "模型名" not in failure.summary
    assert failure.reason == P.REASON_BASE_URL
    assert failure.raw == "_HttpError: Error code: 404"


def test_l2_raw_masks_the_api_key():
    """原文要留，但**绝不能留 Key** —— 探测结果会被写进审计与日志。"""
    exc = _HttpError("Error code: 401 - Incorrect API key provided: sk-secret")
    failure = P._classify_l2_failure(exc, api_key="sk-secret")

    assert "sk-secret" not in failure.raw
    assert "<API_KEY>" in failure.raw
    assert "sk-secret" not in failure.summary


def test_l2_404_with_structured_body_still_blames_model_name():
    """带结构化 body 的 404 才是真的「模型不存在」，不能被上一条抢走。"""
    exc = _HttpError(
        "Error code: 404 - {'error': {'code': 'model_not_found', 'message': 'no such model'}}",
        status_code=404,
    )
    failure = P._classify_l2_failure(exc)
    assert "模型名" in failure.summary
    assert "/compatible-mode" not in failure.summary
    assert failure.reason == P.REASON_MODEL_NAME


def test_l2_detail_is_clipped():
    detail = P._clip("x" * 500)
    assert len(detail) < 500
    assert detail.endswith("…（已截断）")


def test_probe_client_strips_token_plan_registration_suffix(monkeypatch):
    """Token Plan 的 @tp 是内部注册名，出站请求必须使用真实模型名。"""
    import langchain_openai

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _capture)

    P.build_probe_client(
        "openai",
        model_name="qwen3.7-plus@tp",
        api_key="sk-test",
        base_url="https://api.example.com/v1",
    )

    assert captured["model"] == "qwen3.7-plus"


def test_fast_siliconflow_qwen3_probe_disables_thinking(monkeypatch):
    """Qwen3 快速连通性探测不应等待完整思考链。"""
    import langchain_openai

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _capture)

    P.build_probe_client(
        "openai",
        model_name="Qwen/Qwen3-32B",
        api_key="sk-test",
        base_url="https://api.siliconflow.cn/v1",
        fast=True,
    )

    assert captured["extra_body"] == {"enable_thinking": False}
    assert captured["max_tokens"] == 1


def test_embedding_probe_uses_specialized_request_and_returns_uniform_result(monkeypatch):
    captured: dict = {}

    class _SpecializedResult:
        ok = True
        status_code = 200
        summary = "专项模型调用通过"
        detail = "已收到有效响应"
        elapsed_ms = 37

        def to_dict(self):
            return {"ok": self.ok, "statusCode": self.status_code}

    async def _probe(**kwargs):
        captured.update(kwargs)
        return _SpecializedResult()

    monkeypatch.setattr(P.specialized_model_probe, "probe_specialized", _probe)

    result = _run(P.probe_provider(
        driver="openai",
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model_name="text-embedding-3-large",
        model_kind="embedding",
    ))

    assert result.ok is True
    assert captured["role"] == "embedding"
    assert captured["adapter"] == "openai_embedding"
    assert result.steps[0].level == "L2"
    assert result.steps[0].elapsed_ms == 37


def test_legacy_specialized_driver_uses_the_same_generic_embedding_probe(monkeypatch):
    """历史专项供应商在供应商页也必须走统一的用途探测。"""
    captured: dict = {}

    class _SpecializedResult:
        ok = True
        status_code = 200
        summary = "专项模型调用通过"
        detail = "已收到有效响应"
        elapsed_ms = 19

    async def _probe(**kwargs):
        captured.update(kwargs)
        return _SpecializedResult()

    monkeypatch.setattr(P.specialized_model_probe, "probe_specialized", _probe)

    result = _run(P.probe_provider(
        driver="specialized",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-test",
        model_name="qwen3.7-text-embedding",
        model_kind="embedding",
    ))

    assert result.ok is True
    assert captured["role"] == "embedding"


def test_rerank_probe_selects_protocol_from_registered_provider_url(monkeypatch):
    """供应商页按普通模型方式新增重排时，DashScope 与 SiliconFlow 不能共用端点。"""
    captured: list[dict] = []

    class _SpecializedResult:
        ok = True
        status_code = 200
        summary = "专项模型调用通过"
        detail = "已收到有效响应"
        elapsed_ms = 12

    async def _probe(**kwargs):
        captured.append(kwargs)
        return _SpecializedResult()

    monkeypatch.setattr(P.specialized_model_probe, "probe_specialized", _probe)

    dashscope = _run(P.probe_provider(
        driver="openai",
        base_url="https://dashscope.aliyuncs.com/api/v1",
        api_key="sk-dashscope",
        model_name="qwen3.7-text-rerank",
        model_kind="rerank",
    ))
    siliconflow = _run(P.probe_provider(
        driver="openai",
        base_url="https://api.siliconflow.cn/v1",
        api_key="sk-siliconflow",
        model_name="BAAI/bge-reranker-v2-m3",
        model_kind="rerank",
    ))

    assert dashscope.ok is True
    assert siliconflow.ok is True
    assert [item["adapter"] for item in captured] == [
        "dashscope_rerank",
        "jina_rerank",
    ]


# ── 编排：只有 L0 短路，判定只看 L2 ─────────────────────────────────────


def test_l0_failure_short_circuits(monkeypatch):
    monkeypatch.setattr(P, "probe_l0", _async_step("L0", P.STATUS_FAIL, "地址不通"))
    called = {"n": 0}

    async def _l2(*_a, **_k):
        called["n"] += 1
        return P.ProbeStep("L2", P.STATUS_PASS, "ok")

    monkeypatch.setattr(P, "probe_l2", _l2)

    res = _run(P.probe_provider(
        driver="openai", base_url="https://x/v1", api_key="k", model_name="m"
    ))
    assert res.ok is False
    assert res.blocked_at == "L0"
    assert called["n"] == 0
    assert [s.level for s in res.steps] == ["L0"]


def test_probe_chain_has_no_l1(monkeypatch):
    """L1 必须留在探测链之外（2026-09-21 分层重划，详见模块头）。

    原 L1 唯一的产出是模型名清单，而那份清单属于「填写阶段」的输入辅助、不是判定结论。
    挂在链上只会串行拖慢成功路径（8s 超时 + 大量站点不实现 `/models`），并让「拿不到
    清单」被误读成供应商问题。它已改由 `fetch_model_catalog()` 按需提供。
    """
    monkeypatch.setattr(P, "probe_l0", _async_step("L0", P.STATUS_PASS, "可达"))
    monkeypatch.setattr(P, "probe_l2", _async_step("L2", P.STATUS_PASS, "模型可用"))
    monkeypatch.setattr(P, "probe_l3", _async_step("L3", P.STATUS_PASS, "usage"))

    res = _run(P.probe_provider(driver="openai", base_url="https://x/v1",
                                api_key="k", model_name="m",
                                include_stream_usage=True))

    assert res.ok is True
    assert [s.level for s in res.steps] == ["L0", "L2", "L3"]
    # 探测模块里不应再存在 probe_l1 —— 能力已迁到按需的目录接口
    assert not hasattr(P, "probe_l1")
    assert hasattr(P, "fetch_model_catalog")


def test_fast_probe_skips_stream_usage_after_l2(monkeypatch):
    """快速测试只验证模型可调用，不因流式 usage 阻塞。"""
    monkeypatch.setattr(P, "probe_l0", _async_step("L0", P.STATUS_PASS, "可达"))
    captured: dict = {}

    async def _l2(*_args, **kwargs):
        captured.update(kwargs)
        return P.ProbeStep("L2", P.STATUS_PASS, "模型可用")

    monkeypatch.setattr(P, "probe_l2", _l2)

    async def _unexpected_l3(*_a, **_k):
        pytest.fail("快速测试不应调用流式 usage 探测")

    monkeypatch.setattr(P, "probe_l3", _unexpected_l3)

    res = _run(P.probe_provider(
        driver="openai", base_url="https://x/v1", api_key="k", model_name="m",
        include_stream_usage=False,
    ))

    assert res.ok is True
    assert res.blocked_at is None
    assert captured["fast"] is True
    assert [s.level for s in res.steps] == ["L0", "L2", "L3"]
    assert res.steps[-1].status == P.STATUS_SKIP
    assert "快速测试" in res.steps[-1].summary


def test_l2_failure_blocks_with_attribution(monkeypatch):
    monkeypatch.setattr(P, "probe_l0", _async_step("L0", P.STATUS_PASS, "可达"))
    monkeypatch.setattr(P, "probe_l2", _async_step("L2", P.STATUS_FAIL, "模型名错误"))

    res = _run(P.probe_provider(driver="openai", base_url="https://x/v1",
                                api_key="k", model_name="m"))
    assert res.ok is False
    assert res.blocked_at == "L2"
    assert "L3" not in [s.level for s in res.steps]


def test_l3_skip_does_not_block(monkeypatch):
    monkeypatch.setattr(P, "probe_l0", _async_step("L0", P.STATUS_PASS, "可达"))
    monkeypatch.setattr(P, "probe_l2", _async_step("L2", P.STATUS_PASS, "ok"))
    monkeypatch.setattr(P, "probe_l3", _async_step("L3", P.STATUS_SKIP, "不回传 usage"))

    res = _run(P.probe_provider(driver="openai", base_url="https://x/v1",
                                api_key="k", model_name="m",
                                include_stream_usage=True))
    assert res.ok is True
    assert res.summary == "厂商连通性通过"


# ── 结构性契约：探测排除在统计之外 ──────────────────────────────────────


def test_probe_module_never_imports_proxy_or_usage_tracking():
    """B.4 硬约束 3 由「不经 proxy」结构性满足 —— 用 AST 守这条，别改成走 proxy。"""
    tree = ast.parse(Path(P.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    forbidden = ("infra.llm.proxy", "token_tracker", "llm_usage_attribution")
    for mod in imported:
        assert not any(f in mod for f in forbidden), f"探测模块不得依赖 {mod}"


def test_probe_result_dict_shape():
    res = P.ProbeResult(ok=True, blocked_at=None, summary="ok",
                        steps=[P.ProbeStep("L0", P.STATUS_PASS, "可达")])
    d = res.to_dict()
    assert set(d) == {"ok", "blocked_at", "summary", "steps"}
    assert set(d["steps"][0]) == {
        "level", "status", "summary", "detail", "elapsed_ms", "reason",
    }


def test_catalog_dict_shape():
    """前端据此渲染分组与「去修」动作 —— 改字段名要同步 modelConfig.ts。"""
    cat = P.ModelCatalog(
        ok=True, status=P.STATUS_PASS, summary="共 1 个模型可选",
        items=[P.ModelCatalogItem(id="m", kind="chat")],
    )
    d = cat.to_dict()
    assert set(d) == {
        "ok", "status", "summary", "reason", "items", "count", "total",
        "truncated", "shape_ok", "cached",
    }
    assert d["items"][0] == {"id": "m", "kind": "chat"}
