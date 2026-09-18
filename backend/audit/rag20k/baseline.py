"""RAG 20k 阶段 0 的只读、可复现基线采集。

该模块只读取 Git、版本命令和显式白名单配置。它不启动服务、不访问数据库，
也不会把密钥、口令或工作树 diff 正文写入证据文件。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CommandResult:
    """只读外部命令的规范化结果。"""

    status: str
    stdout: str
    stderr: str
    returncode: int


CommandRunner = Callable[[tuple[str, ...], Path], CommandResult]

# 证据输出根是采集器自身的产物：若不排除，第二次采集会把第一次的证据算进
# 工作树指纹，导致相同环境产生不同 capture_id（自引用）。已提交的证据属于
# 仓库内容，经 commit 哈希参与指纹，不受该排除影响。
BASELINE_EVIDENCE_ROOT = Path("docs/evidence/rag20k/phase0/baseline")
_DEFAULT_FINGERPRINT_EXCLUDES: tuple[str, ...] = (BASELINE_EVIDENCE_ROOT.as_posix(),)


def _is_under(relative_path: str, exclude_roots: Sequence[str]) -> bool:
    normalized = relative_path.replace("\\", "/").rstrip("/")
    return any(
        normalized == root or normalized.startswith(root + "/")
        for root in exclude_roots
    )


def default_command_runner(args: tuple[str, ...], cwd: Path) -> CommandResult:
    """不经 shell 执行只读命令，避免参数注入和窗口弹出。"""

    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError as exc:
        return CommandResult("unavailable", "", str(exc), 127)
    except subprocess.TimeoutExpired as exc:
        return CommandResult("timeout", exc.stdout or "", exc.stderr or "", 124)

    status = "ok" if completed.returncode == 0 else "error"
    return CommandResult(
        status=status,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        returncode=completed.returncode,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _command_stdout(
    runner: CommandRunner,
    args: Sequence[str],
    cwd: Path,
    *,
    fallback: str = "unknown",
) -> str:
    result = runner(tuple(args), cwd)
    return result.stdout.strip() if result.status == "ok" and result.stdout.strip() else fallback


def _safe_untracked_digest(repo_root: Path, relative_path: str) -> str:
    """哈希仓库内文件；符号链接或越界路径只记录不可读标记。"""

    candidate = repo_root / relative_path
    try:
        if candidate.is_symlink():
            return _sha256_bytes(b"SYMLINK_NOT_FOLLOWED")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_root.resolve())
        if not resolved.is_file():
            return _sha256_bytes(b"NOT_A_REGULAR_FILE")
        return _sha256_file(resolved)
    except (FileNotFoundError, OSError, ValueError):
        return _sha256_bytes(b"UNREADABLE_OR_OUTSIDE_REPOSITORY")


def collect_git_state(
    repo_root: Path,
    command_runner: CommandRunner | None = None,
    *,
    fingerprint_excludes: Sequence[str] | None = None,
) -> dict[str, object]:
    """采集 commit 与完整工作树指纹，不保存 diff 正文。

    ``fingerprint_excludes`` 为仓库相对目录（POSIX 风格）；其下的未跟踪文件
    不参与指纹与 dirty 判定。``None`` 使用默认排除（基线证据输出根），
    空元组表示不排除任何路径。
    """

    root = repo_root.resolve()
    runner = command_runner or default_command_runner
    excludes = (
        _DEFAULT_FINGERPRINT_EXCLUDES
        if fingerprint_excludes is None
        else tuple(fingerprint_excludes)
    )
    commit = _command_stdout(runner, ("git", "rev-parse", "HEAD"), root)
    branch = _command_stdout(runner, ("git", "branch", "--show-current"), root, fallback="detached")

    diff_result = runner(("git", "diff", "--binary", "HEAD"), root)
    diff_text = diff_result.stdout if diff_result.status == "ok" else ""
    tracked_diff_sha256 = _sha256_bytes(diff_text.encode("utf-8"))

    untracked_result = runner(("git", "ls-files", "--others", "--exclude-standard", "-z"), root)
    untracked_paths = sorted(
        path
        for path in untracked_result.stdout.split("\0")
        if path and not _is_under(path, excludes)
    ) if untracked_result.status == "ok" else []
    untracked_lines = [
        f"{path}\0{_safe_untracked_digest(root, path)}"
        for path in untracked_paths
    ]
    untracked_manifest_sha256 = _sha256_bytes("\n".join(untracked_lines).encode("utf-8"))

    # -uall 逐文件列出未跟踪路径；默认的目录折叠（如 `?? docs/`）会让
    # 按路径前缀排除证据目录时把同目录下的其他未跟踪文件一起误伤/漏算。
    status_result = runner(
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
        root,
    )
    status_entries = status_result.stdout.split("\0") if status_result.status == "ok" else []

    def _excluded_untracked_entry(entry: str) -> bool:
        return entry.startswith("?? ") and _is_under(entry[3:], excludes)

    changed_path_count = sum(
        1 for entry in status_entries
        if len(entry) >= 3 and entry[2] == " " and not _excluded_untracked_entry(entry)
    )
    dirty = any(
        entry and not _excluded_untracked_entry(entry)
        for entry in status_entries
    )
    workspace_sha256 = _sha256_bytes(
        f"{tracked_diff_sha256}\n{untracked_manifest_sha256}".encode("ascii")
    )

    return {
        "commit": commit,
        "branch": branch,
        "dirty": dirty,
        "tracked_diff_sha256": tracked_diff_sha256,
        "untracked_manifest_sha256": untracked_manifest_sha256,
        "workspace_sha256": workspace_sha256,
        "changed_path_count": changed_path_count,
        "untracked_path_count": len(untracked_paths),
        "fingerprint_excludes": list(excludes),
    }


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


_CONFIG_SPECS: dict[str, tuple[str, Callable[[str], object]]] = {
    "ENV_MODE": ("cloud", str),
    "RAG_MODE": ("local", str),
    "LLM_MODEL": ("MiniMax-M3", str),
    "EMBEDDING_MODEL": ("text-embedding-v3", str),
    "VECTOR_PG_DIM": ("1024", int),
    "EMBEDDING_BATCH_SIZE": ("10", int),
    "EMBED_BATCH_SIZE": ("32", int),
    "EMBED_REQUEST_LIMIT": ("10", int),
    "CHUNK_SIZE": ("500", int),
    "CHUNK_OVERLAP": ("50", int),
    "LEAF_CHUNK_TOKENS": ("500", int),
    "PARENT_CHUNK_TOKENS": ("2000", int),
    "ENABLE_SEMANTIC_CHUNKING": ("false", _as_bool),
    "VECTOR_PG_POOL_MIN": ("1", int),
    "VECTOR_PG_POOL_MAX": ("10", int),
    "VECTOR_HNSW_EF_SEARCH": ("80", int),
    "RAG_DOC_CANDIDATE_K": ("50", int),
    "BM25_CANDIDATE_K": ("100", int),
    "HYBRID_SEARCH_K": ("8", int),
    "RERANK_MODEL": ("qwen3-rerank", str),
    "RERANK_TOP_K": ("8", int),
    "RERANK_SCORE_THRESHOLD": ("0.3", float),
    "MULTI_QUERY_MODE": ("auto", str),
    "MULTI_QUERY_COUNT": ("3", int),
    "MULTI_QUERY_TOP_K_PER": ("5", int),
    "EVIDENCE_GATE_ENABLED": ("true", _as_bool),
    "CITATION_SUPPORT_THRESHOLD": ("0.4", float),
    "DB_POOL_MIN_CONN": ("2", int),
    "DB_POOL_MAX_CONN": ("10", int),
}

_SECRET_FIELDS = (
    "API_KEY",
    "AUTH_REDIS_PASSWORD",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "EMBEDDING_API_KEY",
    "INTERNAL_API_TOKEN",
    "JWT_SECRET",
    "JWT_SECRET_PREVIOUS",
    "PGPASSWORD",
    "PG_READONLY_PASSWORD",
    "QWEN_API_KEY",
    "QWEN_TP_API_KEY",
    "REDIS_PASSWORD",
)


def collect_configuration(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """读取显式白名单配置；敏感字段永远只记录是否已配置。"""

    source = os.environ if environ is None else environ
    result: dict[str, object] = {}
    for name, (default, caster) in _CONFIG_SPECS.items():
        raw = source.get(name, default)
        try:
            result[name] = caster(raw)
        except (TypeError, ValueError):
            result[name] = {"invalid": True, "raw_type": type(raw).__name__}
    for name in _SECRET_FIELDS:
        result[name] = {"configured": bool(str(source.get(name, "")).strip())}
    return result


def collect_dependencies(repo_root: Path) -> dict[str, object]:
    """记录依赖锁文件的内容哈希，不复制依赖正文。"""

    files = (
        "pyproject.toml",
        "requirements-lock.txt",
        "frontend/package-lock.json",
        "frontend-admin/package-lock.json",
    )
    result: dict[str, object] = {}
    for relative_path in files:
        path = repo_root / relative_path
        result[relative_path] = {
            "status": "ok" if path.is_file() else "missing",
            "sha256": _sha256_file(path) if path.is_file() else None,
        }
    return result


def _version_command(
    runner: CommandRunner,
    repo_root: Path,
    args: tuple[str, ...],
) -> dict[str, object]:
    result = runner(args, repo_root)
    output = result.stdout or result.stderr
    return {
        "status": result.status,
        "version": output.strip().splitlines()[0] if output.strip() else None,
    }


def _first_available_version(
    runner: CommandRunner,
    repo_root: Path,
    candidates: Sequence[tuple[str, ...]],
) -> dict[str, object]:
    last: dict[str, object] = {"status": "unavailable", "version": None}
    for args in candidates:
        last = _version_command(runner, repo_root, args)
        if last["status"] == "ok":
            return last
    return last


def collect_runtime_versions(
    repo_root: Path,
    command_runner: CommandRunner | None = None,
) -> dict[str, object]:
    """采集运行时版本；命令缺失是证据阻断而不是采集器异常。"""

    runner = command_runner or default_command_runner
    postgres_executable = Path("D:/Program Files/PostgreSQL/18/bin/psql.exe")
    psql = str(postgres_executable) if postgres_executable.is_file() else "psql"
    node = shutil.which("node") or "node"
    npm = shutil.which("npm") or "npm"
    redis_server = shutil.which("redis-server") or "redis-server"
    docker = shutil.which("docker") or "docker"
    return {
        "platform": platform.platform(),
        "python": {
            "status": "ok",
            "version": platform.python_version(),
            "executable": str(Path(sys.executable).resolve()),
        },
        "node": _version_command(runner, repo_root, (node, "--version")),
        "npm": _version_command(runner, repo_root, (npm, "--version")),
        "postgresql": _first_available_version(
            runner,
            repo_root,
            (
                ("docker", "compose", "exec", "-T", "postgres", "postgres", "--version"),
                (psql, "--version"),
            ),
        ),
        "redis": _first_available_version(
            runner,
            repo_root,
            (
                ("docker", "compose", "exec", "-T", "redis", "redis-server", "--version"),
                (redis_server, "--version"),
            ),
        ),
        "docker": _version_command(runner, repo_root, (docker, "--version")),
        "docker_compose": _version_command(runner, repo_root, (docker, "compose", "version")),
    }


def _parse_json_records(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        records: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                records.append(item)
        return records
    if isinstance(decoded, list):
        return [item for item in decoded if isinstance(item, dict)]
    return [decoded] if isinstance(decoded, dict) else []


def collect_docker_state(
    repo_root: Path,
    command_runner: CommandRunner | None = None,
) -> tuple[dict[str, object], list[str]]:
    """读取 Compose 镜像清单；没有 RepoDigest 时明确阻断可追溯验收。"""

    runner = command_runner or default_command_runner
    result = runner(("docker", "compose", "images", "--format", "json"), repo_root)
    if result.status != "ok":
        return {"status": "blocked", "images": []}, ["Docker Compose 镜像清单不可用"]

    try:
        rows = _parse_json_records(result.stdout)
    except json.JSONDecodeError:
        return {"status": "blocked", "images": []}, ["Docker Compose 镜像清单不是合法 JSON"]

    blockers: list[str] = []
    images: list[dict[str, object]] = []
    for row in rows:
        service = str(
            row.get("Service")
            or row.get("service")
            or row.get("ContainerName")
            or row.get("container_name")
            or "unknown"
        )
        repository = row.get("Repository") or row.get("repository")
        tag = row.get("Tag") or row.get("tag")
        digest = row.get("Digest") or row.get("RepoDigest") or row.get("repo_digest")
        if digest in (None, "", "<none>") and repository and tag:
            inspect = runner(
                (
                    "docker",
                    "image",
                    "inspect",
                    f"{repository}:{tag}",
                    "--format",
                    "{{json .RepoDigests}}",
                ),
                repo_root,
            )
            if inspect.status == "ok":
                try:
                    repo_digests = json.loads(inspect.stdout)
                except json.JSONDecodeError:
                    repo_digests = []
                if isinstance(repo_digests, list) and repo_digests:
                    digest = sorted(str(item) for item in repo_digests)[0]
        if digest in (None, "", "<none>"):
            digest = None
            blockers.append(f"Docker 镜像 {service} 缺少不可变 RepoDigest")
        images.append(
            {
                "service": service,
                "repository": repository,
                "tag": tag,
                "image_id": row.get("ID") or row.get("ImageID") or row.get("image_id"),
                "repo_digest": digest,
            }
        )
    images.sort(key=lambda item: str(item["service"]))
    return {
        "status": "blocked" if blockers else "ok",
        "images": images,
    }, blockers


def compute_capture_id(manifest: Mapping[str, object]) -> str:
    """对去除采集时间和既有 ID 的规范 JSON 取稳定指纹。"""

    stable = dict(manifest)
    stable.pop("capture_id", None)
    stable.pop("captured_at_utc", None)
    payload = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(payload)[:16]


def collect_baseline(
    repo_root: Path,
    command_runner: CommandRunner | None = None,
    *,
    fingerprint_excludes: Sequence[str] | None = None,
) -> dict[str, object]:
    """采集阶段 0 基线；外部命令失败会变成阻断项而不是静默缺失。"""

    root = repo_root.resolve()
    runner = command_runner or default_command_runner
    git_state = collect_git_state(
        root,
        runner,
        fingerprint_excludes=fingerprint_excludes,
    )
    runtime = collect_runtime_versions(root, runner)
    dependencies = collect_dependencies(root)
    docker_state, docker_blockers = collect_docker_state(root, runner)
    blockers = list(docker_blockers)

    if git_state["commit"] == "unknown":
        blockers.append("Git commit 无法读取")
    for name, info in runtime.items():
        if isinstance(info, dict) and info.get("status") != "ok":
            blockers.append(f"运行时版本不可用: {name}")
    for path, info in dependencies.items():
        if isinstance(info, dict) and info.get("status") != "ok":
            blockers.append(f"依赖清单缺失: {path}")

    manifest: dict[str, object] = {
        "schema_version": "1.0",
        "captured_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git": git_state,
        "runtime": runtime,
        "dependencies": dependencies,
        "docker": docker_state,
        "configuration": collect_configuration(),
        "blocking_items": sorted(set(blockers)),
    }
    manifest["capture_id"] = compute_capture_id(manifest)
    return manifest


_CAPTURE_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")


def write_baseline_manifest(
    manifest: Mapping[str, object],
    output_root: Path,
) -> Path:
    """按 capture_id 写不可变证据；同一环境重复采集保留首次时间。"""

    capture_id = str(manifest.get("capture_id", ""))
    if not _CAPTURE_ID_PATTERN.fullmatch(capture_id):
        raise ValueError("capture_id 必须是 16 位小写十六进制")
    if compute_capture_id(manifest) != capture_id:
        raise ValueError("capture_id 与清单内容不一致")

    output = output_root / capture_id / "baseline-manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        existing = json.loads(output.read_text(encoding="utf-8"))
        if compute_capture_id(existing) != capture_id:
            raise FileExistsError(f"现有证据与 capture_id 不一致: {output}")
        return output

    output.write_text(
        json.dumps(dict(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output
