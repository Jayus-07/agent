"""使用标准库解析 PPTX 的幻灯片、文本、表格、备注和图片说明。"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser

_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_MAX_ENTRIES = 20000
_MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024


def _text(element: ET.Element) -> str:
    return " ".join("".join(item.text or "" for item in element.findall(".//a:t", _NS)).split())


def _slide_relationships(archive: zipfile.ZipFile, slide_number: int) -> dict[str, str]:
    path = f"ppt/slides/_rels/slide{slide_number}.xml.rels"
    try:
        root = ET.fromstring(archive.read(path))
    except KeyError:
        return {}
    result: dict[str, str] = {}
    for relation in root.findall("rel:Relationship", _NS):
        target = relation.get("Target", "")
        if target.startswith("/"):
            normalized = target.lstrip("/")
        else:
            normalized = f"ppt/slides/{target}"
        # OOXML 相对路径可能包含 ..；规范化后限制在包内，不访问文件系统。
        parts: list[str] = []
        for part in normalized.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part and part != ".":
                parts.append(part)
        result[relation.get("Id", "")] = "/".join(parts)
    return result


def _shape_locator(slide_number: int, shape: ET.Element, shape_index: int) -> dict:
    non_visual = shape.find("p:nvSpPr/p:cNvPr", _NS)
    if non_visual is None:
        non_visual = shape.find("p:nvGraphicFramePr/p:cNvPr", _NS)
    if non_visual is None:
        non_visual = shape.find("p:nvPicPr/p:cNvPr", _NS)
    locator = {"slide": slide_number, "shape_index": shape_index}
    if non_visual is not None:
        if non_visual.get("id"):
            locator["shape_id"] = non_visual.get("id")
        if non_visual.get("name"):
            locator["shape_name"] = non_visual.get("name")
    return locator


def _is_title_shape(shape: ET.Element) -> bool:
    placeholder = shape.find("p:nvSpPr/p:nvPr/p:ph", _NS)
    return placeholder is not None and placeholder.get("type") in {"title", "ctrTitle"}


def _table_rows(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall("a:tr", _NS):
        cells = []
        for cell in row.findall("a:tc", _NS):
            cells.append(_text(cell))
        if any(cells):
            rows.append(cells)
    return rows


def _chart_points(element: ET.Element | None) -> dict[int, str]:
    if element is None:
        return {}
    result = {}
    for point in element.findall(".//c:pt", _NS):
        value = point.find("c:v", _NS)
        if value is not None and value.text is not None:
            try:
                index = int(point.get("idx", "0"))
            except ValueError:
                continue
            result[index] = value.text
    return result


def _chart_rows(chart_root: ET.Element) -> tuple[str, list[list[str]]]:
    title = _text(chart_root.find(".//c:title", _NS))
    series = []
    category_labels: dict[int, str] = {}
    for item in chart_root.findall(".//c:ser", _NS):
        name_node = item.find("c:tx", _NS)
        name_value = name_node.find(".//c:v", _NS) if name_node is not None else None
        name = (
            name_value.text.strip()
            if name_value is not None and name_value.text
            else f"系列 {len(series) + 1}"
        )
        category = _chart_points(item.find("c:cat", _NS))
        values = _chart_points(item.find("c:val", _NS))
        if not values:
            values = _chart_points(item.find("c:yVal", _NS))
        for index, label in category.items():
            category_labels.setdefault(index, label)
        if values:
            series.append((name, category, values))
    if not series:
        return title, []
    indices = sorted({index for _, _, values in series for index in values})
    rows = [["类别"] + [name for name, _, _ in series]]
    for index in indices:
        label = category_labels.get(index, str(index + 1))
        rows.append([
            label,
            *[values.get(index, "") for _, _, values in series],
        ])
    return title, rows


def _core_title(archive: zipfile.ZipFile) -> str:
    try:
        root = ET.fromstring(archive.read("docProps/core.xml"))
    except (KeyError, ET.ParseError):
        return ""
    for tag in ("dc:title", "cp:title"):
        element = root.find(tag, _NS)
        if element is not None and (element.text or "").strip():
            return element.text.strip()
    return ""


class PptxParser(BaseDocumentParser):
    """按幻灯片顺序解析可提取文本，保留表格、标题和原始形状定位。"""

    PARSER_VERSION = "v2"

    def parse(self, file_path: str) -> DocumentAST:
        root = DocumentNode(type="section", text="", level=0)
        raw_lines: list[str] = []
        issues: list[dict] = []
        with zipfile.ZipFile(file_path) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ENTRIES or sum(info.file_size for info in infos) > _MAX_UNCOMPRESSED_BYTES:
                raise ValueError("PPTX 解压后超过解析资源上限")
            slide_paths = sorted(
                (name for name in archive.namelist() if _SLIDE_RE.match(name)),
                key=lambda name: int(_SLIDE_RE.match(name).group(1)),
            )
            if not slide_paths:
                raise ValueError("PPTX 包中没有可解析的幻灯片")
            document_title = _core_title(archive)
            for slide_path in slide_paths:
                slide_number = int(_SLIDE_RE.match(slide_path).group(1))
                slide_xml = ET.fromstring(archive.read(slide_path))
                shape_tree = slide_xml.find("p:cSld/p:spTree", _NS)
                if shape_tree is None:
                    continue
                slide_title = ""
                title_shape = None
                for shape in shape_tree.findall("p:sp", _NS):
                    if _is_title_shape(shape):
                        title_shape = shape
                        slide_title = _text(shape)
                        if slide_title:
                            break
                # 每页都必须有 slide section：无标题页不能用空 section
                # 把正文直接挂到 root，否则多页内容会被递归策略合并成一块。
                heading = slide_title or f"幻灯片 {slide_number}"
                section = DocumentNode(type="section", text=heading, level=1)
                section.source_locator = {
                    "slide": slide_number,
                    "section_type": "slide",
                }
                if not slide_title:
                    section.source_locator["heading_source"] = "slide_number"
                root.children.append(section)
                raw_lines.append(heading)
                target = section

                relationships = _slide_relationships(archive, slide_number)
                for shape_index, shape in enumerate(list(shape_tree), start=1):
                    tag = shape.tag.rsplit("}", 1)[-1]
                    locator = _shape_locator(slide_number, shape, shape_index)
                    if shape is title_shape:
                        continue
                    if tag == "sp":
                        nv = shape.find("p:nvSpPr/p:cNvPr", _NS)
                        description = (nv.get("descr", "").strip() if nv is not None else "")
                        paragraphs = shape.findall("p:txBody/a:p", _NS)
                        for paragraph_index, paragraph in enumerate(paragraphs):
                            value = _text(paragraph)
                            if not value:
                                continue
                            bullet = paragraph.find("a:pPr/a:buChar", _NS) is not None
                            node = DocumentNode(
                                type="list" if bullet else "paragraph", text=value,
                            )
                            node.source_locator = {
                                **locator, "paragraph_index": paragraph_index,
                            }
                            target.children.append(node)
                            raw_lines.append(value)
                        if description and not any(_text(item) for item in paragraphs):
                            node = DocumentNode(type="figure", text=f"图片说明：{description}")
                            node.source_locator = {**locator, "has_alt": True}
                            target.children.append(node)
                            raw_lines.append(node.text)
                    elif tag == "graphicFrame":
                        table = shape.find(".//a:tbl", _NS)
                        if table is not None:
                            rows = _table_rows(table)
                            if rows:
                                from backend.rag.preprocessing.parser._table_nl import make_table_chunk_text

                                table_text = make_table_chunk_text(rows)
                                node = DocumentNode(type="table", text=table_text, rows=rows)
                                node.source_locator = locator
                                target.children.append(node)
                                raw_lines.append(table_text)
                        else:
                            chart_ref = shape.find(".//c:chart", _NS)
                            rel_attr = (
                                chart_ref.get(f"{{{_NS['r']}}}id", "")
                                if chart_ref is not None else ""
                            )
                            related = relationships.get(rel_attr, "")
                            chart_rows: list[list[str]] = []
                            chart_title = ""
                            if "/charts/" in related:
                                try:
                                    chart_root = ET.fromstring(archive.read(related))
                                    chart_title, chart_rows = _chart_rows(chart_root)
                                except (KeyError, ET.ParseError):
                                    chart_rows = []
                            if chart_rows:
                                from backend.rag.preprocessing.parser._table_nl import make_table_chunk_text

                                table_text = make_table_chunk_text(
                                    chart_rows, section_title=chart_title,
                                )
                                node = DocumentNode(
                                    type="table", text=table_text, rows=chart_rows,
                                )
                                node.source_locator = {
                                    **locator,
                                    "chart_part": related,
                                    "has_chart_title": bool(chart_title),
                                    "columns": chart_rows[0],
                                }
                                target.children.append(node)
                                raw_lines.append(table_text)
                            elif "/charts/" in related or chart_ref is not None:
                                issues.append({
                                    "code": "pptx_chart_not_extracted",
                                    "severity": "warn",
                                    "detail": "图表对象已定位，但未将图表数据作为文本事实解析",
                                    "source_locator": locator,
                                })
                    elif tag == "pic":
                        nv = shape.find("p:nvPicPr/p:cNvPr", _NS)
                        alt = (nv.get("descr", "").strip() if nv is not None else "")
                        if alt:
                            node = DocumentNode(type="figure", text=f"图片说明：{alt}")
                            node.source_locator = {**locator, "has_alt": True}
                            target.children.append(node)
                            raw_lines.append(node.text)
                        else:
                            issues.append({
                                "code": "pptx_image_without_alt",
                                "severity": "warn",
                                "detail": "图片没有可用说明，未将图像内容当作已解析事实",
                                "source_locator": locator,
                            })

                rels = relationships
                note_target = next(
                    (value for value in rels.values() if "/notesSlides/notesSlide" in value),
                    "",
                )
                if note_target:
                    try:
                        notes_xml = ET.fromstring(archive.read(note_target))
                    except (KeyError, ET.ParseError):
                        notes_xml = None
                    if notes_xml is not None:
                        note_texts = []
                        for shape in notes_xml.findall(".//p:sp", _NS):
                            placeholder = shape.find("p:nvSpPr/p:nvPr/p:ph", _NS)
                            if placeholder is not None and placeholder.get("type") in {
                                "sldNum", "hdr", "ftr", "dt", "sldImg",
                            }:
                                continue
                            value = _text(shape)
                            if value:
                                note_texts.append(value)
                        if note_texts:
                            note = DocumentNode(
                                type="paragraph", text="\n".join(note_texts),
                            )
                            note.source_locator = {"slide": slide_number, "kind": "speaker_notes"}
                            target.children.append(note)
                            raw_lines.extend(note_texts)

        return DocumentAST(
            root=root,
            source_file=file_path,
            raw_text="\n".join(raw_lines),
            document_title=document_title,
            parse_status="partial" if issues else "complete",
            parse_issues=issues,
            source_format="pptx",
            parser_version=self.PARSER_VERSION,
        )
