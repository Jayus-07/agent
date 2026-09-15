"""日志编码测试 — 修复 Windows GBK stdout 下的中文乱码 + 静默丢失。

背景:
  - Windows 默认 sys.stdout.encoding='gbk',Python logging 用 sys.stdout 做 console handler
  - logging 默认 raiseExceptions=False,GBK 不可编码字符 UnicodeEncodeError 后静默丢失
  - 这导致 trace span name = '检索' 等 CJK 扩展字符在 GBK 环境输出成 '����'
    甚至完全丢失(不输出任何内容)

修复目标:
  1. console handler 强制 UTF-8 编码(无论 sys.stdout 是什么编码)
  2. handleError 必须可观测 — 出错时至少打印到 stderr,而不是默默吞
  3. setup_logger 可重复调用幂等(避免重复添加 handler)

测试策略:
  - 用 capsys / capfd 捕获 stderr + stdout
  - 注入带 \ufffd 等 GBK 不能编码字符的消息,验证不静默丢失
  - 验证 setup_logger 多次调用不会重复 handler
"""
import io
import logging
import sys

import pytest

from backend.shared.logger import logger, reset_logger_cache, setup_logger


# ============ Helper:模拟 GBK stdout ============

@pytest.fixture
def gbk_stdout(monkeypatch):
    """把 sys.stdout 替换成 GBK 编码的 stdout(模拟 Windows 默认环境)。"""
    if sys.platform == "win32":
        # Windows 真机:用 GBK codec 包装 stdout
        buf = io.BytesIO()
        wrapper = io.TextIOWrapper(buf, encoding="gbk", errors="strict", write_through=True)
        monkeypatch.setattr(sys, "stdout", wrapper)
        yield wrapper, buf
        wrapper.flush()
        wrapper.detach()
    else:
        # 非 Windows:跳过,因为 UTF-8 是默认
        yield sys.stdout, None


# ============ handleError 可观测性测试 ============

class TestHandleErrorObservable:
    """GBK 不能编码字符时,logging 默认会调用 logger.handleError,
    默默写一条 '--- Logging error ---' 到 stderr。我们要的不是这样:
    要让 error_type + error_msg 可观测(不是静默丢失日志)。"""

    def test_logger_does_not_silently_drop_unicode_message(
            self, gbk_stdout, tmp_path, monkeypatch):
        """GBK stdout 环境下，setup_logger 真实创建的 file_handler（UTF-8）
        必须保留 GBK 不可编码字符（旧 bug：UnicodeEncodeError 后静默丢失）。"""
        log_file = tmp_path / "cjk.log"
        # LOG_FILE 在 setup_logger 调用时读取，patch 模块属性 + 清实例缓存才生效
        monkeypatch.setattr("backend.shared.logger.LOG_FILE", str(log_file))
        reset_logger_cache()
        content = ""
        test_logger = None
        try:
            test_logger = setup_logger(name="test_drop_silent_001", level="DEBUG")
            # file_handler 级别为 WARNING，用 warning 才会落盘
            test_logger.warning("包含扩展 B 字符: \U00020000")
            fh = next(h for h in test_logger.handlers
                      if isinstance(h, logging.FileHandler))
            fh.flush()
            content = log_file.read_text(encoding="utf-8")
        finally:
            if test_logger is not None:
                for h in list(test_logger.handlers):
                    test_logger.removeHandler(h)
                    h.close()
            reset_logger_cache()
        assert "\U00020000" in content, f"file_handler 不应丢字符,实际: {content!r}"


# ============ UTF-8 console 输出测试 ============

class TestConsoleHandlerUtf8:
    """新行为:console handler 必须是 UTF-8,无视 sys.stdout 实际编码。"""

    def test_console_handler_uses_utf8(self, monkeypatch):
        """setup_logger 后,console handler 应该编码为 utf-8,无视 sys.stdout 实际编码。"""
        # 构造一个 GBK 编码的 stdout(模拟 Windows 默认环境)
        buf = io.BytesIO()
        gbk_stream = io.TextIOWrapper(buf, encoding="gbk", write_through=True)
        monkeypatch.setattr(sys, "stdout", gbk_stream)

        test_logger = setup_logger(name="test_utf8_console_001", level="DEBUG")

        # 找 console handler(StreamHandler),检查它包装的 stream 是不是 UTF-8
        stream_handlers = [h for h in test_logger.handlers
                           if isinstance(h, logging.StreamHandler)
                           and not isinstance(h, logging.FileHandler)]
        assert stream_handlers, "应至少有 1 个 StreamHandler"
        handler_stream = stream_handlers[0].stream
        # 关键断言:stream 编码是 utf-8,不是 sys.stdout 的 gbk
        encoding = getattr(handler_stream, "encoding", None)
        assert encoding and encoding.lower().replace("-", "") == "utf8", (
            f"console handler 应使用 utf-8,实际是 {encoding!r}"
        )


# ============ setup_logger 幂等性测试 ============

class TestSetupLoggerIdempotent:
    """setup_logger 必须可重复调用,不能堆 handler(否则日志会重复输出 N 倍)。"""

    def test_no_duplicate_handlers_on_repeated_calls(self):
        test_logger = setup_logger(name="test_idempotent_001", level="DEBUG")
        before = len(test_logger.handlers)

        # 调多次
        for _ in range(3):
            setup_logger(name="test_idempotent_001", level="DEBUG")

        after = len(test_logger.handlers)
        assert after == before, (
            f"setup_logger 重复调用导致 handler 数量翻倍:{before} → {after}"
        )

    def test_repeated_calls_dont_duplicate_output(self, capsys):
        """端到端:重复 setup_logger 不会让同一消息输出多次。"""
        test_logger = setup_logger(name="test_idempotent_002", level="DEBUG")
        # 第二次重复 setup
        setup_logger(name="test_idempotent_002", level="DEBUG")

        capsys.readouterr()  # 清空之前缓冲
        test_logger.info("idempotency_check_msg")
        out = capsys.readouterr().out
        # 消息应该只出现一次
        assert out.count("idempotency_check_msg") == 1, (
            f"消息应只输出 1 次,实际 {out.count('idempotency_check_msg')} 次:\n{out}"
        )


# ============ CJK 消息端到端测试 ============

class TestCjkMessageSurvives:
    """CJK 字符经过 logger 后,文件 / 编码层都应保持完整。"""

    def test_common_cjk_writes_to_file_handler(self, tmp_path, monkeypatch):
        """常见 CJK 字符(检索/解析)经 setup_logger 真实 file_handler 落盘后保持完整。"""
        log_file = tmp_path / "test.log"
        monkeypatch.setattr("backend.shared.logger.LOG_FILE", str(log_file))
        reset_logger_cache()
        content = ""
        test_logger = None
        try:
            test_logger = setup_logger(name="test_cjk_001", level="DEBUG")
            test_logger.warning("检索:解析成功")  # file_handler 级别为 WARNING
            fh = next(h for h in test_logger.handlers
                      if isinstance(h, logging.FileHandler))
            fh.flush()
            content = log_file.read_text(encoding="utf-8")
        finally:
            if test_logger is not None:
                for h in list(test_logger.handlers):
                    test_logger.removeHandler(h)
                    h.close()
            reset_logger_cache()
        assert "检索" in content, f"file_handler 应保留中文,实际内容: {content!r}"
        assert "解析" in content, f"file_handler 应保留中文,实际内容: {content!r}"