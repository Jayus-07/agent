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

from backend.shared.logger import logger


# =====================================================
# 配置
# =====================================================

def _get_pref_path() -> str:
    base = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data"
    )
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        # 容器镜像中 base 可能被同名文件占用导致启动崩溃循环；
        # 偏好写入是辅助功能，软降级到临时目录不阻塞服务启动
        import tempfile
        base = os.path.join(tempfile.gettempdir(), "agent_prefs")
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
        # 「读失败」≠「本来就没有」（结构病审查 P3-3）：文件不存在 = 首次使用，
        # 正常；文件在但读不了/解析不了 = 异常，偏好**退化成空**而不是没有偏好。
        # 两者此前都落成 _data={} 且只打 warning，调用方无法区分。
        self.load_error: str | None = None
        self._load()

    # ---------------------------------------------------
    # I/O
    # ---------------------------------------------------

    def _load(self):
        """从 JSON 文件加载偏好数据。

        - 文件不存在：首次使用，`_data={}`，`load_error=None`
        - 文件存在但读/解析失败：`_data={}` + `load_error` 记原因（error 级日志），
          调用方可据此判断「这次报告是按无偏好出的」还是「本来就没偏好」。
        """
        self.load_error = None
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info(f"[Preference] 已加载 {len(self._data)} 个用户的偏好")
            else:
                self._data = {}
        except Exception as e:
            self.load_error = f"{type(e).__name__}: {e}"
            logger.error(
                f"[Preference] 偏好文件存在但读取失败（{self.load_error}），"
                f"本次按空偏好运行；path={self.file_path}"
            )
            self._data = {}

    @property
    def degraded(self) -> bool:
        """偏好是否因读取失败而退化为空（供上层如实告知，而非当作「无偏好」）。"""
        return self.load_error is not None

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
