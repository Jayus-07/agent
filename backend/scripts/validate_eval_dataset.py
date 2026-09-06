"""评测集 Ground Truth 全量校验脚本。

背景：2026-08-19 发现 12 条用例的 expected doc_id/snippet 与 KB 实际内容不符
（commit 7f32825 只修了 5 条）。2026-09-07 又发现 5 条用例因 doc_id 协议漂移
（旧 md5(basename)[:10] → 命名空间 md5(kb|dept|basename)[:10]）指向已不存在的
doc_id，评测报"检索失败"但实际检索是对的。本脚本对评测集做全量审计，防止错标
再次溜进基线。

校验项：
  1. 结构：id 唯一、question 无逐字重复、必填字段齐全
  2. doc_id 真实性：relevant_docs 必须是 doc_registry 中该 KB 下的 active 记录
     [ERROR]。这是判分的真实依据 —— 检索返回的 doc_id 来自索引，而非按文件名
     重算的哈希；两者不一致时评测会产生"检索命中但 ID 对不上"的假失败。
     doc_registry 不可用时降级为文件系统校验（两种协议都试）并记 [WARN]。
  3. snippet 真实性：
     - match_type=snippet 用例（snippet 是判据）：关键词必须存在于该 KB 任意
       文档 [ERROR]
     - doc-bound 用例（snippet 仅展示，doc_id 才是判据）：关键词未出现在期望
       文档记 [WARN]（无协议风险，但提示标注与原文措辞不一致）
  4. 负样本一致性：should_reject=True 的用例不得标注 relevant_docs/relevant_snippets [ERROR]

用法：
    python backend/scripts/validate_eval_dataset.py
    python backend/scripts/validate_eval_dataset.py --dataset rag_test_kb.json
    python backend/scripts/validate_eval_dataset.py --docs-root data/docs

退出码：0 = 全部通过；1 = 存在 ERROR（WARN 不阻断）。
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = PROJECT_ROOT / "backend" / "evaluation" / "datasets"
DEFAULT_DOCS_ROOT = PROJECT_ROOT / "data" / "docs"
DEFAULT_REGISTRY = PROJECT_ROOT / "data" / "doc_registry.db"


def normalize(text: str) -> str:
    """与 runner _match_by_snippet 一致的归一化：全角转半角 + 去全部空白。"""
    text = text.translate(
        {i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)}
    ).replace("\u3000", " ")
    return "".join(text.split())


def legacy_doc_id(filename: str) -> str:
    """旧 doc_id 协议：md5(basename)[:10]（仅用于 registry 不可用时降级校验）。"""
    return hashlib.md5(filename.encode("utf-8")).hexdigest()[:10]


def namespaced_doc_id(kb_id: str, department: str, basename: str) -> str:
    """命名空间 doc_id 协议：md5(kb|dept|basename)[:10]（降级校验用）。"""
    return hashlib.md5(f"{kb_id}|{department}|{basename}".encode("utf-8")).hexdigest()[:10]


def load_registry(registry_path: Path) -> dict[str, dict] | None:
    """读取 doc_registry 的 active 记录 → {doc_id: {kb_id, department, file_name, file_path}}。

    registry 不存在或不可读时返回 None，调用方降级为文件系统校验。
    """
    if not registry_path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{registry_path}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "select doc_id, kb_id, department, file_name, file_path "
                "from doc_registry where status='active'"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        print(f"  [WARN] doc_registry 读取失败，降级为文件系统校验: {e}")
        return None
    return {
        doc_id: {
            "kb_id": kb_id or "",
            "department": department or "",
            "file_name": file_name or "",
            "file_path": file_path or "",
        }
        for doc_id, kb_id, department, file_name, file_path in rows
    }


def extract_text(path: Path) -> str | None:
    """按扩展名解析文档文本；解析失败返回 None（记 WARN 而非 ERROR）。"""
    try:
        if path.suffix.lower() == ".md":
            return path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".docx":
            import docx
            doc = docx.Document(str(path))
            parts = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                parts.extend(
                    cell.text for row in table.rows for cell in row.cells
                )
            return "\n".join(parts)
        if path.suffix.lower() == ".pdf":
            import pymupdf as fitz
            with fitz.open(str(path)) as pdf:
                return "\n".join(page.get_text() for page in pdf)
    except Exception as e:  # noqa: BLE001
        print(f"    [WARN] 解析失败 {path.name}: {e}")
    return None


def scan_kb_files(docs_root: Path) -> dict[str, dict[str, Path]]:
    """扫描 data/docs/{kb_id}/{department}/** → {kb_id: {basename: Path}}。"""
    out: dict[str, dict[str, Path]] = {}
    if not docs_root.is_dir():
        return out
    for kb_dir in sorted(p for p in docs_root.iterdir() if p.is_dir()):
        files: dict[str, Path] = {}
        for p in kb_dir.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                files[p.name] = p
        out[kb_dir.name] = files
    return out


def resolve_case_kb(case: dict) -> str:
    """用例的 kb_id：顶层优先，其次 metadata.kb_id（DEPT-* 隔离用例放 metadata）。"""
    return str(case.get("kb_id") or case.get("metadata", {}).get("kb_id") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="评测集 ground truth 全量校验")
    parser.add_argument("--dataset", default="rag_test_kb.json",
                        help="评测集文件名（相对 datasets/ 目录）")
    parser.add_argument("--docs-root", default=str(DEFAULT_DOCS_ROOT),
                        help="KB 源文档根目录（data/docs，其下每个子目录是一个 kb_id）")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY),
                        help="doc_registry.db 路径（doc_id 真实性校验的权威来源）")
    args = parser.parse_args()

    dataset_path = DATASET_DIR / args.dataset
    docs_root = Path(args.docs_root)
    if not dataset_path.exists():
        print(f"[ERROR] 评测集不存在: {dataset_path}")
        return 1
    if not docs_root.is_dir():
        print(f"[ERROR] KB 源文档根目录不存在: {docs_root}")
        return 1

    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["test_cases"]

    registry = load_registry(Path(args.registry))
    kb_files = scan_kb_files(docs_root)
    total_files = sum(len(v) for v in kb_files.values())
    mode = "doc_registry" if registry is not None else "filesystem(降级)"
    print(f"评测集: {dataset_path.name} ({len(cases)} 条) | "
          f"KB: {len(kb_files)} 个 / {total_files} 篇文档 | "
          f"doc_id 校验源: {mode} | version={data.get('version', '?')}")

    errors: list[str] = []
    warns: list[str] = []
    seen_ids: set[str] = set()
    seen_questions: dict[str, str] = {}
    text_cache: dict[str, str | None] = {}

    def doc_text(doc_id: str, kb_id: str) -> tuple[Path | None, str | None]:
        """按 doc_id 取期望文档的源文件路径与文本（registry 优先，降级按文件名哈希）。"""
        path: Path | None = None
        if registry is not None:
            entry = registry.get(doc_id)
            if entry and entry["file_path"]:
                cand = Path(entry["file_path"])
                if cand.exists():
                    path = cand
        if path is None:
            # 降级：在该 KB 目录里按两种协议反查文件名
            for name, p in kb_files.get(kb_id, {}).items():
                if doc_id in (legacy_doc_id(name),
                              namespaced_doc_id(kb_id, "", name)):
                    path = p
                    break
        if path is None:
            return None, None
        key = str(path)
        if key not in text_cache:
            text_cache[key] = extract_text(path)
        return path, text_cache[key]

    for case in cases:
        cid = case.get("id", "<no-id>")
        question = case.get("question", "")
        expected = case.get("expected", {})
        relevant_docs = expected.get("relevant_docs") or []
        snippets = expected.get("relevant_snippets") or []
        should_reject = expected.get("should_reject", False)
        kb_id = resolve_case_kb(case)

        # 1. 结构校验
        if cid in seen_ids:
            errors.append(f"{cid}: id 重复")
        seen_ids.add(cid)
        nq = normalize(question)
        if nq in seen_questions:
            errors.append(f"{cid}: question 与 {seen_questions[nq]} 逐字重复: {question}")
        seen_questions[nq] = cid
        if not kb_id:
            errors.append(f"{cid}: 缺少 kb_id 字段（顶层或 metadata.kb_id）")
        difficulty = case.get("metadata", {}).get("difficulty")
        if difficulty not in ("easy", "medium", "hard"):
            warns.append(f"{cid}: difficulty 非标准值: {difficulty}")

        # 4. 负样本一致性
        if should_reject:
            if relevant_docs or snippets:
                errors.append(f"{cid}: should_reject=True 但标注了 relevant_docs/snippets")
            continue

        # snippet-only 用例（match_type=snippet，不绑定 doc_id）：
        # snippet 必须存在于该 KB 任意文档中
        match_type = expected.get("match_type")
        if match_type == "snippet":
            if not snippets:
                errors.append(f"{cid}: match_type=snippet 但未标注 relevant_snippets")
                continue
            merged_all = ""
            for p in kb_files.get(kb_id, {}).values():
                key = str(p)
                if key not in text_cache:
                    text_cache[key] = extract_text(p)
                if text_cache[key] is not None:
                    merged_all += "\n" + text_cache[key]
            if not merged_all:
                warns.append(f"{cid}: KB '{kb_id}' 文档均无法解析，跳过 snippet 校验")
                continue
            normalized_all = normalize(merged_all)
            for s in snippets:
                if normalize(s) not in normalized_all:
                    errors.append(
                        f"{cid}: snippet '{s}' 未出现在 KB '{kb_id}' 任何文档中（ground truth 错标）"
                    )
            continue

        # 2. doc_id 真实性校验
        # min_relevant_chunks == 0 → 负向/隔离用例（如跨部门泄漏守卫 DEPT-003/005）：
        # runner 判定为"交集为空才 pass"，故 relevant_docs 必须为空。
        if case.get("expected", {}).get("min_relevant_chunks", 1) == 0:
            if relevant_docs:
                errors.append(
                    f"{cid}: min_relevant_chunks=0（负向/隔离用例）但标注了 "
                    f"relevant_docs={relevant_docs}，二者矛盾"
                )
            continue
        if not relevant_docs:
            errors.append(f"{cid}: 正样本缺少 relevant_docs（且未标 should_reject）")
            continue

        resolved_paths: list[Path] = []
        for did in relevant_docs:
            if registry is not None:
                entry = registry.get(did)
                if entry is None:
                    known = sorted(
                        d for d, e in registry.items() if e["kb_id"] == kb_id
                    )
                    errors.append(
                        f"{cid}: doc_id '{did}' 不在 doc_registry 的 active 记录中"
                        f"（协议漂移或文件未入库 → 评测会误判为检索失败）。"
                        f"KB '{kb_id}' 当前 active doc_id: {known}"
                    )
                    continue
                if kb_id and entry["kb_id"] != kb_id:
                    errors.append(
                        f"{cid}: doc_id '{did}' 属于 KB '{entry['kb_id']}'，"
                        f"与用例标注的 KB '{kb_id}' 不一致"
                    )
            else:
                names = kb_files.get(kb_id, {})
                if not any(
                    did in (legacy_doc_id(n), namespaced_doc_id(kb_id, "", n))
                    for n in names
                ):
                    errors.append(
                        f"{cid}: doc_id '{did}' 在 KB '{kb_id}' 目录中无对应文件"
                        f"（已试 md5(basename) 与 md5(kb|dept|basename) 两种协议）"
                    )
                    continue
            path, _ = doc_text(did, kb_id)
            if path is not None:
                resolved_paths.append(path)

        # 3. snippet 真实性校验（期望文档文本并集）
        if snippets and resolved_paths:
            merged = ""
            for p in resolved_paths:
                key = str(p)
                if key not in text_cache:
                    text_cache[key] = extract_text(p)
                if text_cache[key] is not None:
                    merged += "\n" + text_cache[key]
            if not merged:
                warns.append(f"{cid}: 期望文档均无法解析，跳过 snippet 校验")
                continue
            normalized_merged = normalize(merged)
            for s in snippets:
                if normalize(s) not in normalized_merged:
                    # doc-bound 用例中 snippet 仅展示（判据是 doc_id），降级为 WARN；
                    # 提示标注关键词与文档原文措辞不一致，建议向原文对齐
                    warns.append(
                        f"{cid}: snippet '{s}' 未出现在期望文档 "
                        f"{[p.name for p in resolved_paths]} 中（展示字段，不影响判分，"
                        f"建议改为文档原文措辞）"
                    )

    # 汇总
    for w in warns:
        print(f"  [WARN] {w}")
    for e in errors:
        print(f"  [ERROR] {e}")
    if errors:
        print(f"\n[FAIL] 校验失败：{len(errors)} 个错误，{len(warns)} 个警告")
        return 1
    print(f"\n[OK] 校验通过：{len(cases)} 条用例全部合规（{len(warns)} 个警告）")
    return 0


if __name__ == "__main__":
    # Windows GBK 终端下避免中文/emoji 编码崩溃
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
