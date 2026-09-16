"""索引数据模型 — 从 indexer.py 抽出（PR-2.x 分解）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SyncResult:
    """增量同步结果。"""
    added: int = 0
    modified: int = 0
    deleted: int = 0
    skipped: int = 0
    # 单文件索引失败数（per-file 跳过后不再让整轮 sync 崩溃回退全量重建）
    failed: int = 0
    failed_files: list[str] = field(default_factory=list)

    @property
    def total_changed(self) -> int:
        return self.added + self.modified + self.deleted

    def __repr__(self) -> str:
        base = (f"SyncResult(added={self.added}, modified={self.modified}, "
                f"deleted={self.deleted}, skipped={self.skipped}")
        if self.failed:
            base += f", failed={self.failed}"
        return base + ")"


@dataclass
class Delta:
    """增量 diff 结果。"""
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
