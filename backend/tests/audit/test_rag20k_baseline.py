from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import backend.audit.rag20k.baseline as baseline_module
from backend.audit.rag20k.baseline import (
    CommandResult,
    collect_baseline,
    collect_configuration,
    collect_docker_state,
    collect_git_state,
    compute_capture_id,
    default_command_runner,
    write_baseline_manifest,
)


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_git_repo(repo: Path) -> None:
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "audit-test@example.invalid")
    _run_git(repo, "config", "user.name", "Audit Test")
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _run_git(repo, "add", "tracked.txt")
    _run_git(repo, "commit", "-m", "initial")


def test_workspace_fingerprint_changes_when_untracked_content_changes(tmp_path: Path) -> None:
    """防止采集器只哈希 tracked diff，遗漏当前工作树的未跟踪实现。"""
    _init_git_repo(tmp_path)
    untracked = tmp_path / "new.txt"
    untracked.write_text("v1", encoding="utf-8")

    first = collect_git_state(tmp_path)
    untracked.write_text("v2", encoding="utf-8")
    second = collect_git_state(tmp_path)

    assert first["untracked_path_count"] == 1
    assert first["untracked_manifest_sha256"] != second["untracked_manifest_sha256"]
    assert first["workspace_sha256"] != second["workspace_sha256"]


def test_untracked_baseline_outputs_do_not_change_workspace_fingerprint(tmp_path: Path) -> None:
    """防止采集器把自己的上一次输出算进下一次 capture_id。"""
    _init_git_repo(tmp_path)
    first = collect_git_state(tmp_path)
    evidence = (
        tmp_path
        / "docs/evidence/rag20k/phase0/baseline/previous/baseline-manifest.json"
    )
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}\n", encoding="utf-8")

    second = collect_git_state(tmp_path)

    assert first["workspace_sha256"] == second["workspace_sha256"]
    assert second["untracked_path_count"] == 0
    assert second["dirty"] is False


def test_fingerprint_exclusion_policy_is_recorded_for_reproducibility(tmp_path: Path) -> None:
    """排除策略若不留痕，事后无法用同一算法复算 capture_id。"""
    _init_git_repo(tmp_path)

    state = collect_git_state(tmp_path)

    assert tuple(state["fingerprint_excludes"]) == (
        "docs/evidence/rag20k/phase0/baseline",
    )


def test_evidence_exclusion_does_not_hide_other_untracked_files(tmp_path: Path) -> None:
    """排除只针对采集器自身输出；业务/计划/测试等未跟踪文件必须照常参与指纹。"""
    _init_git_repo(tmp_path)
    evidence = (
        tmp_path
        / "docs/evidence/rag20k/phase0/baseline/previous/baseline-manifest.json"
    )
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}\n", encoding="utf-8")
    plan = tmp_path / "docs/plans/next-step.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("plan body\n", encoding="utf-8")

    state = collect_git_state(tmp_path)
    baseline = collect_git_state(tmp_path)

    assert state["untracked_path_count"] == 1
    (tmp_path / "docs/plans/next-step.md").write_text("changed\n", encoding="utf-8")
    assert baseline["untracked_manifest_sha256"] != collect_git_state(tmp_path)[
        "untracked_manifest_sha256"
    ]


def test_repeated_collection_after_writing_evidence_keeps_capture_id(tmp_path: Path) -> None:
    """采集→落盘→再采集必须得到同一 capture_id，否则基线不可复现。"""
    _init_git_repo(tmp_path)
    output_root = tmp_path / "docs/evidence/rag20k/phase0/baseline"

    first = collect_baseline(tmp_path)
    write_baseline_manifest(first, output_root)
    second = collect_baseline(tmp_path)

    assert second["capture_id"] == first["capture_id"]
    assert write_baseline_manifest(second, output_root) == (
        output_root / first["capture_id"] / "baseline-manifest.json"
    )


def test_configuration_records_secret_presence_without_secret_value() -> None:
    """防止基线清单把 API key 或数据库口令写入 Git。"""
    config = collect_configuration(
        {
            "DEEPSEEK_API_KEY": "must-not-leak",
            "PGPASSWORD": "also-must-not-leak",
            "RAG_DOC_CANDIDATE_K": "77",
        }
    )

    serialized = json.dumps(config, ensure_ascii=False)
    assert "must-not-leak" not in serialized
    assert "also-must-not-leak" not in serialized
    assert config["DEEPSEEK_API_KEY"] == {"configured": True}
    assert config["PGPASSWORD"] == {"configured": True}
    assert config["RAG_DOC_CANDIDATE_K"] == 77


def test_capture_id_ignores_collection_timestamp() -> None:
    """防止同一环境仅因采集时间不同而生成不同 capture_id。"""
    first = {
        "schema_version": "1.0",
        "captured_at_utc": "2026-09-19T00:00:00Z",
        "git": {"workspace_sha256": "a" * 64},
    }
    second = {
        **first,
        "captured_at_utc": "2026-09-19T00:01:00Z",
    }

    assert compute_capture_id(first) == compute_capture_id(second)
    assert len(compute_capture_id(first)) == 16


def test_docker_state_marks_images_without_digest_as_blocked(tmp_path: Path) -> None:
    """防止本地 image ID 被误报成可追溯的仓库 digest。"""
    compose_output = json.dumps(
        [
            {
                "Service": "postgres",
                "Repository": "pgvector/pgvector",
                "Tag": "pg16",
                "ID": "sha256:local-image-id",
            }
        ]
    )

    def fake_runner(args: tuple[str, ...], cwd: Path) -> CommandResult:
        assert cwd == tmp_path
        if args == ("docker", "compose", "images", "--format", "json"):
            return CommandResult(status="ok", stdout=compose_output, stderr="", returncode=0)
        assert args == (
            "docker",
            "image",
            "inspect",
            "pgvector/pgvector:pg16",
            "--format",
            "{{json .RepoDigests}}",
        )
        return CommandResult(status="ok", stdout="[]", stderr="", returncode=0)

    state, blockers = collect_docker_state(tmp_path, fake_runner)

    assert state["status"] == "blocked"
    assert state["images"][0]["service"] == "postgres"
    assert state["images"][0]["repo_digest"] is None
    assert blockers == ["Docker 镜像 postgres 缺少不可变 RepoDigest"]


def test_docker_state_uses_container_name_and_inspected_repo_digest(tmp_path: Path) -> None:
    """防止 Compose v5 的真实字段被折叠成 unknown 并误报所有 digest 缺失。"""
    compose_output = json.dumps(
        [
            {
                "ID": "sha256:local-image-id",
                "ContainerName": "agent-postgres-1",
                "Repository": "pgvector/pgvector",
                "Tag": "pg16",
            }
        ]
    )

    def fake_runner(args: tuple[str, ...], cwd: Path) -> CommandResult:
        if args == ("docker", "compose", "images", "--format", "json"):
            return CommandResult("ok", compose_output, "", 0)
        assert args == (
            "docker",
            "image",
            "inspect",
            "pgvector/pgvector:pg16",
            "--format",
            "{{json .RepoDigests}}",
        )
        return CommandResult(
            "ok",
            '["pgvector/pgvector@sha256:immutable"]',
            "",
            0,
        )

    state, blockers = collect_docker_state(tmp_path, fake_runner)

    assert blockers == []
    assert state["status"] == "ok"
    assert state["images"] == [
        {
            "service": "agent-postgres-1",
            "repository": "pgvector/pgvector",
            "tag": "pg16",
            "image_id": "sha256:local-image-id",
            "repo_digest": "pgvector/pgvector@sha256:immutable",
        }
    ]


def test_runtime_versions_resolve_windows_command_shims(tmp_path: Path, monkeypatch) -> None:
    """防止 npm.cmd 已在 PATH 中却因直接执行 npm 而误报不可用。"""
    seen: list[tuple[str, ...]] = []

    def fake_which(name: str) -> str | None:
        return "D:/node/npm.cmd" if name == "npm" else None

    def fake_runner(args: tuple[str, ...], cwd: Path) -> CommandResult:
        seen.append(args)
        return CommandResult("ok", "version", "", 0)

    monkeypatch.setattr(baseline_module.shutil, "which", fake_which)
    baseline_module.collect_runtime_versions(tmp_path, fake_runner)

    assert ("D:/node/npm.cmd", "--version") in seen


def test_runtime_versions_prefer_running_postgres_and_redis_servers(tmp_path: Path) -> None:
    """防止把宿主客户端版本当成实际运行的数据库服务版本。"""
    def fake_runner(args: tuple[str, ...], cwd: Path) -> CommandResult:
        if args == ("docker", "compose", "exec", "-T", "postgres", "postgres", "--version"):
            return CommandResult("ok", "postgres (PostgreSQL) 16.14", "", 0)
        if args == ("docker", "compose", "exec", "-T", "redis", "redis-server", "--version"):
            return CommandResult("ok", "Redis server v=7.4.9", "", 0)
        return CommandResult("ok", "fallback-version", "", 0)

    runtime = baseline_module.collect_runtime_versions(tmp_path, fake_runner)

    assert runtime["postgresql"]["version"] == "postgres (PostgreSQL) 16.14"
    assert runtime["redis"]["version"] == "Redis server v=7.4.9"


def test_write_manifest_uses_capture_id_directory(tmp_path: Path) -> None:
    """防止重复采集互相覆盖，丢失审计证据。"""
    manifest = {
        "schema_version": "1.0",
        "captured_at_utc": "2026-09-19T00:00:00Z",
    }
    canonical = json.dumps(
        {"schema_version": "1.0"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected_capture_id = hashlib.sha256(canonical).hexdigest()[:16]
    manifest["capture_id"] = expected_capture_id

    output = write_baseline_manifest(manifest, tmp_path)

    assert output == tmp_path / expected_capture_id / "baseline-manifest.json"
    assert json.loads(output.read_text(encoding="utf-8")) == manifest


def test_collect_baseline_reports_unavailable_docker_without_failing(tmp_path: Path) -> None:
    """防止本机 Docker 不可用时丢失其余仍可采集的基线信息。"""
    _init_git_repo(tmp_path)

    def runner(args: tuple[str, ...], cwd: Path) -> CommandResult:
        if args[:2] == ("docker", "compose"):
            return CommandResult(
                status="unavailable",
                stdout="",
                stderr="Docker Desktop 未运行",
                returncode=1,
            )
        return default_command_runner(args, cwd)

    manifest = collect_baseline(tmp_path, runner)

    assert manifest["schema_version"] == "1.0"
    assert manifest["git"]["dirty"] is False
    assert manifest["docker"]["status"] == "blocked"
    assert "Docker Compose 镜像清单不可用" in manifest["blocking_items"]
    assert manifest["capture_id"] == compute_capture_id(manifest)
