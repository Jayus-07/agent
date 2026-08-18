"""守住 ChunkFilter 在 PDF 解码异常时(Unicode replacement char)不误杀。

背景:P1 修复 — PDF parser(PyMuPDF)提取报告lab + STSong-Light 生成的 PDF 时,
会有大量 Unicode replacement char (U+FFFD `��`),被 filter 当作 symbol 过滤
导致整篇文档 0 chunks,触发 ChunkingEmptyError。

修复:filter 把 U+FFFD 视为"有意义的字符"(实际代表"PDF parser 解码失败但有原字符")。
"""
from backend.rag.preprocessing.filter import ChunkFilter


def _flt():
    return ChunkFilter()


class TestReplacementCharNotFiltered:
    """U+FFFD (replacement char) 必须不被当作 symbol。"""

    def test_garbled_pdf_chunk_with_replacement_char_passes(self):
        """PDF parser 解码失败的 chunk 应通过 filter(里面有大量 ��)。"""
        # 模拟 PDF parser 提取结果,大量 U+FFFD + 少量正常字符
        sample = "ʾ���羳���̹�˾ �������ƶ� ���Ŀ�Ŀ�� ���ʹ��Ϳ�� ��λ��1.5��"
        ok, reason = _flt().should_keep(sample, {})
        assert ok is True, f"含 replacement char 的 PDF chunk 应通过 filter,实际 reject: {reason}"

    def test_pure_replacement_chars_passes(self):
        """几乎全是 replacement char 的 chunk,只要长度达标就通过。"""
        sample = "��" * 50  # 50 个 replacement char
        ok, reason = _flt().should_keep(sample, {})
        assert ok is True, f"全 replacement char chunk 应通过,实际 reject: {reason}"

    def test_normal_chinese_chunk_still_passes(self):
        """正常中文 chunk 不被影响。"""
        sample = "本节描述跨境电商的运营流程,包括商品上架、订单管理、物流跟踪等核心模块。"
        ok, reason = _flt().should_keep(sample, {})
        assert ok is True

    def test_truly_garbage_still_rejected(self):
        """真乱码(全符号/全数字无意义)仍应被拒。"""
        # 全 ASCII 符号(非 alnum / 非中文 / 非 ��)
        sample = "@#$%^&*()_+{}[]|\\:;\"'<>,.?/~`"
        ok, reason = _flt().should_keep(sample, {})
        assert ok is False, "全符号垃圾必须被拒"