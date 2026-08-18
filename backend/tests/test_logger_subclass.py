"""handleError 不污染全局 logging — 子类化验证。

背景:之前 logger.py 直接 monkey-patch `logging.Logger.handleError` 为全局污染。
任何 import backend.shared.logger 的项目都会让所有 logger 实例改行为。
本次修复改用子类化 + setLoggerClass,只影响本项目的 logger,。

设计:用一个 Logging 类 Override handleError,通过 logging.setLoggerClass() 注册。
子 logger 才会用我们的版本,标准库 logger 不受影响。
"""
import io
import logging
import sys

import pytest

# 不需要 _reset_logger_class fixture — 实现已经保证不调 setLoggerClass


class TestObservableHandleError:
    """验证子类化后的 Logger.handleError 行为可观测。"""

    def test_handle_error_writes_to_stderr(self, capsys):
        from backend.shared.logger import ObservableLogger

        log = ObservableLogger(name="test_observable_001")
        rec = logging.LogRecord(
            name="test", level=logging.INFO, pathname="x.py", lineno=1,
            msg="test message 你好", args=(), exc_info=None,
        )

        # 直接调用 handleError (我们的可观测版本)
        log.handleError(rec)

        captured = capsys.readouterr()
        assert "Logging error" in captured.err
        assert "test message 你好" in captured.err

    def test_handle_error_falls_back_silently_when_stderr_broken(self, monkeypatch):
        """stderr 写不动时不能无限递归。"""
        from backend.shared.logger import ObservableLogger

        # 模拟 stderr.write 抛异常
        def fake_write(_s):
            raise OSError("stderr broken")
        monkeypatch.setattr(sys.stderr, "write", fake_write)

        log = ObservableLogger(name="test_fallback_001")
        rec = logging.LogRecord(
            name="test", level=logging.INFO, pathname="x.py", lineno=1,
            msg="should not raise", args=(), exc_info=None,
        )

        # 必须不抛异常
        log.handleError(rec)

    def test_subclass_used_by_logger_factory(self):
        """logging.getLogger() 应该返回 ObservableLogger 实例。"""
        from backend.shared.logger import setup_logger, ObservableLogger

        test_logger = setup_logger(name="test_factory_001", level="DEBUG")
        assert isinstance(test_logger, ObservableLogger), (
            "setup_logger 必须返回 ObservableLogger 子类实例,而不是标准 Logger"
        )

    def test_does_not_pollute_global_logging(self):
        """关键:不影响标准 logging.Logger 的 handleError 行为。"""
        # 检查 setLoggerClass 是只对本 logger 生效
        from backend.shared import logger as logger_mod

        # backend.shared.logger 在 import 时调 setLoggerClass(ObservableLogger)
        # 但 Logger.manager.loggerClass 只影响之后 NEW 出来的 logger
        # 标准库代码之前已经 import 的 logger 不会被替换

        # 创建新的"外部"logger (用标准类)
        std_log = logging.getLogger("test_external_std")
        # 它必须是标准 Logger 类,不是 ObservableLogger
        assert not isinstance(std_log, logger_mod.ObservableLogger), (
            "ObservableLogger 不应污染 getLogger 返回的实例 — 应该是我们显式创建时才是"
        )


class TestSubclassDoesNotPollute:
    """核心要求:setup_logger 不能污染全局 logging(不调 setLoggerClass)。"""

    def test_does_not_change_global_logger_class(self):
        """setup_logger 之后,logging.Logger 仍然是标准类(没调 setLoggerClass)。"""
        # 在 setup_logger 之前保存当前 manager.loggerClass
        original_class_before = logging.Logger.manager.loggerClass
        # 在 setup_logger 之前显式创建一个标准 logger 作为基线
        baseline_log = logging.getLogger("test_baseline_pre_setup")
        original_class_after = type(baseline_log)

        from backend.shared.logger import setup_logger
        setup_logger(name="test_no_pollute_001", level="DEBUG")

        # setup_logger 调用后,manager.loggerClass 必须没变
        assert logging.Logger.manager.loggerClass is original_class_before, (
            "setup_logger 调了 setLoggerClass,会污染全局"
        )

        # 新创建的 logger 仍然是标准类
        new_log = logging.getLogger("test_baseline_post_setup")
        assert type(new_log) is original_class_after, (
            "setup_logger 之后 logging.getLogger 创建的实例应该是标准 Logger,不是 ObservableLogger"
        )

    def test_setup_returns_observable_directly(self):
        """setup_logger 返回的实例是 ObservableLogger,但 getLogger 不变。"""
        from backend.shared.logger import setup_logger, ObservableLogger
        log = setup_logger(name="test_setup_returns_001", level="DEBUG")
        assert isinstance(log, ObservableLogger)

        # 对比: getLogger 拿到的不是 ObservableLogger
        std_log = logging.getLogger("test_setup_returns_002")
        assert not isinstance(std_log, ObservableLogger)


class TestLoggerFactoryClassBinding:
    """setup_logger 必须保证返回 ObservableLogger + 缓存幂等。"""

    def test_returns_observable_after_setup(self):
        from backend.shared.logger import setup_logger, ObservableLogger

        log = setup_logger(name="test_factory_binding_001", level="DEBUG")
        assert isinstance(log, ObservableLogger)

    def test_idempotent_returns_same_instance(self):
        """同一 name 多次调用必须返回同一实例(不堆 handler)。"""
        from backend.shared.logger import setup_logger, reset_logger_cache
        reset_logger_cache()
        log1 = setup_logger(name="test_idempotent_factory", level="DEBUG")
        log2 = setup_logger(name="test_idempotent_factory", level="DEBUG")
        assert log1 is log2
        # handler 数应=2(console + file),不是 4
        assert len(log1.handlers) == 2


class TestBackwardCompatibility:
    """可观测版本不应破坏 logger 的正常使用。"""

    def test_logger_can_still_log_normally(self, capsys):
        from backend.shared.logger import setup_logger

        log = setup_logger(name="test_normal_log_001", level="DEBUG")
        capsys.readouterr()  # 清空

        log.info("normal message 你好")

        out = capsys.readouterr().out
        assert "normal message 你好" in out

    def test_logger_module_singleton_is_observable(self):
        """默认 logger 实例必须是 ObservableLogger。"""
        from backend.shared.logger import logger, ObservableLogger
        assert isinstance(logger, ObservableLogger)