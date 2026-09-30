"""gen_prompt_contract_lock.py — Prompt 契约快照（lock）生成与漂移检测

为什么存在（治理验收 #3）：Prompt 版本权威在 PG（prompt_versions +
active_version），仓库内没有对照物——「发布后忘了带任何可审计的仓库
痕迹」「DB 被直改（seed 之外的手工 DML）」都无人发现。

与 Tool lock 的关键差异：Tool 的事实源是代码（lock=代码派生快照），
Prompt 的事实源是 DB——本 lock 是 **DB 发布状态在仓库的镜像**：
- 生成：从 PG 读每 prompt 的 active_version + 模板内容哈希 + change_kind
- 纪律：**发布/回滚/改 DB 后必须重新生成并随变更提交**（与 Tool lock
  同款原子提交约定）；lock 与 DB 漂移 = 有发布没留痕 = 被拦
- CI：无 DB 环境只做结构校验；强校验（--check）在有 DB 的环境跑
  （release preflight / 本地守卫测试 test_prompt_contract_lock）

用法（仓库根）::

    python -m backend.scripts.gen_prompt_contract_lock            # 从 DB 生成
    python -m backend.scripts.gen_prompt_contract_lock --check     # DB vs 仓库 lock
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "backend" / "prompts.lock.json"


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _fetch_db_state() -> dict[str, dict] | None:
    """读 PG：每 prompt 的 active_version + 模板哈希 + change_kind。

    DB 不可达返回 None（CI/离线环境合法降级，由调用方决定语义）。
    """
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT p.key,
                       p.active_version,
                       v.template,
                       v.change_kind
                FROM prompts p
                LEFT JOIN prompt_versions v
                       ON v.prompt_id = p.id AND v.version = p.active_version
                ORDER BY p.key
                """
            )
            rows = cur.fetchall()
        return {
            key: {
                "active_version": active or 0,
                "template_hash": hashlib.sha256(
                    (template or "").encode("utf-8")).hexdigest()[:16],
                "change_kind": change_kind,
            }
            for key, active, template, change_kind in rows
        }
    except Exception:
        return None


def derive_snapshot() -> dict | None:
    db = _fetch_db_state()
    if db is None:
        return None
    return {
        "version": 1,
        "generated_at": __import__("datetime").datetime.now().astimezone()
        .isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "source": "postgres://agent_memory.prompts",
        "prompt_count": len(db),
        "prompts": db,
    }


def _load_lock_file() -> dict | None:
    if not LOCK_PATH.exists():
        return None
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def check(existing: dict, current: dict) -> tuple[str, list[str]]:
    """比对仓库 lock 与 DB 当前态，返回 (结论, 漂移明细)。"""
    old, new = existing.get("prompts", {}), current.get("prompts", {})
    issues: list[str] = []
    for key in sorted(set(old) - set(new)):
        issues.append(f"REMOVED prompt: {key}")
    for key in sorted(set(new) - set(old)):
        issues.append(f"ADDED prompt: {key} (active_version={new[key]['active_version']})")
    for key in sorted(set(old) & set(new)):
        o, n = old[key], new[key]
        if o.get("active_version") != n["active_version"]:
            issues.append(
                f"{key}: active_version {o.get('active_version')} -> "
                f"{n['active_version']}（发布/回滚未随 lock 提交？）")
        elif o.get("template_hash") != n["template_hash"]:
            issues.append(f"{key}: 同版本模板内容变化（版本表被手工 DML？）")
    return ("IN_SYNC" if not issues else "DRIFTED"), issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="DB vs 仓库 lock 漂移检测")
    args = parser.parse_args(argv)

    snapshot = derive_snapshot()
    if snapshot is None:
        print("[prompt-contract-lock] DB 不可达：生成/强校验需要 agent_memory PG"
              "（CI 环境只做结构校验）", file=sys.stderr)
        return 2 if args.check else 1

    if not args.check:
        LOCK_PATH.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"[prompt-contract-lock] 已写入 {LOCK_PATH}"
              f"（{snapshot['prompt_count']} 个 prompt，git_sha={snapshot['git_sha']}）")
        return 0

    existing = _load_lock_file()
    if existing is None:
        print("[prompt-contract-lock] FAIL：lock 文件不存在，先运行生成", file=sys.stderr)
        return 1
    verdict, issues = check(existing, snapshot)
    print(f"[prompt-contract-lock] {verdict}（DB {snapshot['prompt_count']} vs "
          f"lock {existing.get('prompt_count')}）")
    for i in issues:
        print(f"  - {i}")
    return 0 if verdict == "IN_SYNC" else 1


if __name__ == "__main__":
    sys.exit(main())
