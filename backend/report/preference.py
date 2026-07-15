"""
preference.py — 用户偏好学习

记录每个用户对每种报告类型的使用偏好：
  - 上次使用的模板
  - 上次使用的图表类型
  - 使用频次

存储: data/report_preferences.json
每次 generate_report() 调用后异步记录，不阻塞主流程。
"""

import os
import json
import threading
from datetime import datetime
from typing import Dict, Any, Optional

from backend.utils.logger import logger


# =====================================================
# 配置
# =====================================================

def _get_pref_path() -> str:
    base = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data"
    )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "report_preferences.json")


# =====================================================
# 偏好存储
# =====================================================

class PreferenceStore:
    """
    用户偏好存储。

    用法:
        store = PreferenceStore()
        prefs = store.get("user_001", "monthly_sales")
        store.record("user_001", "monthly_sales", "sales_detail.j2", "bar")
    """

    def __init__(self, file_path: str = None):
        self.file_path = file_path or _get_pref_path()
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ---------------------------------------------------
    # I/O
    # ---------------------------------------------------

    def _load(self):
        """从 JSON 文件加载偏好数据"""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info(f"[Preference] 已加载 {len(self._data)} 个用户的偏好")
            else:
                self._data = {}
        except Exception as e:
            logger.warning(f"[Preference] 加载失败: {e}，使用空偏好")
            self._data = {}

    def _save(self):
        """持久化到 JSON 文件（线程安全）"""
        with self._lock:
            try:
                with open(self.file_path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"[Preference] 保存失败: {e}")

    def _save_async(self):
        """异步保存（不阻塞主流程）"""
        t = threading.Thread(target=self._save, daemon=True)
        t.start()

    # ---------------------------------------------------
    # 查询
    # ---------------------------------------------------

    def get(self, user_id: str, report_type: str) -> Dict[str, Any]:
        """
        获取用户对特定报告类型的偏好。

        返回:
            {
                "last_template": "sales_detail.j2",
                "last_chart_type": "bar",
                "usage_count": 12,
                "last_used": "2026-05-24T14:30:00"
            }
        """
        user_prefs = self._data.get(user_id, {})
        return user_prefs.get(report_type, {
            "last_template": None,
            "last_chart_type": None,
            "usage_count": 0,
            "last_used": None,
        })

    def get_template_preference(self, user_id: str, report_type: str) -> Optional[str]:
        """获取用户上次使用的模板名"""
        prefs = self.get(user_id, report_type)
        return prefs.get("last_template")

    def get_chart_preference(self, user_id: str, report_type: str) -> Optional[str]:
        """获取用户上次使用的图表类型"""
        prefs = self.get(user_id, report_type)
        return prefs.get("last_chart_type")

    # ---------------------------------------------------
    # 记录
    # ---------------------------------------------------

    def record(
        self,
        user_id: str,
        report_type: str,
        template_name: str = None,
        chart_type: str = None,
    ):
        """
        记录一次报告生成的使用偏好。
        调用后异步写入磁盘。
        """
        if user_id not in self._data:
            self._data[user_id] = {}

        if report_type not in self._data[user_id]:
            self._data[user_id][report_type] = {}

        prefs = self._data[user_id][report_type]
        prefs["usage_count"] = prefs.get("usage_count", 0) + 1
        prefs["last_used"] = datetime.now().isoformat(timespec="seconds")

        if template_name:
            prefs["last_template"] = template_name
        if chart_type:
            prefs["last_chart_type"] = chart_type

        logger.debug(
            f"[Preference] 记录: user={user_id}, type={report_type}, "
            f"template={template_name}, chart={chart_type}, "
            f"count={prefs['usage_count']}"
        )

        self._save_async()

    def reset(self, user_id: str, report_type: str = None):
        """重置用户偏好"""
        if user_id not in self._data:
            return
        if report_type:
            self._data[user_id].pop(report_type, None)
        else:
            self._data.pop(user_id, None)
        self._save_async()
        logger.info(f"[Preference] 已重置: user={user_id}, type={report_type or 'all'}")


# 全局单例
preference_store = PreferenceStore()
