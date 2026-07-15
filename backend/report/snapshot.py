"""
snapshot.py — 数据快照存取

每次报告生成时保存一份 JSON 快照，用于：
  - 报告回溯（对比不同时间的数据）
  - 数据审计（谁在什么时间用了什么筛选条件）
  - LLM 润色前后的数据对比

存储: data/report_snapshots/{report_type}/{timestamp}_{random}.json
自动清理 30 天前的旧快照。
"""

import os
import json
import uuid
import time
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Dict, Any, List, Optional

from backend.utils.logger import logger


class _SnapshotEncoder(json.JSONEncoder):
    """自定义 JSON 编码器：处理 PostgreSQL 的 Decimal、date、datetime 类型"""
    def default(self, obj):
        if isinstance(obj, (Decimal,)):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


# =====================================================
# 配置
# =====================================================

def _get_snapshot_dir() -> str:
    base = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "report_snapshots"
    )
    os.makedirs(base, exist_ok=True)
    return base


# 快照保留天数
SNAPSHOT_RETENTION_DAYS = int(os.getenv("REPORT_SNAPSHOT_DAYS", "30"))


# =====================================================
# 保存
# =====================================================

def save_snapshot(
    report_type: str,
    result: Dict[str, Any],
    filters: dict = None,
    rendered: str = "",
) -> str:
    """
    保存报告数据快照。

    参数:
        report_type: 报告类型
        result:      data_fetcher 返回的 {"data": [...], "metadata": {...}}
        filters:     筛选条件
        rendered:    渲染后的 Markdown（可选）

    返回:
        快照文件路径
    """
    report_dir = os.path.join(_get_snapshot_dir(), report_type)
    os.makedirs(report_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    uid = uuid.uuid4().hex[:8]
    fname = f"{timestamp}_{uid}.json"
    fpath = os.path.join(report_dir, fname)

    snapshot = {
        "report_type": report_type,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "filters": filters or {},
        "data": result.get("data", []),
        "metadata": result.get("metadata", {}),
        "rendered": rendered,
    }

    try:
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2, cls=_SnapshotEncoder)
        logger.info(f"[Snapshot] 已保存: {fname}")
        return fpath
    except Exception as e:
        logger.error(f"[Snapshot] 保存失败: {e}")
        return ""


# =====================================================
# 加载
# =====================================================

def load_snapshot(file_path: str) -> Optional[Dict[str, Any]]:
    """
    加载指定快照文件。

    返回:
        snapshot dict，或 None（读取失败）
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"[Snapshot] 文件不存在: {file_path}")
        return None
    except Exception as e:
        logger.error(f"[Snapshot] 读取失败 {file_path}: {e}")
        return None


def load_latest_snapshot(report_type: str) -> Optional[Dict[str, Any]]:
    """加载指定报告类型的最新快照"""
    report_dir = os.path.join(_get_snapshot_dir(), report_type)
    if not os.path.isdir(report_dir):
        return None

    files = sorted(
        [f for f in os.listdir(report_dir) if f.endswith(".json")],
        reverse=True,
    )
    if not files:
        return None

    return load_snapshot(os.path.join(report_dir, files[0]))


# =====================================================
# 列表 & 清理
# =====================================================

def list_snapshots(report_type: str, limit: int = 20) -> List[Dict[str, str]]:
    """
    列出指定类型的所有快照。

    返回:
        [{"name": "...", "path": "...", "saved_at": "...", "size": 1234}, ...]
    """
    report_dir = os.path.join(_get_snapshot_dir(), report_type)
    if not os.path.isdir(report_dir):
        return []

    snapshots = []
    for fname in sorted(os.listdir(report_dir), reverse=True):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(report_dir, fname)
        try:
            stat = os.stat(fpath)
        except OSError:
            continue
        snapshots.append({
            "name": fname,
            "path": fpath,
            "saved_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "size": stat.st_size,
        })
        if len(snapshots) >= limit:
            break

    return snapshots


def cleanup_old_snapshots(retention_days: int = None):
    """
    清理超过保留期的快照文件。
    默认保留 30 天。
    """
    if retention_days is None:
        retention_days = SNAPSHOT_RETENTION_DAYS

    snapshot_dir = _get_snapshot_dir()
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0

    for root, dirs, files in os.walk(snapshot_dir):
        for fname in files:
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(root, fname)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                if mtime < cutoff:
                    os.remove(fpath)
                    removed += 1
            except Exception as e:
                logger.warning(f"[Snapshot] 清理文件失败 {fpath}: {e}")

    if removed > 0:
        logger.info(f"[Snapshot] 清理完成: 删除 {removed} 个过期快照 (>{retention_days}天)")
    return removed
