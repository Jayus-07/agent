"""services/provider_probe.py —— 四级探测契约（P1b）

锁定三件事：

1. **私网放行只能来自显式 `network_scope`**，且云元数据地址即便放行也拦（B.6）。
   这是防 DNS rebinding 的关键，不能靠「解析出来是私网就自动放行」。
2. **L1 失败不判死**（B.4 硬约束 1）：只有 L0 失败短路。若哪天有人把 L1 的
   404/401 也改成短路，本文件会红 —— 那是刻意的，它会毁掉测试按钮的可信度。
3. **探测不经 proxy**（B.4 硬约束 3）：用 AST 断言 import 列表，避免「探测烧预算
   / 污染 token 统计」这类只在运行时才暴露的问题。
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from backend.services import provider_probe as P


def _run(coro):
    return asyncio.run(coro)


def _async_step(level: str, status: str, summary: str):
    async def _f(*_a, **_k) -> P.ProbeStep:
        return P.ProbeStep(level=level, status=status, summary=summary)
    return _f


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


# ── L1：404 降级而非判死 ────────────────────────────────────────────────


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


def _patch_httpx(monkeypatch, response: _FakeResponse) -> None:
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_k: _FakeClient(response))


def test_l1_200_is_pass(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResponse(200, '{"data": []}'))
    step = _run(P.probe_l1("https://api.example.com/v1", "sk-x"))
    assert step.status == P.STATUS_PASS


def test_l1_404_is_degraded_and_hints_missing_v1(monkeypatch):
    """404 → 降级不判死，并提示可能缺 /v1（B.4 硬约束 1 + B.7 归一化提示）。"""
    _patch_httpx(monkeypatch, _FakeResponse(404, "not found"))
    step = _run(P.probe_l1("https://api.example.com", "sk-x"))
    assert step.status == P.STATUS_DEGRADED
    assert "/v1" in step.summary


def test_l1_401_is_fail_pointing_at_key(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResponse(401, "unauthorized"))
    step = _run(P.probe_l1("https://api.example.com/v1", "sk-bad"))
    assert step.status == P.STATUS_FAIL
    assert "API Key" in step.summary


# ── L2：Key 错 vs 模型名错 ──────────────────────────────────────────────


@pytest.mark.parametrize("message,expected", [
    ("Error code: 401 - invalid api key provided", "Key"),
    ("NotFoundError: model not found: gpt-9", "模型名"),
    ("Model `foo` does not exist", "模型名"),
    ("connection reset by peer", "最小调用失败"),
])
def test_l2_failure_attribution(message, expected):
    out = P._classify_l2_failure(RuntimeError(message))
    assert expected in out


def test_l2_detail_is_clipped():
    detail = P._clip("x" * 500)
    assert len(detail) < 500
    assert detail.endswith("…（已截断）")


# ── 编排：只有 L0 短路 ──────────────────────────────────────────────────


def test_l0_failure_short_circuits(monkeypatch):
    monkeypatch.setattr(P, "probe_l0", _async_step("L0", P.STATUS_FAIL, "地址不通"))
    called = {"n": 0}

    async def _l1(*_a, **_k):
        called["n"] += 1
        return P.ProbeStep("L1", P.STATUS_PASS, "ok")

    monkeypatch.setattr(P, "probe_l1", _l1)

    res = _run(P.probe_provider(driver="openai", base_url="https://x/v1",
                                api_key="k", model_name="m"))
    assert res.ok is False
    assert res.blocked_at == "L0"
    assert called["n"] == 0
    assert [s.level for s in res.steps] == ["L0"]


def test_l1_failure_does_not_short_circuit_and_l2_can_still_pass(monkeypatch):
    """L1 失败（如 401）仍继续跑 L2 —— 有些站点 /models 需额外 scope。"""
    monkeypatch.setattr(P, "probe_l0", _async_step("L0", P.STATUS_PASS, "可达"))
    monkeypatch.setattr(P, "probe_l1", _async_step("L1", P.STATUS_FAIL, "Key 被拒绝"))
    monkeypatch.setattr(P, "probe_l2", _async_step("L2", P.STATUS_PASS, "模型可用"))
    monkeypatch.setattr(P, "probe_l3", _async_step("L3", P.STATUS_PASS, "usage"))

    res = _run(P.probe_provider(driver="openai", base_url="https://x/v1",
                                api_key="k", model_name="m"))
    assert res.ok is True
    assert res.blocked_at is None
    assert [s.level for s in res.steps] == ["L0", "L1", "L2", "L3"]


def test_l2_failure_blocks_with_attribution(monkeypatch):
    monkeypatch.setattr(P, "probe_l0", _async_step("L0", P.STATUS_PASS, "可达"))
    monkeypatch.setattr(P, "probe_l1", _async_step("L1", P.STATUS_DEGRADED, "跳过"))
    monkeypatch.setattr(P, "probe_l2", _async_step("L2", P.STATUS_FAIL, "模型名错误"))

    res = _run(P.probe_provider(driver="openai", base_url="https://x/v1",
                                api_key="k", model_name="m"))
    assert res.ok is False
    assert res.blocked_at == "L2"
    assert "L3" not in [s.level for s in res.steps]


def test_l3_skip_does_not_block(monkeypatch):
    monkeypatch.setattr(P, "probe_l0", _async_step("L0", P.STATUS_PASS, "可达"))
    monkeypatch.setattr(P, "probe_l1", _async_step("L1", P.STATUS_PASS, "ok"))
    monkeypatch.setattr(P, "probe_l2", _async_step("L2", P.STATUS_PASS, "ok"))
    monkeypatch.setattr(P, "probe_l3", _async_step("L3", P.STATUS_SKIP, "不回传 usage"))

    res = _run(P.probe_provider(driver="openai", base_url="https://x/v1",
                                api_key="k", model_name="m"))
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
    assert set(d["steps"][0]) == {"level", "status", "summary", "detail", "elapsed_ms"}
