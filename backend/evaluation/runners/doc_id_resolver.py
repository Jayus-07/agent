"""DocIdResolver — 评测用双向 doc_id ↔ filename 映射。

解决评测数据集（文件名）与检索器输出（hash doc_id）之间的协议不一致。
通过 DocumentRegistry 预加载 + derive_doc_id 正向推导回退，实现任意格式 ID
到 canonical basename 的归一化。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from backend.shared.logger import logger


class DocIdResolver:
    """Bidirectional doc_id <-> filename resolution for evaluation.

    Canonical key = human-readable basename (dataset-side format).
    """

    def __init__(self, registry_path: str | None = None):
        self._hash_to_name: dict[str, str] = {}
        self._name_to_hashes: dict[str, set[str]] = {}
        self._alias: dict[str, str] = {}
        self._backend = "empty"
        self._unresolved: list[str] = []
        self._load_registry(registry_path)

    def _load_registry(self, registry_path: str | None) -> None:
        try:
            if registry_path is None:
                from backend.config.database import DOC_REGISTRY_PATH
                registry_path = DOC_REGISTRY_PATH

            from backend.rag.indexing.doc_registry import DocumentRegistry
            registry = DocumentRegistry(registry_path)
            rows = registry.list_active()

            for row in rows:
                doc_id = row.get("doc_id", "")
                file_name = row.get("file_name", "")
                if not file_name:
                    file_path = row.get("file_path", "")
                    file_name = os.path.basename(file_path) if file_path else ""
                if not doc_id or not file_name:
                    continue
                self._hash_to_name[doc_id] = file_name
                self._name_to_hashes.setdefault(file_name, set()).add(doc_id)

            self._backend = "registry" if self._hash_to_name else "empty"
            logger.info(f"[DocIdResolver] 加载 registry: {len(self._hash_to_name)} 条映射")
        except Exception as e:
            logger.warning(f"[DocIdResolver] registry 加载失败，降级为 basename_only: {e}")
            self._backend = "basename_only"

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def unresolved(self) -> list[str]:
        return list(self._unresolved)

    def seed_alias(self, doc_id: str, source_file: str) -> None:
        if doc_id and source_file:
            self._alias[doc_id] = source_file
            self._hash_to_name.setdefault(doc_id, source_file)
            self._name_to_hashes.setdefault(source_file, set()).add(doc_id)

    def canonical(self, token: str, kb_id: str = "", department: str = "") -> str:
        if not token:
            return token

        if token in self._alias:
            return self._alias[token]

        if token in self._hash_to_name:
            return self._hash_to_name[token]

        base = Path(token.replace("\\", "/")).name
        if base and "." in base:
            return base

        try:
            from backend.rag.indexing.doc_registry import DocumentRegistry
            from backend.config.database import DOC_REGISTRY_PATH
            registry = DocumentRegistry(DOC_REGISTRY_PATH)
            row = registry.get_by_doc_id(token)
            if row:
                file_name = row.get("file_name", "")
                if not file_name:
                    file_name = os.path.basename(row.get("file_path", ""))
                if file_name:
                    self._hash_to_name[token] = file_name
                    return file_name
        except Exception:
            pass

        self._unresolved.append(token)
        return token

    def candidate_hashes(self, filename: str, kb_id: str = "", department: str = "") -> set[str]:
        base = Path(filename.replace("\\", "/")).name
        if not base:
            return set()

        out = set(self._name_to_hashes.get(base, set()))

        out.add(hashlib.md5(base.encode("utf-8")).hexdigest()[:10])

        try:
            from backend.rag.indexing.doc_id import derive_doc_id
            combos = [
                (kb_id or "default", department or "general"),
                (kb_id or "default", ""),
                ("default", department or "general"),
                ("default", "general"),
            ]
            for kb, dept in combos:
                out.add(derive_doc_id(kb_id=kb, department=dept, basename=base))
        except Exception:
            pass

        return out

    def matches(self, actual: str, expected: str, kb_id: str = "", department: str = "") -> bool:
        if not actual or not expected:
            return False

        if actual == expected:
            return True

        actual_canon = self.canonical(actual, kb_id, department)
        expected_canon = self.canonical(expected, kb_id, department)
        if actual_canon == expected_canon:
            return True

        actual_base = Path(actual.replace("\\", "/")).name
        expected_base = Path(expected.replace("\\", "/")).name
        if actual_base and expected_base and actual_base == expected_base:
            return True

        if actual in self.candidate_hashes(expected, kb_id, department):
            return True

        return False
