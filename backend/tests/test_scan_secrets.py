"""P0 护栏回归：敏感扫描必须覆盖 artifacts 文本产物与空用户名凭据 URL。

背景（PR #41 审查）：上一版 scan_secrets.py 把整个 artifacts/ 目录放进
SKIP_DIRS，而真实泄漏恰好发生在 artifacts/rag-perf-r0r1/ 的文本日志里；
同时 CREDENTIAL_URL 要求用户名至少 1 个字符，漏检
redis://:password@host:6379/0 这种空用户名形态。

本文件用**完全虚构**的口令（不复制任何真实凭据）锁定这两个回归。
"""
from __future__ import annotations

import subprocess

import pytest

from scripts import scan_secrets as ss

FAKE_EMPTY_USER = "redis://:gitleaks-test!not-real@127.0.0.1:6379/0"
FAKE_USER_PASS = "redis://tester:gitleaks-test!not-real@127.0.0.1:6379/0"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8")


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    for args in (("init", "-q"), ("config", "user.email", "test@example.com"),
                 ("config", "user.name", "test")):
        _git(path, *args)
    return path


def test_credential_url_matches_empty_username():
    """空用户名形态必须命中（事故 URL 形态）。"""
    assert ss.CREDENTIAL_URL.search(FAKE_EMPTY_USER)
    assert ss.CREDENTIAL_URL.search(FAKE_USER_PASS)


def test_artifact_dir_is_scanned(tmp_path):
    """artifacts/ 下的已跟踪文本文件必须被扫描（不得整目录豁免）。"""
    assert "artifacts" not in ss.SKIP_DIRS, "artifacts 不得整目录豁免"
    repo = _init_repo(tmp_path / "repo")
    target = repo / "artifacts" / "rag-perf-r0r1"
    target.mkdir(parents=True)
    (target / "run.log").write_text("connecting " + FAKE_EMPTY_USER + "\n", encoding="utf-8")
    _git(repo, "add", "-f", "artifacts/rag-perf-r0r1/run.log")

    findings = ss.scan_tree(None, list(ss.GENERIC_RULES), root=repo)
    hit = [(path, name) for path, name, _line in findings]
    assert ("artifacts/rag-perf-r0r1/run.log", "url_with_credentials") in hit, hit


def test_env_example_dsn_template_is_not_blocking():
    """仓库 .env.example 的 DSN 模板（fictional 占位口令）不得阻断 CI。"""
    line = "DC_DATABASE_URL=postgresql://postgres:your_password_here@localhost:5432/demo"
    assert ss.CREDENTIAL_URL.search(line)
    assert ss._is_allowed("backend/.env.example", "url_with_credentials", line)
    # 换成真实形态则必须命中
    real = "DC_DATABASE_URL=postgresql://postgres:realpass0987654321@db:5432/prod"
    assert not ss._is_allowed("backend/.env.example", "url_with_credentials", real)


def test_placeholder_urls_are_not_blocking():
    """示例/变量插值属占位，不阻断：user:pass@ 与 ${VAR} 两种。"""
    assert ss._is_allowed("x.yml", "url_with_credentials", "dsn: postgresql://user:pass@host/db")
    assert ss._is_allowed("docker-compose.yml", "url_with_credentials",
                          "AUTH_REDIS_URL=redis://:${AUTH_REDIS_PASSWORD}@redis:6379/0")
    assert not ss._is_allowed("app.log", "url_with_credentials", FAKE_EMPTY_USER)


def test_history_scan_range_limits_scope(tmp_path):
    """历史扫描支持区间限定：新增提交之外的历史泄漏不参与阻断。"""
    repo = _init_repo(tmp_path / "hrepo")
    (repo / "old.txt").write_text("legacy " + FAKE_USER_PASS + "\n", encoding="utf-8")
    _git(repo, "add", "old.txt")
    _git(repo, "commit", "-qm", "base with legacy leak")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "new.txt").write_text("no credentials here\n", encoding="utf-8")
    _git(repo, "add", "new.txt")
    _git(repo, "commit", "-qm", "clean change")

    ranged = ss.scan_history(f"{base}..HEAD", list(ss.GENERIC_RULES), root=repo)
    assert ranged == [], "新增区间不应报出基线里的历史泄漏"
    all_history = ss.scan_history(None, list(ss.GENERIC_RULES), root=repo)
    assert any(path == "old.txt" for _rev, path, _name in all_history), "全历史扫描仍应看到旧泄漏"

def test_same_line_variable_and_hardcoded_credential_is_flagged():
    """同一行既有 ${VAR} 插值又有硬编码凭据：必须命中（不得整行放行）。"""
    line = ("CMD=redis://:${REDIS_PASSWORD}@cache:6379/0 BACKUP=" + FAKE_EMPTY_USER)
    assert not ss._is_allowed("scripts/run.sh", "url_with_credentials", line)
    # 纯插值仍然允许
    only_var = "AUTH_REDIS_URL=redis://:${AUTH_REDIS_PASSWORD}@redis:6379/0"
    assert ss._is_allowed("docker-compose.yml", "url_with_credentials", only_var)


def test_diff_mode_blocks_new_hardcoded_key_but_not_baseline(tmp_path):
    """新增硬编码 API_KEY 必须阻断；基线里的旧密钥只算存量债务。"""
    repo = _init_repo(tmp_path / "diffrepo")
    (repo / "config.py").write_text('API_KEY = "OLDLEGACYKEY1234567890"', encoding="utf-8")
    _git(repo, "add", "config.py")
    _git(repo, "commit", "-qm", "baseline with legacy hardcoded key")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # 新增提交：又写了一个新的硬编码密钥（虚构）
    (repo / "config.py").write_text(
        'API_KEY = "OLDLEGACYKEY1234567890"\nNEW_API_KEY = "NEWHARDCODEDKEY0987654321"\n',
        encoding="utf-8")
    _git(repo, "add", "config.py")
    _git(repo, "commit", "-qm", "add another hardcoded key")

    diff_rules = [r for r in ss.GENERIC_RULES if r[0] in ss.DIFF_RULES]
    findings = ss.scan_diff(f"{base}..HEAD", diff_rules, root=repo)
    assert any(path == "config.py" for path, _name in findings), (
        "新增硬编码密钥未被 diff 模式拦住")
    # 基线提交本身不该出现在 diff 结果里（存量不阻断）
    base_findings = ss.scan_diff(f"{base}..{base}", diff_rules, root=repo)
    assert base_findings == []


def test_diff_mode_ignores_env_and_placeholder_assignments(tmp_path):
    """新增行里读环境变量/变量插值不算硬编码密钥（避免误伤）。"""
    repo = _init_repo(tmp_path / "diffrepo2")
    (repo / "config.py").write_text("API_KEY = \"OLDLEGACYKEY1234567890\"\n", encoding="utf-8")
    _git(repo, "add", "config.py")
    _git(repo, "commit", "-qm", "baseline")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "config.py").write_text(
        "API_KEY = os.environ[\"API_KEY\"]\nREDIS = \"redis://:${REDIS_PASSWORD}@cache:6379/0\"\n",
        encoding="utf-8")
    _git(repo, "add", "config.py")
    _git(repo, "commit", "-qm", "read from env instead")

    diff_rules = [r for r in ss.GENERIC_RULES if r[0] in ss.DIFF_RULES]
    findings = ss.scan_diff(f"{base}..HEAD", diff_rules, root=repo)
    assert findings == [], findings
