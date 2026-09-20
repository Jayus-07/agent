"""跨处理阶段传递入库运行标识的轻量上下文。

本模块只能依赖 Python 标准库，供 LLM、OCR、Embedding 等底层组件读取
当前文档处理的 ``run_id/step_id``。使用 ``ContextVar`` 保证并发任务之间
不会共享可变的模块级状态。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class ProcessingBinding:
    """当前调用所属的处理运行和阶段。"""

    run_id: str
    step_id: str
    role: str | None
    stage: str


_binding_var: ContextVar[ProcessingBinding | None] = ContextVar(
    "rag_processing_binding", default=None
)


def get_processing_binding() -> ProcessingBinding | None:
    """读取当前上下文绑定；未在入库链路中时返回 ``None``。"""

    return _binding_var.get()


@contextmanager
def bind_processing(binding: ProcessingBinding) -> Iterator[ProcessingBinding]:
    """临时绑定一个处理阶段，并在退出时恢复上层上下文。"""

    token: Token[ProcessingBinding | None] = _binding_var.set(binding)
    try:
        yield binding
    finally:
        _binding_var.reset(token)
