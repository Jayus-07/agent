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
