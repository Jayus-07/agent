"""Helper — 在每个 PDF 生成函数里复用,把默认样式切到中文字体。

退路策略:reportlab 在某些平台(尤其是 Windows 上 STSong-Light CID 字体)
生成的 PDF 文本提取会乱码。完整测试 PDF 生成改用 PyMuPDF (fitz) 自己写 —
它写入的字符自动带正确 ToUnicode CMap,提取稳定。
"""
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT


def apply_chinese_styles(styles, font_name: str) -> None:
    """把 SampleStyleSheet 的所有标准样式切到指定中文字体。

    原地修改 styles 字典的所有标准键(Title / Heading1-6 / BodyText / Italic)。
    自定义段落用 ParagraphStyle(fontName=font_name)。

    背景:reportlab 默认 Helvetica 不带中文字形,提取出来的 PDF 中文全是乱码
    (IIIII / ��),导致下游 chunking filter 全部 reject。
    """
    for name in ("Title", "Heading1", "Heading2", "Heading3", "Heading4",
                  "Heading5", "Heading6", "BodyText", "Italic", "Normal"):
        if name in styles.byName:
            s = styles.byName[name]
            s.fontName = font_name
    # 确保有 CN_Body 自定义样式供带缩进的段落使用
    if "CN_Body" not in styles.byName:
        cn_body = ParagraphStyle(
            name="CN_Body", parent=styles["BodyText"],
            fontName=font_name, alignment=TA_LEFT,
        )
        styles.add(cn_body)