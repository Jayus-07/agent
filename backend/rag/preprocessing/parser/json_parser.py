"""JSON 解析器：按对象、数组和字段路径建立可追溯结构。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser

MAX_JSON_NODES = 50000


def _record_label(value: dict[str, Any]) -> str:
    """仅使用原文中的 ID/名称作为记录标题，不生成业务标签。"""
    for key in ("id", "_id", "name", "title"):
        item = value.get(key)
        if isinstance(item, (str, int, float)) and str(item).strip():
            return str(item).strip()[:120]
    return ""


class JsonParser(BaseDocumentParser):
    """将对象路径和记录 ID 写入 AST 定位信息。"""

    PARSER_VERSION = "v2"

    def parse(self, file_path: str) -> DocumentAST:
        raw_bytes = Path(file_path).read_bytes()
        try:
            source = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            source = raw_bytes.decode("utf-8", errors="strict")
        data = json.loads(source)
        root = DocumentNode(type="section", text="", level=0)
        lines: list[str] = []
        node_count = 0
        truncated = False

        def append_leaf(parent: DocumentNode, path: str, value: Any) -> None:
            nonlocal node_count, truncated
            if node_count >= MAX_JSON_NODES:
                truncated = True
                return
            node_count += 1
            text = f"{path}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
            node = DocumentNode(type="paragraph", text=text)
            node.source_locator = {"json_path": path}
            parent.children.append(node)
            lines.append(text)

        def visit(
            value: Any,
            path: str,
            parent: DocumentNode,
            heading: str = "",
            depth: int = 0,
            force_section: bool = False,
            heading_source: str = "",
        ) -> None:
            nonlocal node_count, truncated
            if truncated:
                return
            if node_count >= MAX_JSON_NODES or depth > 128:
                truncated = True
                return
            node_count += 1
            if isinstance(value, dict):
                section = parent
                if heading or force_section:
                    section = DocumentNode(type="section", text=heading, level=depth)
                    section.source_locator = {
                        "json_path": path,
                        "record_id": _record_label(value),
                        "heading_source": heading_source,
                        "record_index": (
                            int(path.rsplit("[", 1)[1][:-1])
                            if path.endswith("]") and path.rsplit("[", 1)[1][:-1].isdigit()
                            else None
                        ),
                    }
                    parent.children.append(section)
                    if heading:
                        lines.append(heading)
                for key, child in value.items():
                    if truncated:
                        break
                    child_path = f"{path}.{key}" if path != "$" else f"$.{key}"
                    if isinstance(child, (dict, list)):
                        visit(child, child_path, section, str(key), depth + 1)
                    else:
                        append_leaf(section, child_path, child)
                return
            if isinstance(value, list):
                if not value:
                    append_leaf(parent, path, [])
                    return
                if all(isinstance(item, dict) for item in value):
                    for index, item in enumerate(value):
                        if truncated:
                            break
                        record_path = f"{path}[{index}]"
                        label = _record_label(item)
                        heading_source = "record_field"
                        if not label:
                            label = f"记录 {index + 1}"
                            heading_source = "record_index"
                        visit(
                            item, record_path, parent, label,
                            depth + 1, force_section=True,
                            heading_source=heading_source,
                        )
                    return
                for index, child in enumerate(value):
                    if truncated:
                        break
                    child_path = f"{path}[{index}]"
                    if isinstance(child, (dict, list)):
                        visit(child, child_path, parent, f"[{index}]", depth + 1)
                    else:
                        append_leaf(parent, child_path, child)
                return
            append_leaf(parent, path, value)

        visit(data, "$", root)
        issues = []
        if truncated:
            issues.append({
                "code": "json_structure_limit",
                "severity": "error",
                "detail": f"JSON 结构超过 {MAX_JSON_NODES} 个节点或 128 层，解析结果不完整",
            })
        return DocumentAST(
            root=root,
            source_file=file_path,
            raw_text="\n".join(lines),
            parse_status="partial" if truncated else "complete",
            parse_issues=issues,
            source_format="json",
            parser_version=self.PARSER_VERSION,
        )
