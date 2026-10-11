"""verify() 绑定校验核心回归测试（2026-10-11 GPT 复核整改 1/4）。

背景：verify 原实现凭据校验硬编码单一 Provider（specialized-api），而 CI
seed（seed_prompt_eval_db）实际注册分离 Provider（rag-eval-embedding /
rag-eval-rerank / rag-eval-chat）。重构后凭据按每个绑定各自的 provider_id
分别校验（evaluate_bindings 纯函数），本文件锁定其行为：

- 分离 Provider ID 且凭据齐全 → PASS；
- 缺任一绑定或凭据 → FAIL；
- embedding 维度不匹配 → FAIL（fail-closed，不回退本地模型）。

纯函数测试，无 DB / 无网络。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from seed_eval_model_bindings import evaluate_bindings  # noqa: E402


def _binding(
    role: str = "embedding",
    provider_id: str = "rag-eval-embedding",
    dims: int | None = 1024,
    model_name: str = "qwen3.7-text-embedding",
    enabled: bool = True,
) -> tuple:
    options = {"dimensions": dims} if dims is not None else {}
    return (role, provider_id, "dashscope_embedding" if role == "embedding"
            else "dashscope_rerank", model_name,
            "https://example.invalid/v1", options, enabled)


def _cred(cipher: str = "enc:VALID", fingerprint: str = "fp"):
    return (cipher, fingerprint)


def _decrypt_ok(cipher: str) -> str:
    assert cipher.startswith("enc:"), "只接受密文形态（与生产 decrypt_secret 同口径）"
    return "plain-key"


def test_split_provider_ids_pass():
    """分离 Provider ID（CI seed 形态）且凭据齐全 → PASS（GPT 整改 1 回归）。"""
    bindings = [
        _binding("embedding", "rag-eval-embedding"),
        _binding("rerank", "rag-eval-rerank", dims=None, model_name="qwen3.7-text-rerank"),
    ]
    credentials = {
        "rag-eval-embedding": _cred("enc:EMB"),
        "rag-eval-rerank": _cred("enc:RR"),
    }
    summary, problems = evaluate_bindings(
        bindings, credentials, expected_dim=1024, decrypt=_decrypt_ok
    )
    assert problems == []
    assert any("rag-eval-embedding" in s for s in summary)
    assert any("rag-eval-rerank" in s for s in summary)
    # 两个分离 Provider 的凭据都被逐一校验
    assert any("credentials[rag-eval-embedding]" in s for s in summary)
    assert any("credentials[rag-eval-rerank]" in s for s in summary)


def test_missing_binding_fails():
    """缺 rerank 绑定 → FAIL（只配 embedding 不够，RAG 检索评测必须双绑定）。"""
    bindings = [_binding("embedding", "rag-eval-embedding")]
    credentials = {"rag-eval-embedding": _cred()}
    summary, problems = evaluate_bindings(
        bindings, credentials, expected_dim=1024, decrypt=_decrypt_ok
    )
    assert any("rerank: binding 缺失" in p for p in problems)


def test_missing_credential_fails():
    """任一绑定的凭据缺失 → FAIL。"""
    bindings = [
        _binding("embedding", "rag-eval-embedding"),
        _binding("rerank", "rag-eval-rerank", dims=None, model_name="qwen3.7-text-rerank"),
    ]
    credentials = {"rag-eval-embedding": _cred()}  # rerank 凭据缺失
    summary, problems = evaluate_bindings(
        bindings, credentials, expected_dim=1024, decrypt=_decrypt_ok
    )
    assert any("rag-eval-rerank: 凭据缺失" in p for p in problems)
    assert problems


def test_dimension_mismatch_fails():
    """embedding 绑定维度与向量存储期望不一致 → FAIL。"""
    bindings = [
        _binding("embedding", "rag-eval-embedding", dims=512),
        _binding("rerank", "rag-eval-rerank", dims=None, model_name="qwen3.7-text-rerank"),
    ]
    credentials = {
        "rag-eval-embedding": _cred(),
        "rag-eval-rerank": _cred(),
    }
    summary, problems = evaluate_bindings(
        bindings, credentials, expected_dim=1024, decrypt=_decrypt_ok
    )
    assert any("维度不匹配" in p and "512" in p for p in problems)


def test_decrypt_failure_fails():
    """凭据解密异常 → FAIL（不静默放行）。"""
    def _bad_decrypt(cipher: str) -> str:
        raise RuntimeError("FernetInvalidToken")

    bindings = [_binding("embedding", "rag-eval-embedding")]
    credentials = {"rag-eval-embedding": _cred()}
    summary, problems = evaluate_bindings(
        bindings, credentials, expected_dim=1024, decrypt=_bad_decrypt
    )
    assert any("解密失败" in p for p in problems)


def test_disabled_binding_fails():
    """绑定被停用 → FAIL。"""
    bindings = [_binding("embedding", "rag-eval-embedding", enabled=False)]
    credentials = {"rag-eval-embedding": _cred()}
    summary, problems = evaluate_bindings(
        bindings, credentials, expected_dim=1024, decrypt=_decrypt_ok
    )
    assert any("binding 未启用" in p for p in problems)


def test_no_bindings_fails():
    """无任何 specialized 绑定 → FAIL（评测保持 BLOCKED）。"""
    summary, problems = evaluate_bindings([], {}, expected_dim=1024, decrypt=_decrypt_ok)
    assert any("无任何 specialized 绑定" in p for p in problems)
