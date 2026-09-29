"""node_runtime/error_policy.py — 异常策略与超时策略

异常策略由调用方（域适配层）决定，runner 只执行不判断：
- RAISE_THROUGH      异常原样穿透（主图语义：交 LangGraph 图级错误通道），
                     不触发 on_error——上层观测面自会记录，runner 不重复埋点；
- SWALLOW_TO_STATUS  异常转 status=failed 不穿透（专家语义：异常不穿透）；
- FALLBACK           异常转调用方给定的兜底值（run(fallback=...)），
                     结果 status=success；异常本身仍经 on_error 留痕（可观测不丢）。

TimeoutStrategy：
- NONE            调用线程内直接执行；
- THREAD_ISOLATED per-call 单 worker 线程 + contextvars.copy_context()。
                  这是唯一允许的超时实现，禁止共享线程池 timeout：
                  - P2.3（2026-09-17 全量回归实证）：共享池的孤儿任务长期占用
                    worker，后续调用在队列里排队，future.result(timeout) 会在
                    任务开跑前误判超时；
                  - B4（2026-09-23 生产收口）：submit 不携带 contextvars
                    （新线程是空上下文），请求级状态（如 Context Budget 业务
                    pin）在线程内不可见——必须显式拷贝当前上下文执行。
"""
from __future__ import annotations

from enum import Enum


class ErrorPolicy(str, Enum):
    RAISE_THROUGH = "raise_through"
    SWALLOW_TO_STATUS = "swallow_to_status"
    FALLBACK = "fallback"


class TimeoutStrategy(str, Enum):
    NONE = "none"
    # copy_context=True 固定在实现内（B4），无第二种形态
    THREAD_ISOLATED = "thread_isolated"
