"""文档 ID 派生 — 命名空间化，消除跨知识库/跨部门/跨子目录同名文件碰撞。

背景:
  旧协议 doc_id = md5(basename)[:10] 只跟文件名走，不同 (kb_id, department)
  下的同名文件会共享同一 doc_id（indexer.py 注释: "当前部署可接受"）。
  共享 doc_id 会导致:
    - doc_registry.get_by_doc_id 返回单行，跨部门同文件互相覆盖；
    - 评测集 relevant_docs 按 doc_id 引用时出现歧义。

修复（dev 预留方向: "如需严格隔离可再叠加 kb 前缀派生"）:
  将 kb_id + department 一并纳入派生，得到确定性、无歧义的 doc_id:
    doc_id = md5(f"{kb_id}|{department}|{basename}")[:10]

  同一 (kb_id, department) 内同名文件 = 同一物理文件 = 同一 doc（正确，无伪碰撞）。
  不同 (kb_id, department) 同名文件 → 不同 doc_id（严格隔离）。

子目录扩展（2026-09-02，F5 前置修复）:
  {kb}/{dept}/ 下的子目录内同名文件（如 rag_test_kb/general/README.md 与
  rag_test_kb/general/写作规范反例/README.md）在旧协议下仍会碰撞——
  reindex 每次重新派生都会撞回同一 doc_id，产生重复 active 行 + 向量混淆。
  现协议纳入部门目录以下的相对子路径:
    有子目录: doc_id = md5(f"{kb_id}|{department}|{subpath}|{basename}")[:10]
    无子目录: doc_id = md5(f"{kb_id}|{department}|{basename}")[:10]  ← 与旧协议完全一致（向后兼容）

约定存储路径: {docs_dir}/{kb_id}/{department}/[{subpath}/]{filename}
两处调用方必须一致:
  - indexer._derive_doc_id  → derive_doc_id(kb_id=, department=, basename=, subpath=)
  - loader/metadata         → derive_doc_id_from_path(file_path, docs_dir)
  （注: loader.py 目前仍使用旧 md5(basename) 协议注入 chunk metadata，
   属已知的"doc_id 协议分裂"问题，BM25 侧已用 file_path 双键兜底，
   统一迁移归 F6 批次处理。）
"""

import hashlib
import os


def derive_doc_id(*, kb_id: str, department: str, basename: str, subpath: str = "") -> str:
    """确定性 doc_id：命名空间化 (kb_id, department, [subpath], basename)。

    subpath 为部门目录以下的相对子目录（"/" 分隔、无首尾斜杠），
    缺省（空）时与历史协议 md5(f"{kb}|{dept}|{basename}") 完全一致，
    保证存量平铺文件的 doc_id 不变。
    """
    kb_id = kb_id or "default"
    department = department or "general"
    subpath = (subpath or "").replace("\\", "/").strip("/")
    if subpath:
        raw = f"{kb_id}|{department}|{subpath}|{basename}"
    else:
        raw = f"{kb_id}|{department}|{basename}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def parse_kb_dept_subpath_from_path(
    file_path: str, docs_dir: str | None = None
) -> tuple[str, str, str]:
    """从存储路径推导 (kb_id, department, subpath)。

    约定: {docs_dir}/{kb_id}/{department}/[{subpath}/]{filename}
      - 不足两级 → 回退 ('default', 'general', '')
      - 路径不在 docs_dir 下（relpath 以 .. 开头）→ 视为解析失败回退
      - 解析失败 → 回退（绝不抛异常，避免阻断索引）
    """
    try:
        if docs_dir:
            rel = os.path.relpath(file_path, docs_dir)
        else:
            rel = file_path
        if rel.startswith(".."):
            return "default", "general", ""
        parts = rel.replace("\\", "/").split("/")
        if len(parts) >= 4:
            return parts[0], parts[1], "/".join(parts[2:-1])
        if len(parts) == 3:
            return parts[0], parts[1], ""
        if len(parts) == 2:
            return parts[0], "general", ""
        return "default", "general", ""
    except Exception:
        return "default", "general", ""


def parse_kb_dept_from_path(file_path: str, docs_dir: str | None = None) -> tuple[str, str]:
    """从存储路径推导 (kb_id, department)。子目录信息见 parse_kb_dept_subpath_from_path。"""
    kb_id, department, _ = parse_kb_dept_subpath_from_path(file_path, docs_dir)
    return kb_id, department


def derive_doc_id_from_path(file_path: str, docs_dir: str | None = None) -> str:
    """从完整文件路径派生 doc_id（loader 路径用：按 docs_dir 解析 kb/dept/subpath）。"""
    kb_id, department, subpath = parse_kb_dept_subpath_from_path(file_path, docs_dir)
    basename = os.path.basename(file_path)
    return derive_doc_id(
        kb_id=kb_id, department=department, basename=basename, subpath=subpath
    )
