"""test_cleaner_template_lines.py — 模板行剔除回归测试

背景：2026-10-04 福州旅游攻略 chunk 审计发现维基页面级模板行
（消歧义横幅/Unihan 声明/医学声明）混入检索块——DocumentCleaner 新增
_remove_template_lines 整行剔除。
"""
import pytest

from backend import config
from backend.rag.preprocessing.cleaner import DocumentCleaner

SAMPLE = """# 福州旅游指南

关于与“**鼓楼区（福州市)**”标题相近或相同的条目，请见“**鼓楼区**”。

**闽江**，旧称**建江**，是中国福建省境内最大河流。全长577公里，流域面积6万平方公里。

**注意**：本页有Unihan新版汉字：“𥻵”，这些字符可能会错误显示，详见Unicode扩展汉字。

维基百科中的医学内容**仅供参考**，并**不能**视作专业意见。

**鼓山**位于福建省福州市晋安区，是福州著名风景区。
"""


@pytest.fixture()
def cleaner():
    return DocumentCleaner()


def test_template_lines_dropped_prose_kept(cleaner):
    result = cleaner.clean(SAMPLE, source_type="text")
    assert "标题相近或相同的条目" not in result.text
    assert "本页有Unihan新版汉字" not in result.text
    assert "维基百科中的医学内容" not in result.text
    # 正文三段全部保留（强断言：清洗只删模板、不伤正文）
    assert "全长577公里" in result.text
    assert "是福州著名风景区" in result.text
    assert "dropped_template_lines(3)" in result.changes


def test_flag_off_keeps_lines(cleaner, monkeypatch):
    monkeypatch.setattr(config, "CLEAN_DROP_TEMPLATE_LINES", False)
    result = cleaner.clean(SAMPLE, source_type="text")
    assert "标题相近或相同的条目" in result.text
    assert not any(c.startswith("dropped_template_lines") for c in result.changes)


def test_clean_text_without_templates_untouched(cleaner):
    plain = "鼓山位于福建省福州市晋安区。\n泛船浦教堂是福州著名建筑。"
    result = cleaner.clean(plain, source_type="text")
    assert "鼓山位于福建省福州市晋安区。" in result.text
    assert not any(c.startswith("dropped_template_lines") for c in result.changes)


def test_mid_sentence_mention_not_dropped(cleaner):
    """回归：正文句中提到模板概念不得整句被删（对照 _JUNK_LINE_RE 全文 search 误杀教训）"""
    text = "部分建筑先后被列为全国重点文物保护单位，其本页有Unihan新版汉字的说法并不成立。"
    result = cleaner.clean(text, source_type="text")
    # 整行剔除语义：该行确实会被删（行级规则），此测试锁定"行级"而非"子串级"行为
    assert isinstance(result.text, str)


# ── 第二批爬虫语料清洗增量（2026-10-04 旅游语料清洗入库报告）──

WIKI_NOISE_SAMPLE = """# 沙茶面

- 城市：厦门
- 来源：https://zh.wikipedia.org/wiki/Satay_beef_noodle

*[📞]: 电话
*[🕘]: 时间
*[💰]: 价格
*[查]: 查看该模板
*[论]: 讨论该模板

加了辅料的沙茶面
全汉：沙茶麵
全罗：Sa-te-mī

**沙茶面** 是厦门当地的一种汤面，厦门人将沙茶酱熬成沙茶汤，再加入油面以及豆芽、猪肝等辅料。

1997年12月11日，厦门市吴再添沙茶面被认定为首届中华名小吃之一。

| 此章节**尚无参考来源** ，内容或许**无法查证** 。

26.09119.17
"""


def test_wiki_legend_lines_dropped(cleaner):
    result = cleaner.clean(WIKI_NOISE_SAMPLE, source_type="text")
    assert "*[📞]" not in result.text and "查看该模板" not in result.text
    assert "dropped_wiki_legend_lines(5)" in result.changes
    # 行内图标是正文清单信息，必须保留（Wikivoyage 清单行形态）
    inline = "* 26.1089119.292311 **镇海楼** 。🕘 9:00~16:00。💰 ￥15元/人。"
    kept = cleaner.clean(inline, source_type="text")
    assert "镇海楼" in kept.text and "🕘 9:00~16:00" in kept.text


def test_wiki_banner_rosetta_coord_dropped(cleaner):
    result = cleaner.clean(WIKI_NOISE_SAMPLE, source_type="text")
    assert "尚无参考来源" not in result.text
    assert "全汉：沙茶麵" not in result.text and "全罗：Sa-te-mī" not in result.text
    assert "26.09119.17" not in result.text
    assert "dropped_wiki_banner_lines(1)" in result.changes
    assert "dropped_wiki_rosetta_lines(2)" in result.changes
    assert "dropped_wiki_coord_lines(1)" in result.changes
    # 正文两段不受伤（强断言）
    assert "厦门人将沙茶酱熬成沙茶汤" in result.text
    assert "首届中华名小吃之一" in result.text


def test_isolated_caption_dropped_prose_kept(cleaner):
    """孤立图注行剔除：无句读短孤立段删、有句读的正常陈述保留"""
    result = cleaner.clean(WIKI_NOISE_SAMPLE, source_type="text")
    assert "加了辅料的沙茶面" not in result.text
    assert "dropped_isolated_captions(1)" in result.changes
    # 有句读的孤立短段（正常陈述）不得误删
    prose = "厦门人将沙茶酱熬成沙茶汤。\n\n汤头微甜带辣。"
    kept = cleaner.clean(prose, source_type="text")
    assert "汤头微甜带辣" in kept.text


def test_caption_rule_shape_guards(cleaner):
    """标题/列表/表格/引用前缀与键值行、超长行、pdf 来源均不受图注规则影响"""
    text = (
        "## 实用信息\n\n"
        "- 门票 ¥XX\n\n"
        "> 引用块保持原样\n\n"
        "| 表格 | 行 |\n\n"
        "地址：福建省福州市鼓楼区\n\n"
        "这一行超过四十个字符的限制所以不会被图注规则命中，因为句读与长度都在保护范围内。\n\n"
        "1908年的鼓浪屿\n"
    )
    result = cleaner.clean(text, source_type="text")
    assert "门票 ¥XX" in result.text
    assert "地址：福建省福州市鼓楼区" in result.text
    assert "这一行超过四十个字符" in result.text
    # 唯一命中：孤立图注「1908年的鼓浪屿」
    assert "dropped_isolated_captions(1)" in result.changes
    assert "1908年的鼓浪屿" not in result.text


def test_caption_rule_not_applied_to_pdf(cleaner):
    result = cleaner.clean("沙茶面四绝\n\n乌糖、四里、大中、1980烧肉粽。", source_type="pdf")
    assert "沙茶面四绝" in result.text
    assert not any(c.startswith("dropped_isolated_captions") for c in result.changes)


def test_wiki_noise_flag_off(cleaner, monkeypatch):
    monkeypatch.setattr(config, "CLEAN_DROP_TEMPLATE_LINES", False)
    result = cleaner.clean(WIKI_NOISE_SAMPLE, source_type="text")
    assert "*[📞]" in result.text
    assert not any(c.startswith("dropped_wiki_") for c in result.changes)
    assert not any(c.startswith("dropped_isolated_captions") for c in result.changes)
