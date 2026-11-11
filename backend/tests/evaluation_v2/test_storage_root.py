from pathlib import Path

from backend.evaluation import storage


def test_eval_runs_root_uses_git_common_root_for_linked_worktree(monkeypatch, tmp_path):
    worktree = tmp_path / "worktrees" / "evaluation-center"
    common_git_dir = tmp_path / "main-repo" / ".git"
    worktree.mkdir(parents=True)
    monkeypatch.delenv("EVAL_RUNS_DATA_DIR", raising=False)

    def git_common_dir(*_args, **kwargs):
        assert kwargs["cwd"] == worktree
        return str(common_git_dir)

    monkeypatch.setattr(storage.subprocess, "check_output", git_common_dir)

    assert storage._resolve_data_root(worktree) == common_git_dir.parent / "data" / "eval_runs"


def test_eval_runs_root_honors_explicit_directory(monkeypatch, tmp_path):
    configured = tmp_path / "shared" / "eval_runs"
    monkeypatch.setenv("EVAL_RUNS_DATA_DIR", str(configured))
    monkeypatch.setattr(
        storage.subprocess,
        "check_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应再调用 Git")),
    )

    assert storage._resolve_data_root(tmp_path) == configured
