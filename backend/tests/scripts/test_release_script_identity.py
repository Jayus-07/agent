"""release.sh 必须保留流水线注入的镜像短 SHA。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _bash_executable() -> str:
    if os.name != "nt":
        bash = shutil.which("bash")
        if bash:
            return bash
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        / "Git/bin/bash.exe",
        Path("D:/Program Files/Git/bin/bash.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    pytest.skip("需要 Bash 执行 release.sh 行为测试")


def _git_executable() -> str:
    git = shutil.which("git")
    if not git:
        pytest.skip("需要 Git 创建隔离的临时提交")
    return git


def _bash_path(path: str) -> str:
    """把 Windows 可执行文件路径转换为 Git Bash 能读取的路径。"""
    if os.name != "nt":
        return path
    drive, tail = os.path.splitdrive(path)
    if not drive:
        return path.replace("\\", "/")
    return f"/{drive[0].lower()}{tail.replace(chr(92), '/') }"


def _prepare_release_repo(tmp_path: Path) -> tuple[Path, str, str, dict[str, str]]:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "backend/scripts").mkdir(parents=True)
    source_root = Path(__file__).resolve().parents[3]
    shutil.copy2(source_root / "scripts/release.sh", root / "scripts/release.sh")
    (root / "backend/__init__.py").write_text("", encoding="utf-8")
    (root / "backend/scripts/__init__.py").write_text("", encoding="utf-8")
    (root / "backend/scripts/gen_tool_contract_lock.py").write_text(
        "raise SystemExit(23)\n", encoding="utf-8"
    )

    git = _git_executable()
    subprocess.run([git, "init", "--quiet", str(root)], check=True)
    subprocess.run(
        [git, "-C", str(root), "config", "user.email", "release-test@example.invalid"],
        check=True,
    )
    subprocess.run(
        [git, "-C", str(root), "config", "user.name", "Release Script Test"],
        check=True,
    )
    subprocess.run([git, "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [git, "-C", str(root), "commit", "--quiet", "-m", "fixture"],
        check=True,
    )
    full_sha = subprocess.check_output(
        [git, "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "rev-parse" && "$2" == "--short" && "$3" == "HEAD" ]]; then\n'
        '  printf "%s\\n" "$FAKE_SHORT_SHA"\n'
        "else\n"
        '  "$REAL_GIT" "$@"\n'
        "fi\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        fake_git.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["REAL_GIT"] = _bash_path(git)
    env["FAKE_SHORT_SHA"] = full_sha[:8]
    env["PYTHONPATH"] = str(root)
    return root, full_sha, _bash_executable(), env


def _run_release(root: Path, bash: str, env: dict[str, str], injected_sha: str):
    env = {**env, "GIT_COMMIT": injected_sha}
    return subprocess.run(
        [bash, str(root / "scripts/release.sh"), "--skip-build"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def test_release_script_preserves_injected_commit_prefix(tmp_path: Path):
    root, full_sha, bash, env = _prepare_release_repo(tmp_path)

    result = _run_release(root, bash, env, full_sha[:7])

    assert result.returncode != 0
    assert f"commit={full_sha[:7]} " in result.stdout
    assert "commit=" + env["FAKE_SHORT_SHA"] + " " not in result.stdout
    assert "== 3. 滚动 up" not in result.stdout


def test_release_script_rejects_injected_commit_from_another_head(tmp_path: Path):
    root, full_sha, bash, env = _prepare_release_repo(tmp_path)
    other_sha = "0" * 7 if not full_sha.startswith("0") else "1" * 7

    result = _run_release(root, bash, env, other_sha)

    assert result.returncode != 0
    assert "注入的 GIT_COMMIT 与当前工作树 HEAD 不一致" in result.stdout
    assert "[release] commit=" not in result.stdout
