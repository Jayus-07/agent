"""backend/tests/domain_runtime/ — Domain Runtime 生产收口（2026-09-24）

跨域隔离契约测试：状态隔离 / 切域 / checkpoint namespace / pending 语义 /
跨域泄漏。生产改动只允许「补缺口」，禁止为结构漂亮重构（STOP B 铁律）。
"""
