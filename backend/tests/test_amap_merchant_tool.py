"""tests/test_amap_merchant_tool.py — 高德商家检索 Tool 的离线单测

**全部不联网**：高德 HTTP 调用被替换为固定响应（2026-10-02 实测结构），
验证的是本层的「解析 / 归一 / 营业状态推导 / 封套语义」。真实网络行为由
实机验收覆盖（tools 层真实 key 走查）。

重点覆盖三类「静默错误」——它们不抛异常，只会让结果悄悄错掉：
  1. 经纬度顺序：高德给「经度,纬度」，写反不报错，只会把点画到几百公里外
  2. 「没数据」与「0」混同：评分/人均缺失必须落 None，落 0 会变成
     「人均 0 元」这种假事实
  3. 「查不到」与「查不了」混同：空结果与调用失败走不同封套
"""
from __future__ import annotations

import json
from datetime import time as dt_time

import pytest

from backend.config import map as MAP
from backend.infra.http import amap as AMAP_HTTP
from backend.infra.lbs import amap as AMAP_LBS
from backend.tools.map import merchant as M
from backend.tools.map.lookup import ACTIONS, map_lookup_tool
from backend.tools.tool_registry import tool_registry


@pytest.fixture
def configured(monkeypatch):
    """临时把高德视为「已配置」（conftest 默认全局禁用了它）。"""
    monkeypatch.setattr(MAP, "AMAP_ENABLED", True)
    monkeypatch.setattr(MAP, "AMAP_KEY", "TEST-KEY-0000")
    monkeypatch.setattr(MAP, "AMAP_SECRET", "")
    monkeypatch.setattr(MAP, "AMAP_CACHE_ENABLED", False)
    monkeypatch.setattr(MAP, "AMAP_MIN_INTERVAL", 0.0)
    yield


# 2026-10-02 实测响应裁剪（海底捞·东百中心店 / 三坊七巷）
_REAL_POI_RESTAURANT = {
    "parent": "B0FFL7ZFBO",
    "address": "杨桥东路8号东百中心B馆7楼",
    "business": {
        "opentime_today": "10:00-07:00",
        "cost": "120.00",
        "keytag": "火锅",
        "rating": "4.7",
        "business_area": "鼓西",
        "tel": "0591-38065459",
        "tag": "虾滑,澳洲肥牛",
        "rectag": "火锅",
        "opentime_week": "周一至周日 10:00-07:00",
    },
    "distance": "",
    "pcode": "350000",
    "adcode": "350102",
    "pname": "福建省",
    "cityname": "福州市",
    "type": "餐饮服务;中餐厅;火锅店",
    "typecode": "050117",
    "adname": "鼓楼区",
    "citycode": "0591",
    "name": "海底捞火锅(东百中心店)",
    "location": "119.298447,26.086311",
    "id": "B0FFF9X950",
}
_REAL_POI_SCENIC = {
    "address": "南后街139号",
    "business": {
        "opentime_today": "08:30-22:00",
        "keytag": "5A景区",
        "rating": "4.8",
        "business_area": "南街",
        "tel": "0591-83890057",
        "rectag": "白墙瓦屋坊巷纵横",
        "opentime_week": "周一至周日 08:30-22:00",
    },
    "distance": "",
    "adcode": "350102",
    "pname": "福建省",
    "cityname": "福州市",
    "type": "风景名胜;风景名胜;国家级景点",
    "typecode": "140000",
    "adname": "鼓楼区",
    "name": "三坊七巷",
    "location": "119.296623,26.081958",
    "id": "B0F00I8Y90",
}


# =============================================
# 1. 归一（纯函数）
# =============================================
class TestNormalizeMerchant:
    def test_coord_swapped_to_platform_order(self):
        """高德「经度,纬度」必须换向为平台 (lat, lng)。"""
        rec = AMAP_LBS.normalize_merchant(_REAL_POI_RESTAURANT)
        assert rec["lat"] == pytest.approx(26.086311)
        assert rec["lng"] == pytest.approx(119.298447)

    def test_rating_and_cost_parsed(self):
        rec = AMAP_LBS.normalize_merchant(_REAL_POI_RESTAURANT)
        assert rec["rating"] == pytest.approx(4.7)
        assert rec["avg_cost_cny"] == pytest.approx(120.0)

    def test_missing_cost_is_none_not_zero(self):
        """景点无 cost：缺数据必须落 None，落 0 会变成「人均 0 元」假事实。"""
        rec = AMAP_LBS.normalize_merchant(_REAL_POI_SCENIC)
        assert rec["rating"] == pytest.approx(4.8)
        assert rec["avg_cost_cny"] is None
        # cost 键完全缺失的形态（部分 POI 连 rating 都没有）
        bare = AMAP_LBS.normalize_merchant({"name": "公交站", "location": "119.2,26.0"})
        assert bare["rating"] is None and bare["avg_cost_cny"] is None

    def test_region_fields(self):
        rec = AMAP_LBS.normalize_merchant(_REAL_POI_RESTAURANT)
        assert rec["province"] == "福建省"
        assert rec["city"] == "福州市"
        assert rec["district"] == "鼓楼区"
        assert rec["adcode"] == "350102"
        assert rec["category"] == "餐饮服务;中餐厅;火锅店"

    def test_distance_parsed_when_nearby(self):
        raw = dict(_REAL_POI_RESTAURANT, distance="356")
        assert AMAP_LBS.normalize_merchant(raw)["distance_m"] == 356
        assert AMAP_LBS.normalize_merchant(_REAL_POI_RESTAURANT)["distance_m"] is None


# =============================================
# 2. 营业状态推导（纯函数）
# =============================================
class TestOpenStatus:
    def test_normal_span(self):
        assert M.open_status("09:00-20:00", dt_time(10, 0)) == "营业中"
        assert M.open_status("09:00-20:00", dt_time(21, 0)) == "已打烊"
        # 边界：开点即营业，闭点即打烊
        assert M.open_status("09:00-20:00", dt_time(9, 0)) == "营业中"
        assert M.open_status("09:00-20:00", dt_time(20, 0)) == "已打烊"

    def test_midnight_span(self):
        """跨午夜是火锅店常态（实测高德给 "10:00-07:00"）。"""
        assert M.open_status("10:00-07:00", dt_time(23, 30)) == "营业中"
        assert M.open_status("10:00-07:00", dt_time(2, 0)) == "营业中"
        assert M.open_status("10:00-07:00", dt_time(8, 0)) == "已打烊"

    def test_multi_segments(self):
        assert M.open_status("11:00-14:00,17:00-22:00", dt_time(12, 0)) == "营业中"
        assert M.open_status("11:00-14:00,17:00-22:00", dt_time(15, 0)) == "已打烊"
        assert M.open_status("11:00-14:00,17:00-22:00", dt_time(18, 0)) == "营业中"

    def test_24h(self):
        assert M.open_status("24小时营业", dt_time(3, 0)) == "营业中"

    def test_unknown_when_no_usable_data(self):
        assert M.open_status("", dt_time(10, 0)) == "未知"
        # 有文案但不是时段格式：不猜
        assert M.open_status("营业时间暂无", dt_time(10, 0)) == "未知"

    def test_haversine_sanity(self):
        """纬度差 0.001° ≈ 111m；经度差按纬度余弦收缩。"""
        d = M._haversine_m(26.0, 119.3, 26.001, 119.3)
        assert 110 <= d <= 112
        # 高德 v5 不回 distance（恒空串），周边模式的距离必须本地算成立
        assert M._haversine_m(26.0, 119.3, 26.0, 119.3) == 0


# =============================================
# 3. 门面 place_text（mock 传输层）
# =============================================
class TestPlaceTextFacade:
    @pytest.fixture
    def capture(self, monkeypatch):
        calls: list[tuple[str, dict]] = []

        def _fake(path, params=None, *, ttl=None):
            calls.append((path, dict(params or {})))
            return {"status": "1", "info": "OK", "infocode": "10000",
                    "count": "1", "pois": [_REAL_POI_RESTAURANT]}

        monkeypatch.setattr(AMAP_HTTP, "call_sync", _fake)
        return calls

    def test_success_returns_normalized_list(self, configured, capture):
        out = AMAP_LBS.place_text("海底捞", region="福州市")
        assert out and out[0]["name"] == "海底捞火锅(东百中心店)"

    def test_params_carry_business_fields_and_defaults(self, configured, capture):
        AMAP_LBS.place_text("海底捞")
        path, params = capture[-1]
        assert path == AMAP_HTTP.EP_PLACE_TEXT
        assert params["show_fields"] == "business"
        # 未给 region 时兜底默认城市，防全国噪声
        assert params["region"] == MAP.AMAP_DEFAULT_REGION
        assert params["city_limit"] == "true"

    def test_location_swapped_for_amap(self, configured, capture):
        """平台 (lat, lng) → 高德「经度,纬度」，写反点就飞到几百公里外。"""
        AMAP_LBS.place_text("咖啡", location=(26.086311, 119.298447), radius=1000)
        _, params = capture[-1]
        assert params["location"] == "119.298447,26.086311"
        assert params["radius"] == 1000  # 字符串化发生在 HTTP 层 _as_pairs

    def test_error_maps_to_none_not_exception(self, configured, monkeypatch):
        def _fail(path, params=None, *, ttl=None):
            raise AMAP_HTTP.AmapError("高德 API 错误 10003: TAKE_TOO_FREQUENT",
                                      infocode="10003")

        monkeypatch.setattr(AMAP_HTTP, "call_sync", _fail)
        assert AMAP_LBS.place_text("海底捞") is None

    def test_empty_pois_maps_to_empty_list(self, configured, monkeypatch):
        monkeypatch.setattr(AMAP_HTTP, "call_sync",
                            lambda path, params=None, *, ttl=None:
                            {"status": "1", "pois": []})
        assert AMAP_LBS.place_text("海底捞") == []


# =============================================
# 4. Tool 层（mock 门面）
# =============================================
def _ok_envelope(out: str) -> dict:
    data = json.loads(out)
    assert data["status"] == "success", data
    return data["data"]


def _fail_envelope(out: str) -> dict:
    data = json.loads(out)
    assert data["status"] == "failed", data
    return data


class TestMerchantTool:
    def test_registered(self):
        assert "map_merchant_search_tool" in tool_registry.tool_names

    def test_not_configured_hint_names_amap_key(self, monkeypatch):
        monkeypatch.setattr(MAP, "AMAP_ENABLED", True)
        monkeypatch.setattr(MAP, "AMAP_KEY", "")
        out = _fail_envelope(M.map_merchant_search_tool.func(keyword="海底捞"))
        assert "高德" in out["error"]
        assert "AMAP_KEY" in out["hint"]

    def test_empty_keyword_rejected(self, configured):
        out = _fail_envelope(M.map_merchant_search_tool.func(keyword="  "))
        assert "keyword" in out["error"]

    def test_bad_near_coord_rejected(self, configured):
        out = _fail_envelope(M.map_merchant_search_tool.func(
            keyword="咖啡", near="不是坐标"))
        assert "near" in out["error"]

    def test_success_carries_all_contract_fields(self, configured, monkeypatch):
        monkeypatch.setattr(AMAP_LBS, "place_text",
                            lambda *a, **kw: [AMAP_LBS.normalize_merchant(
                                _REAL_POI_RESTAURANT)])
        data = _ok_envelope(M.map_merchant_search_tool.func(keyword="海底捞",
                                                            city="福州"))
        assert data["count"] == 1
        m = data["merchants"][0]
        # 用户可见契约九字段：名称/品类/评分/价格/人均/地址/营业状态/来源/更新时间
        assert m["name"] == "海底捞火锅(东百中心店)"
        assert m["category"] == "餐饮服务;中餐厅;火锅店"
        assert m["rating"] == pytest.approx(4.7)
        assert m["price"] == "人均¥120"
        assert m["avg_cost_cny"] == pytest.approx(120.0)
        assert m["address"] == "福建省福州市鼓楼区杨桥东路8号东百中心B馆7楼"
        assert m["source"] == "amap"
        assert m["open_status"] in ("营业中", "已打烊", "未知")
        assert m["open_time_today"] == "10:00-07:00"
        # updated_at 是 ISO8601 带时区的检索时刻
        from datetime import datetime
        assert datetime.fromisoformat(m["updated_at"]).tzinfo is not None

    def test_missing_cost_price_is_null(self, configured, monkeypatch):
        """缺人均时 price/avg_cost_cny 显式 null，不编造数值。"""
        monkeypatch.setattr(AMAP_LBS, "place_text",
                            lambda *a, **kw: [AMAP_LBS.normalize_merchant(
                                _REAL_POI_SCENIC)])
        m = _ok_envelope(M.map_merchant_search_tool.func(keyword="三坊七巷",
                                                         city="福州"))["merchants"][0]
        assert m["price"] is None
        assert m["avg_cost_cny"] is None

    def test_no_result_is_success_with_note(self, configured, monkeypatch):
        """「查不到」≠「查不了」：空结果是成功封套 + note。"""
        monkeypatch.setattr(AMAP_LBS, "place_text", lambda *a, **kw: [])
        data = _ok_envelope(M.map_merchant_search_tool.func(keyword="不存在的店"))
        assert data["count"] == 0 and data["merchants"] == []
        assert "无匹配" in data["note"]

    def test_provider_failure_is_failed_envelope(self, configured, monkeypatch):
        """「查不了」：限流/网络走 failed 封套，让 LLM 换策略。"""
        monkeypatch.setattr(AMAP_LBS, "place_text", lambda *a, **kw: None)
        out = _fail_envelope(M.map_merchant_search_tool.func(keyword="海底捞"))
        assert "海底捞" in out["error"]

    def test_nearby_mode_computes_distance_locally(self, configured, monkeypatch):
        """周边模式 distance_m 由本地 haversine 填充（高德不回该字段）。"""
        monkeypatch.setattr(AMAP_LBS, "place_text",
                            lambda *a, **kw: [AMAP_LBS.normalize_merchant(
                                _REAL_POI_RESTAURANT)])
        m = _ok_envelope(M.map_merchant_search_tool.func(
            keyword="海底捞", near="26.086311,119.298447"))["merchants"][0]
        assert m["distance_m"] == 0  # 中心点即商家坐标
        # 非周边模式保持 None
        m2 = _ok_envelope(M.map_merchant_search_tool.func(
            keyword="海底捞", city="福州"))["merchants"][0]
        assert m2["distance_m"] is None


# =============================================
# 5. 聚合入口接线
# =============================================
class TestLookupAggregation:
    def test_action_registered_with_required_param(self):
        desc, required = ACTIONS["merchant_search"]
        assert required == ("keyword",)
        assert "商家" in desc

    def test_dispatch_returns_merchants(self, configured, monkeypatch):
        monkeypatch.setattr(AMAP_LBS, "place_text",
                            lambda *a, **kw: [AMAP_LBS.normalize_merchant(
                                _REAL_POI_RESTAURANT)])
        data = _ok_envelope(map_lookup_tool.invoke({
            "action": "merchant_search", "keyword": "海底捞", "city": "福州",
        }))
        assert data["merchants"][0]["name"] == "海底捞火锅(东百中心店)"

    def test_dispatch_missing_keyword_blocked_at_boundary(self, configured):
        out = json.loads(map_lookup_tool.invoke({"action": "merchant_search"}))
        assert out["status"] == "failed"
        assert "keyword" in out["error"]
