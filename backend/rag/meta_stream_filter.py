"""meta_stream_filter.py — 流式 META 尾部拦截器

背景: RAG 生成结果末尾带机读标注 <!--META{...}-->,供 _verify 解析
can_answer/citations 做拒答决策。非流式路径由 _verify 统一剥离;
真流式路径逐 chunk 直发前端,META 会闪现在用户消息末尾。

约定:
  - full(): 聚合的全量文本(含 META)——与 invoke 返回一致,下游决策不破坏
  - feed()/flush() 返回可安全外发的增量(META 及其后的内容全部剔除)
  - META 起始符跨 chunk 切分时靠尾部缓冲正确识别
"""


class MetaStreamFilter:
    """聚合全量、外发剔除 <!--META...--> 尾部的流式过滤器。"""

    _MARKER = "<!--META"

    def __init__(self):
        self._parts: list[str] = []
        self._pending = ""    # 尾部缓冲:可能包含半截 META 起始符
        self._in_meta = False

    def feed(self, text: str) -> str:
        """喂入一个 chunk,返回本次可外发的增量文本。"""
        if not text or self._in_meta:
            if text:
                self._parts.append(text)  # META 段:聚合但不外发
            return ""
        self._parts.append(text)
        buf = self._pending + text
        idx = buf.find(self._MARKER)
        if idx != -1:
            self._in_meta = True
            self._pending = ""
            emit = buf[:idx]
        else:
            keep = len(self._MARKER) - 1
            if len(buf) > keep:
                emit, self._pending = buf[:-keep], buf[-keep:]
            else:
                emit, self._pending = "", buf
        return emit

    def flush(self) -> str:
        """流结束时调用:放行尾部缓冲中非 META 的剩余正文。"""
        if self._in_meta or not self._pending:
            self._pending = ""
            return ""
        buf, self._pending = self._pending, ""
        idx = buf.find(self._MARKER)
        return buf[:idx] if idx != -1 else buf

    @property
    def full(self) -> str:
        """全量聚合文本(含 META)——与 invoke 返回完全一致。"""
        return "".join(self._parts)
