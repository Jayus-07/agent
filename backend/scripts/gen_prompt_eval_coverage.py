"""gen_prompt_eval_coverage.py — Prompt 评测覆盖矩阵（派生，禁手抄，G2）

为什么存在（提示词治理批次 D，2026-10-06）：43+ prompt 里只有少数真正配过
评测/发布，「哪个 prompt 有回归集、哪个裸奔」此前无台账。本脚本从三类
事实源**派生**覆盖矩阵，人工不维护数字：

  事实源 1  PROMPT_REGISTRY（key/category/risk/default_file）
  事实源 2  evaluation/datasets/** 文本扫描（见证据规则）
  事实源 3  DB 版本演进（active_version>1 或 change_kind 非空 = 迭代过）；
            DB 不可达时合法降级并在报告注明

证据规则（covered / partial / none）：
  covered  数据集文本命中 prompt key 字面 或 default_file 文件名词干
  partial  数据集 module/category 字段或文件/目录名命中 key 的 category
  none     两类证据皆无

输出：docs/reports/<日期>-提示词评测覆盖矩阵.md（生成物，禁手改）。
report-only：刻意不做 CI 门禁——先补集，再升级门禁（验收清单 I11）。

用法（仓库根）::

    python -m backend.scripts.gen_prompt_eval_coverage            # 生成并打印摘要
    python -m backend.scripts.gen_prompt_eval_coverage --out PATH  # 自定义输出
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.prompts.registry import PROMPT_REGISTRY  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "docs" / "reports" / "2026-10-06-提示词评测覆盖矩阵.md"
DATASETS_DIR = REPO_ROOT / "backend" / "evaluation" / "datasets"
_SCAN_SUFFIXES = {".json", ".jsonl", ".yaml", ".yml", ".md"}
_MAX_FILE_BYTES = 2_000_000  # 单文件扫描上限，防超大数据集拖慢

# category → 数据集目录/文件名惯用别名（路径级关联 → partial 证据；
# 仅用于路径语料，不做全文匹配防误命中）。派生自 evaluation/datasets 实际命名。
_CATEGORY_PATH_ALIASES: dict[str, tuple[str, ...]] = {
    "customer_service": ("cs", "customer_service"),
    "planner": ("planner",),
    "rag": ("rag",),
    "sql": ("sql",),
    "travel": ("travel",),
    "router": ("router", "routing"),
    "memory": ("memory",),
    "selection": ("selection", "market_research"),
    "general_chat": ("general_chat",),
    "context": ("context", "follow_up", "continuation"),
}

# key 尾段太通用（system/qa 等）不做路径推断
_TAIL_STOPLIST = {"system", "qa", "llm", "user", "summary"}


def _dataset_texts() -> dict[str, str]:
    """{相对路径: 全文小写}——文本证据扫描底料。"""
    texts: dict[str, str] = {}
    if not DATASETS_DIR.is_dir():
        return texts
    for path in sorted(DATASETS_DIR.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SCAN_SUFFIXES:
            continue
        if path.stat().st_size > _MAX_FILE_BYTES:
            continue
        try:
            texts[str(path.relative_to(DATASETS_DIR))] = path.read_text(
                encoding="utf-8", errors="ignore",
            ).lower()
        except OSError:
            continue
    return texts


def _dataset_path_corpus() -> str:
    """文件/目录相对路径拼接（目录级关联 → partial 证据）。"""
    if not DATASETS_DIR.is_dir():
        return ""
    return " ".join(
        str(p.relative_to(DATASETS_DIR)).lower()
        for p in sorted(DATASETS_DIR.rglob("*"))
    )


def _evidence_for(spec, texts: dict[str, str], path_corpus: str) -> tuple[str, list[str]]:
    """返回 (状态, 命中证据列表)。规则见模块 docstring。"""
    key_hits: list[str] = []
    for rel, text in texts.items():
        if spec.key.lower() in text:
            key_hits.append(rel)
    stem = Path(spec.default_file).stem.lower() if spec.default_file else ""
    if stem and not key_hits:
        for rel, text in texts.items():
            if stem in text:
                key_hits.append(rel)
    if key_hits:
        return "covered", sorted(set(key_hits))[:3]

    partial_hits: list[str] = []
    cat = spec.category.lower()
    aliases = _CATEGORY_PATH_ALIASES.get(cat, (cat,)) if cat else ()
    if any(a in path_corpus for a in aliases if a):
        partial_hits.append(f"category:{cat}")
    for rel, text in texts.items():
        head = text[:4000]  # 只看头部 module/description 字段区
        if f'"module": "{cat}"' in head or f"'module': '{cat}'" in head:
            partial_hits.append(f"module:{rel}")
    tail = spec.key.rsplit(".", 1)[-1].lower()
    if (
        len(tail) >= 5 and tail not in _TAIL_STOPLIST
        and (tail in path_corpus or any(tail in t for t in texts.values()))
    ):
        partial_hits.append(f"key_tail:{tail}")
    if partial_hits:
        return "partial", partial_hits[:3]
    return "none", []


def _db_evidence() -> tuple[dict[str, str], str]:
    """{key: 版本演进证据}；DB 不可达返回 ({}, 原因)。"""
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT p.key,
                       COALESCE(MAX(v.version), 0) AS max_version,
                       MAX(p.active_version)       AS active_version,
                       BOOL_OR(v.change_kind IS NOT NULL) AS has_change_kind
                FROM prompts p
                LEFT JOIN prompt_versions v ON v.prompt_id = p.id
                GROUP BY p.key
                """
            )
            rows = cur.fetchall()
        out = {}
        for key, max_version, active, has_ck in rows:
            if (max_version or 0) > 1 or has_ck:
                out[key] = f"v{active or '?'}/max_v{max_version} 已迭代"
            else:
                out[key] = f"v{active or 0} 未迭代"
        return out, ""
    except Exception as exc:  # noqa: BLE001 — DB 可选降级
        return {}, str(exc)[:120]


def generate(out_path: Path) -> str:
    texts = _dataset_texts()
    path_corpus = _dataset_path_corpus()
    db_ev, db_err = _db_evidence()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows: list[dict] = []
    for key in sorted(PROMPT_REGISTRY):
        spec = PROMPT_REGISTRY[key]
        state, hits = _evidence_for(spec, texts, path_corpus)
        rows.append({
            "key": key, "category": spec.category, "risk": spec.risk_level,
            "default_file": spec.default_file or "（代码内联/code_controlled）",
            "state": state, "hits": "；".join(hits) or "—",
            "db": db_ev.get(key, "—" if db_err else "未入 DB"),
        })

    order = {"covered": 0, "partial": 1, "none": 2}
    covered = sum(1 for r in rows if r["state"] == "covered")
    partial = sum(1 for r in rows if r["state"] == "partial")
    none = sum(1 for r in rows if r["state"] == "none")
    high_gap = [
        r for r in rows
        if r["risk"] in ("high", "critical") and r["state"] != "covered"
    ]

    lines = [
        "# 提示词评测覆盖矩阵",
        "",
        f"> **生成物，禁手改**（G2：一切数字由脚本派生）。生成时间：{now}",
        "> 生成命令：`python -m backend.scripts.gen_prompt_eval_coverage`",
        "> 证据规则：covered=数据集文本命中 key 或 default_file 词干；"
        "partial=category/module 级关联；none=无证据。"
        "数据集扫描范围：`backend/evaluation/datasets/**`。",
        "",
        f"## 汇总（共 {len(rows)} 键）",
        "",
        f"- covered：{covered}｜partial：{partial}｜none：{none}",
        f"- **high/critical 无直接证据缺口（{len(high_gap)} 个）**：",
        "",
    ]
    lines += [f"  - `{r['key']}`（{r['risk']}，{r['default_file']}）" for r in high_gap]
    lines += [
        "",
        "## 全量矩阵",
        "",
        "| key | category | risk | default_file | 覆盖 | 证据 | 版本演进 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (order[x["state"]], x["key"])):
        lines.append(
            f"| `{r['key']}` | {r['category']} | {r['risk']} "
            f"| {r['default_file']} | {r['state']} | {r['hits']} | {r['db']} |"
        )
    if db_err:
        lines += [
            "",
            f"> ⚠️ DB 证据不可用（{db_err}）——「版本演进」列以 — 代替，"
            "DB 可达后重跑即可补全。",
        ]
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return (
        f"[coverage] 共 {len(rows)} 键：covered={covered} partial={partial} "
        f"none={none}；high/critical 缺口={len(high_gap)}；输出={out_path}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Prompt 评测覆盖矩阵报告")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 md 路径")
    args = parser.parse_args()
    print(generate(Path(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
