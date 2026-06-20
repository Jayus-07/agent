"""PreferenceStore 测试 — 用户偏好学习"""
import pytest
import json
import os

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_agent.preference import PreferenceStore


class TestPreferenceStore:
    """用户偏好存储"""

    def test_new_user_default_prefs(self, tmp_path):
        pref_file = tmp_path / "prefs.json"
        store = PreferenceStore(file_path=str(pref_file))
        prefs = store.get("new_user", "sales_report")
        assert prefs["last_template"] is None
        assert prefs["last_chart_type"] is None
        assert prefs["usage_count"] == 0
        assert prefs["last_used"] is None

    def test_record_and_get(self, tmp_path):
        pref_file = tmp_path / "prefs.json"
        store = PreferenceStore(file_path=str(pref_file))
        store.record("user_a", "monthly_sales", template_name="sales_detail.j2",
                      chart_type="bar")

        prefs = store.get("user_a", "monthly_sales")
        assert prefs["last_template"] == "sales_detail.j2"
        assert prefs["last_chart_type"] == "bar"
        assert prefs["usage_count"] == 1
        assert prefs["last_used"] is not None

    def test_usage_count_increments(self, tmp_path):
        pref_file = tmp_path / "prefs.json"
        store = PreferenceStore(file_path=str(pref_file))
        for _ in range(3):
            store.record("user_a", "monthly_sales")
        prefs = store.get("user_a", "monthly_sales")
        assert prefs["usage_count"] == 3

    def test_get_template_preference(self, tmp_path):
        pref_file = tmp_path / "prefs.json"
        store = PreferenceStore(file_path=str(pref_file))
        store.record("user_x", "report_type_1", template_name="detail.j2")
        tpl = store.get_template_preference("user_x", "report_type_1")
        assert tpl == "detail.j2"

    def test_get_chart_preference(self, tmp_path):
        pref_file = tmp_path / "prefs.json"
        store = PreferenceStore(file_path=str(pref_file))
        store.record("user_x", "report_type_1", chart_type="pie")
        chart = store.get_chart_preference("user_x", "report_type_1")
        assert chart == "pie"

    def test_update_template_only(self, tmp_path):
        pref_file = tmp_path / "prefs.json"
        store = PreferenceStore(file_path=str(pref_file))
        store.record("user_a", "sales", template_name="v1.j2")
        store.record("user_a", "sales", template_name="v2.j2")
        assert store.get_template_preference("user_a", "sales") == "v2.j2"
        assert store.get("user_a", "sales")["usage_count"] == 2

    def test_reset_user_report_type(self, tmp_path):
        pref_file = tmp_path / "prefs.json"
        store = PreferenceStore(file_path=str(pref_file))
        store.record("user_z", "type_x", template_name="t.j2")
        store.reset("user_z", "type_x")
        prefs = store.get("user_z", "type_x")
        assert prefs["usage_count"] == 0  # back to defaults

    def test_reset_user_all(self, tmp_path):
        pref_file = tmp_path / "prefs.json"
        store = PreferenceStore(file_path=str(pref_file))
        store.record("user_z", "type_x", template_name="t.j2")
        store.record("user_z", "type_y", template_name="u.j2")
        store.reset("user_z")  # reset all
        assert store.get("user_z", "type_x")["usage_count"] == 0

    def test_persistence(self, tmp_path):
        """数据持久化后重新加载"""
        pref_file = tmp_path / "prefs.json"
        store1 = PreferenceStore(file_path=str(pref_file))
        store1.record("persist_user", "persist_type", template_name="persist.j2")

        # 强制同步保存（关闭 _save_async 的线程问题）
        store1._save()

        store2 = PreferenceStore(file_path=str(pref_file))
        prefs = store2.get("persist_user", "persist_type")
        assert prefs["last_template"] == "persist.j2"

    def test_multiple_users_independent(self, tmp_path):
        pref_file = tmp_path / "prefs.json"
        store = PreferenceStore(file_path=str(pref_file))
        store.record("user_1", "report", template_name="t1.j2")
        store.record("user_2", "report", template_name="t2.j2")
        assert store.get_template_preference("user_1", "report") == "t1.j2"
        assert store.get_template_preference("user_2", "report") == "t2.j2"

    def test_empty_file_path_creates_default(self, tmp_path):
        """空文件路径自动使用默认路径"""
        # 不传 file_path，使用默认路径
        store = PreferenceStore()
        assert store.file_path.endswith("report_preferences.json")
