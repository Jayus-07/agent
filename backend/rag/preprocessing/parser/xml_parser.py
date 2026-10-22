"""XML 解析器：保留元素层级、字段路径与重复记录表格。"""
from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser

_MAX_XML_NODES = 50000
_XML_DTD_RE = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _simple_element(element: ET.Element) -> bool:
    return not list(element) or all(not list(child) for child in element)


class XmlParser(BaseDocumentParser):
    """基于标准库 XML parser；拒绝 DTD/ENTITY，避免外部实体解析。"""

    PARSER_VERSION = "v2"

    def parse(self, file_path: str) -> DocumentAST:
        raw_bytes = Path(file_path).read_bytes()
        if _XML_DTD_RE.search(raw_bytes.replace(b"\x00", b"")):
            raise ValueError("XML 文档含 DTD/ENTITY 声明，已拒绝解析")
        root_element = ET.fromstring(raw_bytes)
        root = DocumentNode(type="section", text="", level=0)
        lines: list[str] = []
        count = 0
        truncated = False

        def add_leaf(parent: DocumentNode, text: str, locator: dict) -> None:
            nonlocal count, truncated
            value = text.strip()
            if not value:
                return
            if count >= _MAX_XML_NODES:
                truncated = True
                return
            count += 1
            node = DocumentNode(type="paragraph", text=value)
            node.source_locator = locator
            parent.children.append(node)
            lines.append(value)

        def visit(
            element: ET.Element,
            path: str,
            parent: DocumentNode,
            depth: int,
            heading: str,
        ) -> None:
            nonlocal count, truncated
            if count >= _MAX_XML_NODES or depth > 128:
                truncated = True
                return
            count += 1
            name = _local_name(element.tag)
            section = DocumentNode(type="section", text=heading or name, level=max(depth, 1))
            section.source_locator = {"xpath": path, "xml_tag": name}
            parent.children.append(section)
            lines.append(section.text)
            for key, value in element.attrib.items():
                if truncated:
                    break
                add_leaf(
                    section,
                    f"{name}/@{_local_name(key)}: {value}",
                    {"xpath": path, "attribute": key},
                )
            if element.text and element.text.strip():
                add_leaf(section, f"{name}: {element.text.strip()}", {"xpath": path})

            children = list(element)
            index = 0
            while index < len(children):
                if truncated:
                    break
                child = children[index]
                child_name = _local_name(child.tag)
                group = [child]
                next_index = index + 1
                while (
                    next_index < len(children)
                    and _local_name(children[next_index].tag) == child_name
                ):
                    group.append(children[next_index])
                    next_index += 1
                if (
                    len(group) > 1
                    and all(_simple_element(item) for item in group)
                ):
                    if count + len(group) > _MAX_XML_NODES:
                        truncated = True
                        break
                    count += len(group)
                    headers: list[str] = []
                    for item in group:
                        for attr in item.attrib:
                            label = f"@{_local_name(attr)}"
                            if label not in headers:
                                headers.append(label)
                        for item_child in item:
                            label = _local_name(item_child.tag)
                            if label not in headers:
                                headers.append(label)
                    if not headers:
                        headers = ["value"]
                    rows = [headers]
                    for item in group:
                        values = {f"@{_local_name(k)}": v for k, v in item.attrib.items()}
                        values.update({
                            _local_name(item_child.tag): (item_child.text or "").strip()
                            for item_child in item
                        })
                        if item.text and item.text.strip():
                            values["value"] = item.text.strip()
                        rows.append([values.get(header, "") for header in headers])
                    from backend.rag.preprocessing.parser._table_nl import make_table_chunk_text

                    table_text = make_table_chunk_text(rows, section_title=child_name)
                    table = DocumentNode(type="table", text=table_text, rows=rows)
                    table.source_locator = {
                        "xpath": f"{path}/{child_name}",
                        "row_start": 1,
                        "row_end": len(group),
                        "columns": headers[:64],
                    }
                    section.children.append(table)
                    lines.append(table_text)
                else:
                    child_path = f"{path}/{child_name}[{index + 1}]"
                    visit(child, child_path, section, depth + 1, child_name)
                index = next_index
            if element.tail and element.tail.strip():
                add_leaf(section, element.tail.strip(), {"xpath": path, "tail": True})

        visit(root_element, f"/{_local_name(root_element.tag)}[1]", root, 1, "")
        issues = []
        if truncated:
            issues.append({
                "code": "xml_structure_limit",
                "severity": "error",
                "detail": f"XML 结构超过 {_MAX_XML_NODES} 个节点或 128 层，解析结果不完整",
            })
        return DocumentAST(
            root=root,
            source_file=file_path,
            raw_text="\n".join(lines),
            parse_status="partial" if truncated else "complete",
            parse_issues=issues,
            source_format="xml",
            parser_version=self.PARSER_VERSION,
        )
