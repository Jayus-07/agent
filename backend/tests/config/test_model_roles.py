"""test_model_roles.py — 模型角色注册表与统一解析入口（P0，2026-09-19）

覆盖 docs/model-config-governance-design.md §3 的契约：

  - resolve_raw：字面值（DB 覆盖 → env → 代码默认），**不展开 inherit**
  - resolve_effective：实际生效值（多了 inherit 展开）
  - 大小写保留（MiniMax-M3 / Qwen/Qwen3-32B / BAAI/bge-m3 不得被 lower）
  - 空值有语义的角色必须保留空串（消费方用 `if DOC_LLM_MODEL:` 判断）
  - get_secret 的 fail-loud 契约（绝不返回密文/占位符）
  - 导入链约束（不得把 langchain / SQLAlchemy 拖进 backend.config）

不连 DB、不连网络、不加载真实模型。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.config import model_roles
from backend.config.model_roles import (
    MODEL_ROLES,
    SOURCE_DB,
    SOURCE_DEFAULT,
    SOURCE_ENV,
    effective_snapshot,
    get_secret,
    inject_overrides,
    provider_of,
    resolve_effective,
    resolve_name,
    resolve_raw,
    reset_overrides,
    validate_roles,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean_overrides():
    """_overrides 是模块级状态，前后各清一次防止跨测试污染。"""
    reset_overrides()
    yield
    reset_overrides()


def _clear_role_env(monkeypatch) -> None:
    """清掉全部角色的 env，避免宿主机 .env / 环境变量影响判定。"""
    for spec in MODEL_ROLES.values():
        monkeypatch.delenv(spec.env_key, raising=False)


# =====================================================
# 注册表完整性
# =====================================================

def test_all_roles_registered():
    assert set(MODEL_ROLES) == {
        "main", "doc", "tool_selector", "fallback",
        "ocr", "embedding", "rerank", "eval_gen",
    }


def test_every_role_has_env_key_and_desc():
    for role, spec in MODEL_ROLES.items():
        assert spec.env_key, f"{role} 缺 env_key"
        assert spec.desc, f"{role} 缺 desc"
        assert spec.env_key.isupper(), f"{role}.env_key 应为大写 env 名"


def test_inherit_targets_exist():
    for role, spec in MODEL_ROLES.items():
        if spec.inherit is not None:
            assert spec.inherit in MODEL_ROLES, f"{role} 的 inherit 指向未注册角色"


# =====================================================
# resolve_raw：字面值
# =====================================================

def test_raw_reads_env(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    info = resolve_raw("main")
    assert info["value"] == "qwen3.7-plus"
    assert info["source"] == SOURCE_ENV
    assert info["explicit"] is True


def test_raw_falls_back_to_code_default(monkeypatch):
    _clear_role_env(monkeypatch)
    info = resolve_raw("embedding")
    assert info["value"] == "text-embedding-v3"   # 与改造前 config/llm.py 默认一致
    assert info["source"] == SOURCE_DEFAULT
    assert info["explicit"] is False


def test_raw_unknown_role_raises(monkeypatch):
    with pytest.raises(KeyError):
        resolve_raw("no_such_role")


def test_raw_strips_surrounding_whitespace(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "  qwen3.7-plus  ")
    assert resolve_raw("main")["value"] == "qwen3.7-plus"


# =====================================================
# 大小写保留（硬约束 3）
# =====================================================

@pytest.mark.parametrize("value", [
    "MiniMax-M3",
    "Qwen/Qwen3-32B",
    "BAAI/bge-m3",
    "Qwen/Qwen3-32B-AWQ",
])
def test_case_preserved(monkeypatch, value):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", value)
    assert resolve_raw("main")["value"] == value
    assert resolve_effective("main")["value"] == value


# =====================================================
# 空值语义（P0 最容易被"顺手修坏"的地方）
# =====================================================

@pytest.mark.parametrize("role,env_key", [
    ("doc", "DOC_LLM_MODEL"),
    ("tool_selector", "TOOL_SELECTOR_MODEL"),
    ("fallback", "LLM_FALLBACK_MODEL"),
])
def test_empty_env_stays_empty_in_raw(monkeypatch, role, env_key):
    """这些角色的空串在消费方手里有语义（`if DOC_LLM_MODEL:` 判断是否启用
    本地 Ollama）。resolve_raw 必须保留空串，不能物化成继承值。"""
    _clear_role_env(monkeypatch)
    assert resolve_raw(role)["value"] == ""
    assert resolve_name(role) == ""


def test_effective_expands_inherit_when_empty(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    eff = resolve_effective("doc")
    assert eff["value"] == "qwen3.7-plus"
    assert eff["source"] == "inherit:main"
    assert eff["inherited_from"] == "main"
    assert eff["explicit"] is False


def test_effective_does_not_expand_when_env_set(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    monkeypatch.setenv("DOC_LLM_MODEL", "qwen2.5:3b")
    eff = resolve_effective("doc")
    assert eff["value"] == "qwen2.5:3b"
    assert eff["source"] == SOURCE_ENV
    assert eff["inherited_from"] is None


def test_role_without_inherit_returns_empty(monkeypatch):
    """fallback 空值 = 不切备用模型，无继承概念，空就是空。"""
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    assert resolve_effective("fallback")["value"] == ""


# =====================================================
# DB 覆盖层（P1 由 sys_config 注入）
# =====================================================

def test_db_override_beats_env(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    inject_overrides({"main": "MiniMax-M3"}, {"main": {"updatedBy": "user:1"}})
    info = resolve_raw("main")
    assert info["value"] == "MiniMax-M3"
    assert info["source"] == SOURCE_DB
    assert info["updated_by"] == "user:1"


def test_override_removed_falls_back_to_env(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    inject_overrides({"main": "MiniMax-M3"})
    assert resolve_raw("main")["source"] == SOURCE_DB
    reset_overrides()
    assert resolve_raw("main")["value"] == "qwen3.7-plus"
    assert resolve_raw("main")["source"] == SOURCE_ENV


def test_override_propagates_through_inherit(monkeypatch):
    """DB 改 main 后，跟随 main 的 doc 生效值应同步变化。"""
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    inject_overrides({"main": "MiniMax-M3"})
    assert resolve_effective("doc")["value"] == "MiniMax-M3"
    assert resolve_effective("doc")["source"] == "inherit:main"


def test_override_ignores_unknown_role():
    inject_overrides({"no_such_role": "x", "main": "MiniMax-M3"})
    assert resolve_raw("main")["source"] == SOURCE_DB
    with pytest.raises(KeyError):
        resolve_raw("no_such_role")


# =====================================================
# get_secret：fail-loud 契约（设计文档 §4.4）
# =====================================================

def test_secret_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert get_secret("deepseek") is None


def test_secret_returns_value_when_set(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-key")
    assert get_secret("deepseek") == "sk-real-key"


def test_secret_none_for_local_provider(monkeypatch):
    """ollama 无需密钥（PROVIDER_API_KEY_ENV 中为 None）。"""
    assert get_secret("ollama") is None


def test_secret_none_for_unknown_provider():
    assert get_secret("no_such_provider") is None


def test_secret_none_for_empty_provider():
    assert get_secret("") is None


def test_secret_never_returns_ciphertext(monkeypatch):
    """env 里若出现 enc: 前缀的密文（P1 之前不该有），必须视为坏值返回 None。

    否则密文会被当 API Key 发给 provider，换回 401 而真因被埋 ——
    与 competitor/crypto.py:decrypt_cookie 的静默返回原文是同一类坑。
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "enc:gAAAAABfakeciphertext")
    assert get_secret("deepseek") is None


# =====================================================
# provider 归属
# =====================================================

def test_provider_of_registered_model():
    assert provider_of("qwen3.7-plus") == "qwen"
    assert provider_of("deepseek-v4-flash") == "deepseek"


def test_provider_of_unregistered_model_is_none():
    """embedding/rerank/ocr 的模型不在 AVAILABLE_MODELS 内，应返回 None 而非报错。"""
    assert provider_of("BAAI/bge-m3") is None
    assert provider_of("") is None


# =====================================================
# 快照 / 校验
# =====================================================

def test_snapshot_covers_all_roles_with_source():
    snap = {item["role"]: item for item in effective_snapshot()}
    assert set(snap) == set(MODEL_ROLES)
    for item in snap.values():
        assert item["source"]
        assert item["envKey"]
        assert "value" in item and "rawValue" in item


def test_snapshot_flags_reindex_requirement():
    snap = {item["role"]: item for item in effective_snapshot()}
    assert snap["embedding"]["requiresReindex"] is True
    assert snap["rerank"]["requiresReindex"] is False


def test_snapshot_flags_empty_has_meaning():
    snap = {item["role"]: item for item in effective_snapshot()}
    assert snap["doc"]["emptyHasMeaning"] is True
    assert snap["embedding"]["emptyHasMeaning"] is False


def test_validate_roles_catches_unregistered(monkeypatch):
    """配了未注册的模型名必须被点名。

    改造前 LLM_FALLBACK_MODEL 的"须在 AVAILABLE_MODELS 注册"只是 .env 里的
    一句注释，无任何强制 —— 这正是本用例要锁住的回归。
    """
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "Not/A-Registered-Model")
    warns = validate_roles()
    assert any("fallback" in w and "未在 AVAILABLE_MODELS 注册" in w for w in warns)


def test_validate_roles_silent_when_all_registered(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "Qwen/Qwen3-32B")
    monkeypatch.setenv("DOC_LLM_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("TOOL_SELECTOR_MODEL", "deepseek-v4-flash")
    assert validate_roles() == []


def test_validate_roles_skips_empty_values(monkeypatch):
    """空值（未配置）不算错误 —— 空有自己的语义。"""
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    assert validate_roles() == []


# =====================================================
# 导入链约束（硬约束 1 / 2）
# =====================================================

def test_no_heavy_import_in_config_chain():
    """backend.config.model_roles 处在 backend.config 导入链上，
    不得把 langchain / torch / SQLAlchemy / transformers 拖进来。

    子进程验证（同进程内其他测试已把重依赖导入了，断言会失真）。
    """
    code = (
        "import sys, backend.config.model_roles as m;"
        "heavy=[x for x in ('langchain','langchain_core','torch','sqlalchemy',"
        "'transformers','langchain_ollama') if x in sys.modules];"
        "print(','.join(heavy))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT)},
    )
    assert proc.returncode == 0, f"导入失败:\n{proc.stderr}"
    assert proc.stdout.strip() == "", f"重依赖被拖入配置导入链: {proc.stdout.strip()}"


def test_module_does_not_import_infra_llm_at_module_level():
    """模块级不得 import backend.infra.llm.*（会执行其 __init__ → langchain，
    并与 proxy.py 形成循环导入）。源码级检查，防止后续被"顺手加回来"。"""
    src = (_REPO_ROOT / "backend" / "config" / "model_roles.py").read_text(encoding="utf-8")
    top_level = []
    for line in src.splitlines():
        if line.startswith(("import ", "from ")) and "backend.infra.llm" in line:
            top_level.append(line)
    assert not top_level, f"模块级出现了 infra.llm 导入: {top_level}"
