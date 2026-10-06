"""生成/刷新数据集内容指纹（C6-1/DATA-01、C9-1/P0-02）。

对每个数据集目录：
1. manifest.json 写入 ``content_hash`` = sha256(cases.jsonl)[:16] —— loader
   加载 canonical 时校验，原地修改即 fail-fast（不可变锁的技术强制）。
2. suites/*.json 写入 ``cases_hash`` = 同口径 canonical 指纹 —— suite 声明
   它构建时基于哪份 canonical 内容，canonical 漂移后旧 suite 拒绝静默加载。

用法::

    python -m backend.evaluation.datasets.gen_dataset_manifest [module ...]

不传 module 时处理全部含 manifest.json + cases.jsonl 的数据集目录。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

DATASETS_ROOT = Path(__file__).resolve().parent


def file_content_hash(path: Path) -> str:
    """行尾归一化内容指纹——委托 loader 唯一实现（P0-03，禁第二份口径）。

    历史上这里用裸字节 hash：Windows 生成 CRLF 口径、CI 读 LF，指纹永不
    相等（每日 RAG 回归连续 5 天误报的帮凶之一）。
    """
    from backend.evaluation.dataset.loader import file_content_hash as _normalized

    return _normalized(path)


def refresh_dataset(split_dir: Path) -> bool:
    """刷新单个数据集目录的指纹；返回是否有改动。"""
    manifest_path = split_dir / "manifest.json"
    cases_path = split_dir / "cases.jsonl"
    if not manifest_path.exists() or not cases_path.exists():
        return False
    content_hash = file_content_hash(cases_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed = False
    if manifest.get("content_hash") != content_hash:
        if manifest.get("content_hash"):
            # 已声明的 hash 与实际不符：先修 hash 等于追认了原地修改，
            # 必须显式确认（--force 语义这里不提供，走新版本流程）。
            raise SystemExit(
                f"[{split_dir.name}] cases.jsonl 内容与 manifest.content_hash "
                f"不一致（{content_hash} != {manifest.get('content_hash')}）。"
                f"如属正常演进，请走新版本目录；如确认要重置指纹，手工更新 manifest。"
            )
        manifest["content_hash"] = content_hash
        changed = True

    suites_dir = split_dir / "suites"
    if suites_dir.is_dir():
        for suite_path in sorted(suites_dir.glob("*.json")):
            suite = json.loads(suite_path.read_text(encoding="utf-8"))
            if not isinstance(suite, dict):
                continue
            if suite.get("cases_hash") != content_hash:
                suite["cases_hash"] = content_hash
                suite_path.write_text(
                    json.dumps(suite, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(f"[suite] {suite_path.name} cases_hash → {content_hash}")
                changed = True

    if changed:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[manifest] {split_dir.name} content_hash → {content_hash}")
    return changed


def main(argv: list[str]) -> int:
    modules = argv[1:]
    targets = (
        [DATASETS_ROOT / m for m in modules]
        if modules
        else sorted(p for p in DATASETS_ROOT.iterdir() if p.is_dir())
    )
    touched = 0
    for split_dir in targets:
        try:
            touched += bool(refresh_dataset(split_dir))
        except SystemExit:
            raise
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"[skip] {split_dir.name}: {exc}")
    print(f"[done] 更新 {touched} 个数据集指纹")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
