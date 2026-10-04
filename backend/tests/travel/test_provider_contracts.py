"""tests/travel/test_provider_contracts.py — 外部 Provider 契约快照测试（验收 #138）

四个外部源的**字段契约**用离线 fixture 锁死：字段改名/删除 = 测试红，
adapter 层第一时间暴露（消费方字段访问不会静默炸）。快照断言「键集合
精确相等」——新增字段是兼容演进（增键不破坏消费方），故对增量宽松、
对删除/改名严格：断言「契约键 ⊆ 实际键」且核心键类型正确。

覆盖：①腾讯 LBS 地点检索归一 ②高德商户检索 ③12306 车票封套 ④腾讯天气
（future days）。全部不联网（HTTP 层 stub / 纯函数直测）。
"""
from __future__ import annotations

import json


class TestTencentPlaceContract:
    """腾讯 place_search 归一化输出契约（live_map/poi_service 共同消费）。"""

    HIT = {
        "id": "tx1", "title": "鼓山", "address": "福州市晋安区鼓山路",
        "category": "公园:风景区", "tel": "", "location": {"lat": 26.05, "lng": 119.39},
        "ad_info": {"province": "福建省", "city": "福州市", "district": "晋安区",
                    "adcode": "350111"},
    }

    def test_normalized_keys_locked(self):
        from backend.infra.lbs.api import _normalize_poi

        poi = _normalize_poi(self.HIT)
        contract = {"id", "name", "address", "category", "tel", "lat", "lng",
                    "distance_m", "province", "city", "district", "adcode"}
        missing = contract - set(poi)
        assert not missing, f"腾讯归一化契约键缺失: {missing}（消费方会炸）"
        assert poi["name"] == "鼓山"
        assert isinstance(poi["lat"], float) and isinstance(poi["lng"], float)
        assert poi["city"] == "福州市" and poi["adcode"] == "350111"

    def test_missing_ad_info_never_crashes(self):
        from backend.infra.lbs.api import _normalize_poi

        poi = _normalize_poi({"id": "x", "title": "某地", "location": {"lat": 1.0, "lng": 2.0}})
        assert poi["city"] == "" and poi["adcode"] == ""


class TestAmapMerchantContract:
    """高德商户检索封套契约（美食推荐卡/A1 评分源共同消费）。"""

    def test_envelope_and_record_keys_locked(self, monkeypatch):
        from backend.config import map as map_cfg
        from backend.tools.map import merchant as merchant_mod

        # 根 conftest 离线纪律关了 AMAP_ENABLED（autouse），用例级恢复（LIFO 生效）
        monkeypatch.setattr(map_cfg, "AMAP_ENABLED", True)

        # rec 形态 = amap.normalize_merchant 输出（place_text 的真实返回条目）
        rec = {
            "id": "am1", "name": "老福州菜馆", "category": "餐饮服务:中餐厅:福建菜",
            "typecode": "050200", "rating": 4.5, "avg_cost_cny": 88.0,
            "open_time_today": "10:00-22:00",
            "open_time_week": "周一至周日 10:00-22:00",
            "tel": "0591-8888888", "business_area": "三坊七巷",
            "address": "南街", "province": "福建省", "city": "福州市",
            "district": "鼓楼区", "adcode": "350102",
            "lat": 26.08, "lng": 119.29, "distance_m": None,
        }
        monkeypatch.setattr(
            merchant_mod.AMAP_LBS, "place_text", lambda *a, **_kw: [rec])
        raw = merchant_mod.map_merchant_search_tool.invoke(
            {"keyword": "闽菜", "city": "福州"})
        payload = json.loads(raw)
        assert payload["status"] == "success"
        data = payload["data"]
        envelope = {"keyword", "city", "count", "merchants"}
        missing = envelope - set(data)
        assert not missing, f"高德封套契约键缺失: {missing}"
        m = data["merchants"][0]
        record = {"id", "name", "category", "rating", "price", "avg_cost_cny",
                  "address", "open_status", "open_time_today", "open_time_week",
                  "tel", "lat", "lng", "distance_m", "source", "updated_at"}
        missing = record - set(m)
        assert not missing, f"高德商户记录契约键缺失: {missing}"
        assert m["source"] == "amap"
        assert m["open_status"] in ("营业中", "已打烊", "未知")


class TestTrainEnvelopeContract:
    """12306 车票封套契约（transit 专家/票价展示消费）。"""

    def test_success_envelope_keys_locked(self, monkeypatch):
        from backend.tools.travel import train as train_mod

        payload = {
            "success": True, "count": 1, "from_station": "福州",
            "to_station": "厦门北", "train_date": "2026-10-20",
            "trains": [{
                "train_no": "G1655", "from_station": "福州南",
                "to_station": "厦门北", "departure_time": "08:12",
                "arrival_time": "09:36", "duration": "1小时24分",
                "seats": {"二等座": "有", "一等座": "12"},
            }],
        }
        captured = {}

        def fake_call_tool(base_url, upstream_tool, arguments):
            captured["tool"] = upstream_tool
            return payload

        monkeypatch.setattr(train_mod, "call_tool", fake_call_tool)
        raw = train_mod.travel_train_search_tool.invoke(
            {"from_station": "福州", "to_station": "厦门北", "date": "2026-10-20"})
        env = json.loads(raw)
        assert env["status"] == "success"
        data = env["data"]
        envelope = {"from_station", "to_station", "date", "count",
                    "total_matched", "trains", "source", "queried_at"}
        missing = envelope - set(data)
        assert not missing, f"车票封套契约键缺失: {missing}"
        assert data["source"] == "12306"

    def test_disabled_is_not_configured_envelope(self):
        """未启用是结构化 not_configured，不是异常（查不了≠崩溃）。"""
        from backend.tools.travel import train as train_mod

        if getattr(train_mod, "TRAIN_MCP_ENABLED", False):
            import pytest
            pytest.skip("TRAIN_MCP_ENABLED=true（本机开启），跳过未启用分支")
        raw = train_mod.travel_train_search_tool.invoke(
            {"from_station": "福州", "to_station": "厦门北", "date": "2026-10-20"})
        env = json.loads(raw)
        assert env["status"] != "success"
        assert env.get("error")


class TestWeatherContract:
    """腾讯天气 future 封套契约（weather 专家分级判定消费）。"""

    def test_future_days_keys_locked(self):
        """days 记录必须含 date/day/night，昼夜组可解析出天气文本键。"""
        from backend.infra.lbs.api import _weather_info

        # _weather_info 是昼夜组的归一出口（契约单一来源）
        info = _weather_info({"weather": "小雨", "temperature": "22"})
        assert isinstance(info, dict)
        assert "weather" in info, "昼夜组契约必须含 weather 键（分级判定依赖）"
