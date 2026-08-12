"""BaseDocumentParser — 格式解析器抽象。只解析文件结构，产出 Raw AST。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from backend.rag.preprocessing.ast import DocumentAST


class BaseDocumentParser(ABC):
    @abstractmethod
    def parse(self, file_path: str) -> DocumentAST:
        """读取文件并产出 Raw AST（格式级结构）。"""
        raise NotImplementedError
