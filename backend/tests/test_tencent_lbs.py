"""tests/test_tencent_lbs.py — 腾讯位置服务接入的离线单测

**全部不联网**：腾讯的 HTTP 调用被替换为固定响应，验证的是本层的
「解析 / 归一 / 降级」逻辑，也就正是最容易写错、且出错了最不容易被发现的部分。

真实网络行为由 ``backend/scripts/verify_tencent_lbs.py`` 覆盖（实网端到端自检）。

重点覆盖三类「静默错误」——它们不会抛异常，只会让结果悄悄错掉：
  1. 单位口径：``/direction`` 用分钟、``/distance/matrix`` 用秒
  2. 坐标系：WGS-84 / GCJ-02 / BD-09 混用造成数百米偏移
  3. 经纬度顺序：腾讯要求「纬度,经度」，写反不会报错，只会把点画到几百公里外
"""
from __future__ import annotations

import pytest

from backend.config import map as MAP
from backend.infra.http import tencent_lbs as T
from backend.infra.lbs import api, geo


@pytest.fixture
def configured(monkeypatch):
    """临时把 LBS 视为「已配置」（conftest 默认全局禁用了它）。"""
    monkeypatch.setattr(MAP, "TENCENT_LBS_ENABLED", True)
    monkeypatch.setattr(MAP, "TENCENT_LBS_KEY", "TEST-KEY-0000")
    monkeypatch.setattr(MAP, "TENCENT_LBS_SK", "")
    monkeypatch.setattr(MAP, "TENCENT_LBS_CACHE_ENABLED", False)
    monkeypatch.setattr(MAP, "TENCENT_LBS_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(MAP, "TENCENT_LBS_FRONTEND_KEY", "")
    monkeypatch.setattr(MAP, "TENCENT_LBS_REFERER", "test-referer")
    yield


@pytest.fixture
def capture(monkeypatch):
    """拦截传输层，记录请求参数并返回预置响应。"""
    calls: list[tuple[str, object]] = []
    responses: dict[str, dict] = {}

    def _fake(path, params=None, *, ttl=None):
        calls.append((path, params))
        if path not in responses:
            raise AssertionError(f"测试未预置 {path} 的响应")
        return responses[path]

    monkeypatch.setattr(T, "call_sync", _fake)
    monkeypatch.setattr(T, "safe_call",
                        lambda path, params=None, **kw: _fake(path, params, **kw))
    return calls, responses


# =============================================
# 1. 坐标系（纯函数）
# =============================================
class TestCoordinateSystems:
    def test_gcj_round_trip_within_metres(self):
        """GCJ→WGS→GCJ 往返误差应在米级（近似反解的预期精度）。"""
        origin = (26.082410, 119.296820)
        back = geo.wgs84_to_gcj02(*geo.gcj02_to_wgs84(*origin))
        assert _metres(origin, back) < 3.0

    def test_gcj_offset_is_significant(self):
        """不做转换就用的后果要真实存在（否则这条纪律没必要写进文档）。"""
        origin = (26.082410, 119.296820)
        converted = geo.wgs84_to_gcj02(*origin)
        assert _metres(origin, converted) > 100

    def test_bd09_round_trip(self):
        origin = (26.082410, 119.296820)
        back = geo.bd09_to_gcj02(*geo.gcj02_to_bd09(*origin))
        assert _metres(origin, back) < 1.0

    def test_out_of_china_not_shifted(self):
        """境外不做偏移（GCJ-02 是国境内算法）。"""
        tokyo = (35.6762, 139.6503)
        assert geo.wgs84_to_gcj02(*tokyo) == tokyo

    @pytest.mark.parametrize("alias", ["wgs84", "WGS-84", "gps", "84"])
    def test_to_gcj02_aliases(self, alias):
        assert geo.to_gcj02(26.0, 119.0, alias) != (26.0, 119.0)

    def test_to_gcj02_gcj_is_identity(self):
        assert geo.to_gcj02(26.0, 119.0, "gcj02") == (26.0, 119.0)

    def test_to_gcj02_unknown_raises(self):
        with pytest.raises(ValueError, match="未知坐标系"):
            geo.to_gcj02(26.0, 119.0, "mercury")

    def test_parse_lat_lng_corrects_swapped_order(self):
        """开发者习惯「经度,纬度」，写反必须被纠正而不是静默出错。"""
        assert geo.parse_lat_lng("26.08,119.29") == (26.08, 119.29)
        assert geo.parse_lat_lng("119.29,26.08") == (26.08, 119.29)

    @pytest.mark.parametrize("bad", ["", "abc", "26.08", "26.08,abc", "1,2,3", "0,0"])
    def test_parse_lat_lng_rejects_garbage(self, bad):
        assert geo.parse_lat_lng(bad) is None

    def test_format_uses_lat_first(self):
        assert geo.format_lat_lng(26.082410, 119.296820) == "26.082410,119.296820"


# =============================================
# 2. 传输层：签名 / 参数 / 缓存 / 错误映射
# =============================================
class TestTransport:
    def test_not_configured_raises_status_190(self, monkeypatch):
        monkeypatch.setattr(MAP, "TENCENT_LBS_ENABLED", False)
        with pytest.raises(T.TencentLbsError) as ei:
            T.call_sync(api.__name__ and T.EP_IP_LOCATION)
        assert ei.value.status == 190
        assert ei.value.is_fatal

    def test_signature_is_deterministic_and_order_insensitive(self, configured):
        pairs_a = [("address", "福州"), ("region", "福州")]
        pairs_b = [("region", "福州"), ("address", "福州")]
        sig_a = T._sign(T.EP_GEOCODER, pairs_a)
        assert sig_a == T._sign(T.EP_GEOCODER, pairs_b), "参数顺序不应影响签名"
        assert len(sig_a) == 32 and sig_a.islower()

    def test_signature_changes_with_sk(self, configured, monkeypatch):
        pairs = [("address", "福州")]
        base = T._sign(T.EP_GEOCODER, pairs)
        monkeypatch.setattr(MAP, "TENCENT_LBS_SK", "SOME-SECRET")
        assert T._sign(T.EP_GEOCODER, pairs) != base

    def test_signature_changes_with_path(self, configured):
        pairs = [("address", "福州")]
        assert T._sign(T.EP_GEOCODER, pairs) != T._sign(T.EP_PLACE_SEARCH, pairs)

    def test_pairs_preserve_duplicate_keys(self):
        """静态图的 markers 需要同键多值，dict 表达不了。"""
        pairs = T._as_pairs([("markers", "a|1,1"), ("markers", "b|2,2")])
        assert pairs == [("markers", "a|1,1"), ("markers", "b|2,2")]

    def test_pairs_drop_empty_values(self):
        assert T._as_pairs({"a": "1", "b": None, "c": ""}) == [("a", "1")]

    def test_prepare_injects_key_and_sig(self, configured, monkeypatch):
        monkeypatch.setattr(MAP, "TENCENT_LBS_SK", "S")
        pairs = dict(T._prepare(T.EP_GEOCODER, {"address": "x"}))
        assert pairs["key"] == "TEST-KEY-0000"
        assert "sig" in pairs

    def test_cache_key_ignores_key_and_sig(self):
        a = T._cache_key("/p", [("key", "K1"), ("a", "1"), ("sig", "S1")])
        b = T._cache_key("/p", [("key", "K2"), ("a", "1"), ("sig", "S2")])
        assert a == b

    @pytest.mark.parametrize("status,quota", [(120, True), (121, True), (123, True),
                                             (110, False), (190, False)])
    def test_status_mapping(self, status, quota):
        err = T.TencentLbsError("x", status=status)
        assert err.is_quota is quota

    def test_parse_raises_on_nonzero_status(self):
        class Resp:
            status_code = 200
            headers = {"content-type": "application/json"}

            @staticmethod
            def json():
                return {"status": 121, "message": "限流", "request_id": "rid-1"}

        with pytest.raises(T.TencentLbsError) as ei:
            T._parse(Resp())
        assert ei.value.status == 121
        assert ei.value.request_id == "rid-1"

    def test_parse_passes_through_on_success(self):
        class Resp:
            status_code = 200
            headers = {"content-type": "application/json"}

            @staticmethod
            def json():
                return {"status": 0, "result": {"ok": True}}

        assert T._parse(Resp())["result"] == {"ok": True}

    def test_cache_hit_avoids_second_request(self, configured, monkeypatch):
        hits = {"n": 0}

        class Resp:
            status_code = 200
            headers = {"content-type": "application/json"}

            @staticmethod
            def json():
                hits["n"] += 1
                return {"status": 0, "result": {"n": hits["n"]}}

        class Client:
            def get(self, url, params=None):
                return Resp()

        monkeypatch.setattr(MAP, "TENCENT_LBS_CACHE_ENABLED", True)
        monkeypatch.setattr(T, "_get_sync_client", lambda: Client())
        T.clear_cache()
        try:
            first = T.call_sync(T.EP_IP_LOCATION, {"ip": "1.1.1.1"})
            second = T.call_sync(T.EP_IP_LOCATION, {"ip": "1.1.1.1"})
            assert first == second
            assert hits["n"] == 1, "第二次应命中缓存，不应再发请求"
        finally:
            T.clear_cache()

    def test_safe_call_swallows_errors(self, monkeypatch):
        monkeypatch.setattr(T, "call_sync",
                            lambda *a, **k: (_ for _ in ()).throw(
                                T.TencentLbsError("boom", status=121)))
        assert T.safe_call(T.EP_IP_LOCATION) is None

    def test_fatal_status_not_retried(self, configured, monkeypatch):
        """鉴权类错误重试没有意义，只会多烧配额。"""
        attempts = {"n": 0}

        def _boom(path, params=None):
            attempts["n"] += 1
            raise T.TencentLbsError("key 无效", status=190)

        class Client:
            def get(self, url, params=None):
                _boom(url, params)

        monkeypatch.setattr(T, "_get_sync_client", lambda: Client())
        monkeypatch.setattr(MAP, "TENCENT_LBS_RETRIES", 3)
        with pytest.raises(T.TencentLbsError):
            T.call_sync(T.EP_IP_LOCATION)
        assert attempts["n"] == 1


# =============================================
# 3. 能力门面：响应归一
# =============================================
class TestApiNormalization:
    def test_ip_location(self, configured, capture):
        _, responses = capture
        responses[T.EP_IP_LOCATION] = {
            "status": 0,
            "result": {"ip": "1.2.3.4", "location": {"lat": 26.07, "lng": 119.29},
                       "ad_info": {"province": "福建省", "city": "福州市", "adcode": 350100}},
        }
        r = api.ip_location()
        assert r["city"] == "福州市" and r["adcode"] == "350100"

    def test_geocode_flattens_address_components(self, configured, capture):
        _, responses = capture
        responses[T.EP_GEOCODER] = {
            "status": 0,
            "result": {
                "title": "某地", "location": {"lat": 26.0, "lng": 119.0},
                "ad_info": {"province": "福建省", "city": "福州市", "district": "鼓楼区"},
                "address_components": {"province": "福建省", "city": "福州市",
                                       "district": "鼓楼区", "street": "南后街",
                                       "street_number": "139"},
                "similarity": 0.99, "reliability": 7, "level": 4,
            },
        }
        r = api.geocode("南后街139号")
        assert r["formatted_address"] == "福建省福州市鼓楼区南后街139"
        assert r["reliability"] == 7

    def test_place_search_normalizes_poi(self, configured, capture):
        calls, responses = capture
        responses[T.EP_PLACE_SEARCH] = {
            "status": 0,
            "data": [{"id": 123, "title": "三坊七巷", "address": "南后街139号",
                      "category": "旅游景点:国家级景点", "tel": "0591",
                      "location": {"lat": 26.08, "lng": 119.29},
                      "ad_info": {"province": "福建省", "city": "福州市",
                                  "district": "鼓楼区", "adcode": 350102}}],
        }
        pois = api.place_search("三坊七巷", region="福州")
        assert pois[0]["id"] == "123" and pois[0]["name"] == "三坊七巷"
        # boundary 应带城市限定
        assert "region(福州,0)" in dict(calls[0][1])["boundary"]

    def test_place_search_nearby_switches_boundary(self, configured, capture):
        calls, responses = capture
        responses[T.EP_PLACE_SEARCH] = {"status": 0, "data": []}
        api.place_search("美食", near=(26.08, 119.29), radius=2000)
        boundary = dict(calls[0][1])["boundary"]
        assert boundary.startswith("nearby(26.080000,119.290000,2000)")

    def test_place_search_defaults_to_configured_region(self, configured, capture):
        """不给地域限定会被腾讯返回全国噪声结果，必须兜底。"""
        calls, responses = capture
        responses[T.EP_PLACE_SEARCH] = {"status": 0, "data": []}
        api.place_search("景点")
        assert dict(calls[0][1])["boundary"] == f"region({MAP.TENCENT_LBS_DEFAULT_REGION},0)"

    def test_page_size_clamped_to_api_limit(self, configured, capture):
        calls, responses = capture
        responses[T.EP_PLACE_SEARCH] = {"status": 0, "data": []}
        api.place_search("景点", region="福州", page_size=999)
        assert dict(calls[0][1])["page_size"] == MAP.TENCENT_LBS_MAX_PAGE_SIZE

    def test_failure_returns_none_but_empty_returns_list(self, configured, capture):
        """「查不了」与「查不到」必须可区分 —— 前者让 LLM 换策略，后者才改行程。"""
        _, responses = capture
        responses[T.EP_PLACE_SEARCH] = {"status": 0, "data": []}
        assert api.place_search("不存在的地方", region="福州") == []

        responses[T.EP_PLACE_SEARCH] = None  # 模拟失败
        assert api.place_search("x", region="福州") is None

    def test_district_flatten_and_short_name(self, configured, capture):
        """下级行政区只返回 fullname，必须补齐 name 而非留空。"""
        _, responses = capture
        responses[T.EP_DISTRICT_CHILDREN] = {
            "status": 0,
            "result": [[{"id": "350102", "fullname": "鼓楼区",
                         "location": {"lat": 26.08, "lng": 119.30}},
                        {"id": "350121", "fullname": "闽侯县",
                         "location": {"lat": 26.15, "lng": 119.13}}]],
        }
        kids = api.district_children("350100")
        assert [k["name"] for k in kids] == ["鼓楼", "闽侯"]

    def test_district_provinces_takes_first_group_only(self, configured, capture):
        _, responses = capture
        responses[T.EP_DISTRICT_LIST] = {
            "status": 0,
            "result": [
                [{"id": "110000", "name": "北京", "fullname": "北京市",
                  "location": {"lat": 39.9, "lng": 116.7}}],
                [{"id": "110101", "fullname": "东城区", "location": {"lat": 39.9, "lng": 116.4}}],
            ],
        }
        provinces = api.district_provinces()
        assert len(provinces) == 1 and provinces[0]["fullname"] == "北京市"

    def test_direction_normalizes_minutes_to_seconds(self, configured, capture):
        """腾讯 /direction 的 duration 单位是**分钟**。"""
        _, responses = capture
        responses[T.EP_DIRECTION.format(mode="driving")] = {
            "status": 0,
            "result": {"routes": [{
                "mode": "DRIVING", "distance": 4665, "duration": 16,
                "traffic_light_count": 8, "toll": 0,
                "taxi_fare": {"fare": 13},
                "steps": [{"instruction": "向东行驶15米", "road_name": "内部道路",
                           "distance": 15, "duration": 1}],
            }]},
        }
        r = api.direction("driving", 26.08, 119.29, 26.10, 119.31)
        assert r["duration_min"] == 16 and r["duration_s"] == 960
        assert r["distance_km"] == 4.665
        assert r["taxi_fare_cny"] == 13.0

    def test_matrix_normalizes_seconds_to_minutes(self, configured, capture):
        """腾讯 /distance/matrix 的 duration 单位是**秒**（与 direction 不一致）。"""
        _, responses = capture
        responses[T.EP_DISTANCE_MATRIX] = {
            "status": 0,
            "result": {"rows": [{"elements": [{"distance": 4440, "duration": 815},
                                              {"distance": 11535, "duration": 1480}]}]},
        }
        m = api.distance_matrix("driving", [(26.08, 119.29)], [(26.10, 119.31), (26.05, 119.38)])
        assert m[0][0] == {"distance_m": 4440, "duration_s": 815, "duration_min": 13.6}
        assert m[0][1]["duration_min"] == 24.7

    def test_direction_rejects_unknown_mode(self, configured):
        with pytest.raises(ValueError, match="不支持的出行方式"):
            api.direction("teleport", 26.0, 119.0, 26.1, 119.1)

    def test_matrix_rejects_transit(self, configured):
        with pytest.raises(ValueError, match="距离矩阵仅支持"):
            api.distance_matrix("transit", [(26.0, 119.0)], [(26.1, 119.1)])

    def test_direction_policy_validation(self, configured):
        with pytest.raises(ValueError, match="不支持的驾车策略"):
            api.direction("driving", 26.0, 119.0, 26.1, 119.1, policy="FLY")

    def test_coord_translate_caps_at_200_points(self, configured):
        with pytest.raises(ValueError, match="上限 200"):
            api.coord_translate([(26.0, 119.0)] * 201, from_type=1)

    def test_static_map_url_contains_key_for_server_side_use(self, configured):
        url = api.static_map_url(center=(26.08, 119.29), zoom=14)
        assert url.startswith(MAP.TENCENT_LBS_HOST)
        assert T.EP_STATIC_MAP in url and "key=TEST-KEY-0000" in url

    def test_static_map_url_returns_none_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(MAP, "TENCENT_LBS_ENABLED", False)
        assert api.static_map_url(center=(26.08, 119.29)) is None

    def test_markers_grouped_by_style(self, configured):
        params = api._markers_param([
            {"lat": 26.0, "lng": 119.0, "color": "red", "label": "A"},
            {"lat": 26.1, "lng": 119.1, "color": "red", "label": "A"},
            {"lat": 26.2, "lng": 119.2, "color": "blue", "label": "B"},
        ])
        assert len(params) == 2, "同样式应合并为同一个 markers 参数"
        values = dict(params)
        assert values["markers"].count("|") >= 3

    def test_composite_marker_cap(self, configured):
        params = api._markers_param([{"lat": 26.0, "lng": 119.0}] * 60)
        total = sum(v.count("|") - 1 for _, v in params)
        assert total <= 50

    def test_capability_report_hides_key(self, configured):
        report = api.capability_report()
        assert report["configured"] is True
        assert "TEST-KEY-0000" not in str(report)


# =============================================
# 4. 天气
# =============================================
class TestWeather:
    def test_now_normalizes_realtime(self, configured, capture):
        _, responses = capture
        responses[T.EP_WEATHER] = {
            "status": 0,
            "result": {"realtime": [{
                "province": "福建省", "city": "福州市", "district": "鼓楼区",
                "adcode": 350102, "update_time": "2026-09-14 13:15",
                "infos": {"weather": "多云", "temperature": 28, "wind_direction": "东北风",
                          "wind_power": "1-2级", "humidity": 71, "air_pressure": 1005},
            }]},
        }
        r = api.weather(location=(26.0824, 119.2968), kind="now")
        assert r["kind"] == "now" and r["district"] == "鼓楼区"
        assert r["current"]["weather"] == "多云" and r["current"]["temperature"] == 28

    def test_future_handles_infos_as_list(self, configured, capture):
        """`now` 的 infos 是 dict，`future` 是 list —— 同名字段两种类型。"""
        _, responses = capture
        responses[T.EP_WEATHER] = {
            "status": 0,
            "result": {"forecast": [{
                "province": "福建省", "city": "福州市", "adcode": 350100,
                "infos": [{
                    "date": "2026-09-14", "week": "星期一",
                    "day": {"weather": "小雨", "temperature": 31},
                    "night": {"weather": "多云", "temperature": 24},
                }],
            }]},
        }
        r = api.weather(adcode="350100", kind="future")
        assert len(r["days"]) == 1
        assert r["days"][0]["day"]["weather"] == "小雨"
        assert r["days"][0]["night"]["temperature"] == 24

    def test_hours_normalizes_hourly(self, configured, capture):
        _, responses = capture
        responses[T.EP_WEATHER] = {
            "status": 0,
            "result": {"forecast_hours": [{
                "city": "厦门市", "infos": [
                    {"hour": "2026-09-14 12:00:00",
                     "info": {"weather": "晴天", "temperature": 31, "wind_power": "微风"}},
                ],
            }]},
        }
        r = api.weather(adcode="350200", kind="hours")
        assert r["hours"][0]["hour"] == "2026-09-14 12:00:00"
        assert r["hours"][0]["temperature"] == 31

    def test_location_param_prefers_coordinates(self, configured, capture):
        calls, responses = capture
        responses[T.EP_WEATHER] = {"status": 0, "result": {"realtime": [{}]}}
        api.weather(location=(26.0824, 119.2968), kind="now")
        params = dict(calls[0][1])
        assert params["location"] == "26.082400,119.296800"
        assert "adcode" not in params

    def test_requires_one_locator(self, configured):
        with pytest.raises(ValueError, match="至少提供一个"):
            api.weather(kind="now")

    def test_rejects_unknown_kind(self, configured):
        with pytest.raises(ValueError, match="不支持的天气类型"):
            api.weather(adcode="350100", kind="tomorrow")

    def test_failure_returns_none(self, configured, capture):
        _, responses = capture
        responses[T.EP_WEATHER] = None
        assert api.weather(adcode="350100") is None

    def test_weather_for_city_resolves_to_coordinates(self, configured, monkeypatch):
        """先解析行政区拿中心点，再用坐标查 —— 比直接传 adcode 多一级精度。"""
        seen = {}
        monkeypatch.setattr(api, "resolve_district",
                            lambda c: {"id": "350100", "lat": 26.074, "lng": 119.296})
        monkeypatch.setattr(api, "weather",
                            lambda **kw: seen.update(kw) or {"kind": "now"})
        api.weather_for_city("福州", kind="now")
        assert seen["location"] == (26.074, 119.296)


# =============================================
# 5. 街景
# =============================================
class TestStreetView:
    def test_113_is_not_swallowed(self, configured, monkeypatch):
        """街景失败必须抛异常：原因是「要去申请开通」，静默返回 None 会被
        误解成「这个坐标没有街景」。"""
        def _raise(path, params=None, *, ttl=None):
            raise T.TencentLbsError("腾讯 LBS 错误 113: 此功能未被授权", status=113)

        monkeypatch.setattr(T, "call_sync", _raise)
        with pytest.raises(T.TencentLbsError) as ei:
            api.street_view_pano(26.0824, 119.2968)
        assert ei.value.status == 113

    def test_pano_normalization(self, configured, capture):
        _, responses = capture
        responses[T.EP_STREETVIEW_PANO] = {
            "status": 0,
            "result": {"pano": "100110261003111", "location": {"lat": 26.0825, "lng": 119.2970},
                       "description": "三坊七巷南后街"},
        }
        pano = api.street_view_pano(26.0824, 119.2968)
        assert pano["pano"] == "100110261003111"
        assert pano["description"] == "三坊七巷南后街"

    def test_pano_tolerates_missing_location(self, configured, capture):
        """未实测过的响应结构，缺字段时回退到入参坐标而不是抛 KeyError。"""
        _, responses = capture
        responses[T.EP_STREETVIEW_PANO] = {"status": 0, "result": {"pano": "X"}}
        pano = api.street_view_pano(26.0824, 119.2968)
        assert (pano["lat"], pano["lng"]) == (26.0824, 119.2968)

    def test_image_bytes_returns_none_on_failure(self, configured, monkeypatch):
        monkeypatch.setattr(T, "call_bytes_sync",
                            lambda *a, **k: (_ for _ in ()).throw(
                                T.TencentLbsError("x", status=113)))
        assert api.street_view_image_bytes("X") is None

    def test_image_bytes_requires_pano(self, configured):
        assert api.street_view_image_bytes("") is None

    def test_apply_hint_registered_for_streetview_paths(self):
        assert "mapapi@vip.qq.com" in T.apply_hint_for(T.EP_STREETVIEW_PANO)
        assert "mapapi@vip.qq.com" in T.apply_hint_for(T.EP_STREETVIEW_IMAGE)
        # 其他端点不应带街景的专属指引
        assert T.apply_hint_for(T.EP_WEATHER) == ""

    def test_113_message_prefers_apply_hint_over_generic_advice(self):
        """泛化的「去控制台勾选」与申请制指引同时出现会互相打架。"""
        class Resp:
            status_code = 200
            headers = {"content-type": "application/json"}

            @staticmethod
            def json():
                return {"status": 113, "message": "此功能未被授权"}

        with pytest.raises(T.TencentLbsError) as ei:
            T._parse(Resp(), T.EP_STREETVIEW_PANO)
        msg = str(ei.value)
        assert "mapapi@vip.qq.com" in msg
        assert "为 Key 勾选对应服务" not in msg

        # 非申请制端点仍走通用提示
        with pytest.raises(T.TencentLbsError) as ei2:
            T._parse(Resp(), T.EP_WEATHER)
        assert "为 Key 勾选对应服务" in str(ei2.value)


# =============================================
# 6. 导航调起（URI API）
# =============================================
class TestNavigationUri:
    def test_backend_key_fallback_carries_warning(self, configured):
        r = api.navigation_uri(to_lat=26.049, to_lng=119.3896, to_name="鼓山")
        assert r["key_kind"] == "backend"
        assert "TEST-KEY-0000" in r["url"]
        assert r["warning"] and "不要直接返回给浏览器" in r["warning"]

    def test_frontend_key_used_when_configured(self, configured, monkeypatch):
        monkeypatch.setattr(MAP, "TENCENT_LBS_FRONTEND_KEY", "FRONT-KEY-9999")
        r = api.navigation_uri(to_lat=26.049, to_lng=119.3896)
        assert r["key_kind"] == "frontend"
        assert "FRONT-KEY-9999" in r["url"]
        assert "TEST-KEY-0000" not in r["url"]
        assert "warning" not in r

    def test_url_shape_and_referer(self, configured):
        r = api.navigation_uri(to_lat=26.049, to_lng=119.3896, to_name="鼓山",
                               from_lat=26.0824, from_lng=119.2968, from_name="三坊七巷")
        assert r["url"].startswith(MAP.TENCENT_LBS_HOST + T.EP_URI_ROUTEPLAN)
        assert "referer=test-referer" in r["url"]
        assert "fromcoord=26.082400%2C119.296800" in r["url"]
        assert "tocoord=26.049000%2C119.389600" in r["url"]

    def test_omits_from_when_not_given(self, configured):
        """不传起点 = 以用户当前位置为起点，这是 URI API 的设计意图。"""
        r = api.navigation_uri(to_lat=26.049, to_lng=119.3896)
        assert "fromcoord" not in r["url"]

    def test_policy_only_for_drive(self, configured):
        drive = api.navigation_uri(to_lat=26.0, to_lng=119.0, mode="drive", policy=1)
        walk = api.navigation_uri(to_lat=26.0, to_lng=119.0, mode="walk", policy=1)
        assert "policy=1" in drive["url"] and drive["policy"] == "避免拥堵"
        assert "policy=" not in walk["url"] and walk["policy"] == ""

    @pytest.mark.parametrize("mode", ["fly", "teleport"])
    def test_rejects_unknown_mode(self, configured, mode):
        with pytest.raises(ValueError, match="不支持的调起方式"):
            api.navigation_uri(to_lat=26.0, to_lng=119.0, mode=mode)

    def test_empty_mode_coerces_to_drive(self, configured):
        """API 层对缺省宽松（空串→drive），严格校验由工具层承担 —— 这是本层
        与 tools/map 的既定分工，写成用例以免日后被当成 bug 改掉。"""
        assert api.navigation_uri(to_lat=26.0, to_lng=119.0, mode="")["mode"] == "drive"
        # 工具层则必须报错：LLM 传了空值说明它没想清楚，不能替它猜
        from backend.tools.map import route as map_route

        assert "error" in map_route.map_navigation_tool.invoke(
            {"to_location": "26.0,119.0", "mode": "fly"})

    def test_rejects_unknown_policy(self, configured):
        with pytest.raises(ValueError, match="不支持的驾车策略"):
            api.navigation_uri(to_lat=26.0, to_lng=119.0, policy=9)

    def test_requires_key(self, monkeypatch):
        monkeypatch.setattr(MAP, "TENCENT_LBS_ENABLED", False)
        with pytest.raises(T.TencentLbsError):
            api.navigation_uri(to_lat=26.0, to_lng=119.0)


# =============================================
# 7. 服务探活
# =============================================
class TestServiceAvailability:
    def test_probe_reports_each_service_independently(self, configured, monkeypatch):
        """单个服务异常不应让整份探活结果失败。"""
        monkeypatch.setattr(api, "weather", lambda **k: {"kind": "now"})
        monkeypatch.setattr(api, "street_view_pano",
                            lambda *a, **k: (_ for _ in ()).throw(
                                T.TencentLbsError("街景未开通", status=113)))
        monkeypatch.setattr(api, "resolve_district", lambda c: {"id": "350100"})
        monkeypatch.setattr(api, "direction", lambda *a, **k: {"distance_km": 1.0})

        report = api.service_availability()
        assert report["天气"]["ok"] is True
        assert report["街景"]["ok"] is False and "未开通" in report["街景"]["detail"]
        assert report["行政区划"]["ok"] is True

    def test_capability_report_flags_frontend_key(self, configured, monkeypatch):
        assert api.capability_report()["frontend_key"] is False
        monkeypatch.setattr(MAP, "TENCENT_LBS_FRONTEND_KEY", "F")
        assert api.capability_report()["frontend_key"] is True


# =============================================
# 8. 旅行域接入
# =============================================
class TestTravelLiveMap:
    def test_map_category(self):
        from backend.tools.travel.live_map import map_category
        from backend.travel.models.poi import (
            CATEGORY_MEAL, CATEGORY_NIGHT, CATEGORY_PARK, CATEGORY_SHOPPING, CATEGORY_VISIT,
        )

        assert map_category("旅游景点:国家级景点") == CATEGORY_VISIT
        assert map_category("餐饮服务:餐厅") == CATEGORY_MEAL
        assert map_category("购物服务:商场") == CATEGORY_SHOPPING
        assert map_category("公园广场:公园") == CATEGORY_PARK
        assert map_category("娱乐场所:酒吧") == CATEGORY_NIGHT
        assert map_category("") == CATEGORY_VISIT

    def test_live_leg_returns_estimate_leg_shape(self, monkeypatch):
        from backend.tools.travel import live_map

        monkeypatch.setattr(live_map, "is_enabled", lambda: True)
        monkeypatch.setattr(live_map.api, "direction", lambda *a, **k: {
            "distance_km": 12.5, "duration_min": 31.0, "taxi_fare_cny": 28.0,
        })
        leg = live_map.live_leg(26.08, 119.29, 26.20, 119.40)
        assert set(leg) == {"distance_km", "mode", "minutes", "cost_cny", "source"}
        assert leg["minutes"] == 31 and leg["source"] == "tencent:lbs"
        assert leg["cost_cny"] == 28.0, "应优先采用腾讯真实计程车价"

    def test_live_leg_walking_is_free(self, monkeypatch):
        from backend.tools.travel import live_map

        monkeypatch.setattr(live_map, "is_enabled", lambda: True)
        monkeypatch.setattr(live_map.api, "direction", lambda *a, **k: {
            "distance_km": 0.8, "duration_min": 11.0, "taxi_fare_cny": None,
        })
        leg = live_map.live_leg(26.0800, 119.2968, 26.0830, 119.2990)
        assert leg["mode"] == "walk" and leg["cost_cny"] == 0.0

    def test_live_leg_returns_none_when_disabled(self, monkeypatch):
        from backend.tools.travel import live_map

        monkeypatch.setattr(live_map, "is_enabled", lambda: False)
        assert live_map.live_leg(26.0, 119.0, 26.1, 119.1) is None

    def test_live_leg_applies_minimum_floor(self, monkeypatch):
        from backend.tools.travel import live_map

        monkeypatch.setattr(live_map, "is_enabled", lambda: True)
        monkeypatch.setattr(live_map.api, "direction", lambda *a, **k: {
            "distance_km": 0.05, "duration_min": 0.0, "taxi_fare_cny": None,
        })
        leg = live_map.live_leg(26.0800, 119.2968, 26.0801, 119.2969)
        assert leg["minutes"] >= 5, "纯公式会算出 0 分钟这种不现实的值"

    def test_resolve_missing_places_skips_existing(self, monkeypatch):
        from backend.tools.travel import live_map
        from backend.travel.models.poi import Poi

        called: list[str] = []
        monkeypatch.setattr(live_map, "resolve_place",
                            lambda name, city, **kw: called.append(name))
        existing = Poi(poi_id="a", name="三坊七巷", city="福州", lat=26.08, lng=119.29)
        added, notes = live_map.resolve_missing_places("福州", [existing], ["三坊七巷"])
        assert added == [] and notes == [] and called == []

    def test_resolve_missing_places_reports_added(self, monkeypatch):
        from backend.tools.travel import live_map
        from backend.travel.models.poi import Poi

        fake = Poi(poi_id="lbs_1", name="平潭岛", city="福州", lat=25.5, lng=119.8,
                   required=True, source="tencent:lbs")
        monkeypatch.setattr(live_map, "resolve_place", lambda name, city, **kw: fake)
        added, notes = live_map.resolve_missing_places("福州", [], ["平潭岛"])
        assert [p.name for p in added] == ["平潭岛"]
        assert "腾讯位置服务" in notes[0] and "未核实" in notes[0]

    def test_resolved_must_go_is_marked_required(self, monkeypatch):
        """回归：补入的 POI 若不标 required，骨架分配（按 required 优先）会把它
        排在所有种子 POI 之后，被节奏容量挤掉 —— 用户点名的地点就消失了。
        实测踩过：用户说「想去平潭岛」，补入成功但 day_plan 里没有它。"""
        from backend.tools.travel import live_map

        seen: dict = {}
        monkeypatch.setattr(
            live_map, "resolve_place",
            lambda name, city, **kw: seen.update(kw) or None)
        live_map.resolve_missing_places("福州", [], ["平潭岛"])
        assert seen.get("required") is True

    def test_skeleton_prioritises_resolved_must_go(self):
        """把「required 优先」这条契约钉住：补入项必须排在热度高的种子项之前。"""
        from backend.travel.experts.poi import build_skeleton
        from backend.travel.models.brief import TravelBrief
        from backend.travel.models.poi import Poi

        must = Poi(poi_id="lbs_1", name="平潭岛", city="福州", lat=25.5, lng=119.8,
                   required=True, suggested_minutes=120, source="tencent:lbs")
        popular = Poi(poi_id="fz_x", name="热门景点", city="福州", lat=26.08, lng=119.29,
                      required=False, rating=5.0, suggested_minutes=90)
        brief = TravelBrief(destination="福州", days=1, pace="relaxed")
        skeleton = build_skeleton(brief, [popular, must])
        assert "平潭岛" in [p.name for p in skeleton.days[0]]

    def test_resolve_place_rejects_far_away_hits(self, monkeypatch):
        """用户说「福州的土楼」时不应把 250km 外的永定土楼塞进行程。"""
        from backend.tools.travel import live_map

        monkeypatch.setattr(live_map, "is_enabled", lambda: True)
        monkeypatch.setattr(live_map, "_city_center", lambda city: (26.074, 119.296))
        monkeypatch.setattr(live_map.api, "place_search", lambda *a, **k: [
            {"id": "1", "name": "永定土楼", "lat": 24.6, "lng": 116.9,
             "city": "龙岩", "category": "旅游景点"},
        ])
        assert live_map.resolve_place("土楼", "福州") is None

    def test_resolve_place_accepts_nearby_hit(self, monkeypatch):
        from backend.tools.travel import live_map

        monkeypatch.setattr(live_map, "is_enabled", lambda: True)
        monkeypatch.setattr(live_map, "_city_center", lambda city: (26.074, 119.296))
        monkeypatch.setattr(live_map.api, "place_search", lambda *a, **k: [
            {"id": "9", "name": "鼓岭", "lat": 26.076, "lng": 119.406,
             "city": "福州", "category": "旅游景点:其它旅游景点"},
        ])
        poi = live_map.resolve_place("鼓岭", "福州")
        assert poi is not None and poi.name == "鼓岭"
        assert poi.source == "tencent:lbs" and poi.poi_id == "lbs_9"

    def test_install_is_idempotent_and_reversible(self, monkeypatch):
        from backend.tools.travel import live_map, routing

        monkeypatch.setattr(live_map, "is_enabled", lambda: True)
        assert live_map.install_live_map() is True
        assert routing.get_route_provider() is live_map.live_leg
        monkeypatch.setattr(live_map, "is_enabled", lambda: False)
        assert live_map.install_live_map() is False
        assert routing.get_route_provider() is None


class TestRoutingProviderSeam:
    def test_provider_overrides_estimate(self):
        from backend.tools.travel import routing

        routing.set_route_provider(lambda *a: {
            "distance_km": 9.9, "mode": "drive", "minutes": 42,
            "cost_cny": 33.0, "source": "tencent:lbs",
        })
        try:
            leg = routing.estimate_leg(26.08, 119.29, 26.20, 119.40)
            assert leg["minutes"] == 42 and leg["source"] == "tencent:lbs"
        finally:
            routing.set_route_provider(None)

    def test_provider_returning_none_falls_back(self):
        from backend.tools.travel import routing

        routing.set_route_provider(lambda *a: None)
        try:
            leg = routing.estimate_leg(26.08, 119.29, 26.09, 119.30)
            assert leg["source"] == routing.SOURCE_LOCAL
            # Phase 1 起本地回落 dict 带时效标注（providers/travel/facts）
            assert set(leg) == {"distance_km", "mode", "minutes", "cost_cny",
                                "source", "observed_at", "traffic_aware",
                                "is_estimate", "fallback_reason"}
        finally:
            routing.set_route_provider(None)

    def test_provider_raising_falls_back(self):
        """数据源异常不得让整个行程生成失败。"""
        from backend.tools.travel import routing

        def _boom(*a):
            raise RuntimeError("网络炸了")

        routing.set_route_provider(_boom)
        try:
            leg = routing.estimate_leg(26.08, 119.29, 26.09, 119.30)
            assert leg["minutes"] > 0
        finally:
            routing.set_route_provider(None)

    def test_route_km_stays_pure(self):
        """排序启发式不联网：每轮 O(n²) 次调用，走网络会打爆配额。"""
        from backend.tools.travel import routing

        routing.set_route_provider(lambda *a: (_ for _ in ()).throw(
            AssertionError("route_km 不应调用数据源")))
        try:
            assert routing.route_km(26.08, 119.29, 26.20, 119.40) > 0
        finally:
            routing.set_route_provider(None)


# =============================================
# 9. 工具层
# =============================================
class TestMapTools:
    def test_tools_report_not_configured(self, monkeypatch):
        from backend.tools.map import geo as map_geo

        monkeypatch.setattr(MAP, "TENCENT_LBS_ENABLED", False)
        out = map_geo.map_geocode_tool.invoke({"address": "福州市鼓楼区"})
        assert "未配置" in out

    def test_geocode_tool_success(self, configured, monkeypatch):
        from backend.tools.map import geo as map_geo

        monkeypatch.setattr(map_geo.api, "geocode",
                            lambda *a, **k: {"title": "x", "lat": 26.0, "lng": 119.0})
        out = map_geo.map_geocode_tool.invoke({"address": "福州市鼓楼区"})
        assert '"lat": 26.0' in out and "error" not in out

    def test_tool_reports_failure_explicitly(self, configured, monkeypatch):
        """失败必须以 error 呈现，否则 LLM 会把「查不了」当成「没有这个地方」。"""
        from backend.tools.map import geo as map_geo

        monkeypatch.setattr(map_geo.api, "geocode", lambda *a, **k: None)
        out = map_geo.map_geocode_tool.invoke({"address": "不存在的地方"})
        assert "error" in out

    def test_coord_convert_tool_flags_noop(self):
        from backend.tools.map import geo as map_geo

        out = map_geo.map_coord_convert_tool.invoke(
            {"location": "26.08,119.29", "from_type": "gcj02"})
        assert '"changed": false' in out

    def test_route_tool_accepts_chinese_mode(self, configured, monkeypatch):
        from backend.tools.map import route as map_route

        seen = {}
        monkeypatch.setattr(map_route.api, "direction",
                            lambda mode, *a, **k: seen.update(mode=mode) or {
                                "distance_km": 1.0, "duration_min": 12.0,
                                "duration_s": 720, "steps": []})
        out = map_route.map_route_tool.invoke({
            "from_location": "26.08,119.29", "to_location": "26.09,119.30",
            "mode": "步行"})
        assert seen["mode"] == "walking" and "error" not in out

    def test_route_tool_rejects_bad_coord(self, configured):
        from backend.tools.map import route as map_route

        out = map_route.map_route_tool.invoke({
            "from_location": "北京", "to_location": "26.09,119.30"})
        assert "error" in out

    def test_distance_matrix_caps_point_count(self, configured):
        from backend.tools.map import route as map_route

        many = ";".join(["26.0,119.0"] * 26)
        out = map_route.map_distance_matrix_tool.invoke(
            {"from_locations": many, "to_locations": "26.1,119.1"})
        assert "error" in out and "25" in out

    def test_static_map_tool_returns_proxy_url_without_key(self, configured):
        """工具绝不能把带 Key 的直链交给 LLM。"""
        from backend.config import map as MAP_CFG
        from backend.tools.map import static_map

        out = static_map.map_static_map_tool.invoke(
            {"center": "26.0824,119.2968", "markers": "26.049,119.389,B"})
        assert "/api/map/static-map" in out
        assert MAP_CFG.TENCENT_LBS_KEY not in out
        assert "key=" not in out

    def test_static_map_tool_validates_markers(self):
        from backend.tools.map import static_map

        out = static_map.map_static_map_tool.invoke(
            {"center": "26.08,119.29", "markers": "not,a,coord,x"})
        assert "error" in out

    def test_static_map_tool_requires_position(self):
        from backend.tools.map import static_map

        out = static_map.map_static_map_tool.invoke({"center": ""})
        assert "error" in out

    def test_normalize_coord_handles_llm_formats(self):
        from backend.tools.map import _base

        assert _base.normalize_coord("26.08,119.29") == (26.08, 119.29)
        assert _base.normalize_coord("lat=26.08,lng=119.29") == (26.08, 119.29)
        assert _base.normalize_coord("26.08 119.29") == (26.08, 119.29)
        assert _base.normalize_coord("26.08") is None

    def test_all_map_tools_registered(self):
        from backend.tools.tool_registry import tool_registry

        expected = {
            "map_geocode_tool", "map_reverse_geocode_tool", "map_ip_location_tool",
            "map_district_tool", "map_coord_convert_tool", "map_place_search_tool",
            "map_place_suggest_tool", "map_route_tool", "map_distance_matrix_tool",
            "map_static_map_tool", "map_weather_tool", "map_street_view_tool",
            "map_navigation_tool",
        }
        assert expected <= tool_registry.tool_names

    def test_weather_tool(self, configured, monkeypatch):
        from backend.tools.map import weather as map_weather

        monkeypatch.setattr(map_weather.api, "weather",
                            lambda **k: {"kind": "now", "current": {"weather": "多云"}})
        out = map_weather.map_weather_tool.invoke(
            {"location": "26.0824,119.2968", "kind": "实时"})
        assert "多云" in out and "error" not in out

    def test_weather_tool_requires_locator(self, configured):
        from backend.tools.map import weather as map_weather

        out = map_weather.map_weather_tool.invoke({"city": "", "location": ""})
        assert "error" in out

    def test_weather_tool_rejects_bad_kind(self, configured):
        from backend.tools.map import weather as map_weather

        out = map_weather.map_weather_tool.invoke({"city": "福州", "kind": "明天"})
        assert "error" in out

    def test_street_view_tool_surfaces_apply_hint(self, configured, monkeypatch):
        """113 的申请指引必须原样到达调用方，否则用户只会以为没有街景数据。"""
        from backend.tools.map import street_view

        monkeypatch.setattr(street_view.api, "street_view_pano",
                            lambda *a, **k: (_ for _ in ()).throw(
                                T.TencentLbsError(
                                    "腾讯 LBS 错误 113: 此功能未被授权。"
                                    "街景服务需单独申请：发至 mapapi@vip.qq.com", status=113)))
        out = street_view.map_street_view_tool.invoke({"location": "26.0824,119.2968"})
        assert "mapapi@vip.qq.com" in out and "error" in out

    def test_street_view_tool_returns_proxy_url_without_key(self, configured, monkeypatch):
        from backend.tools.map import street_view

        monkeypatch.setattr(street_view.api, "street_view_pano",
                            lambda *a, **k: {"pano": "P1", "lat": 26.08, "lng": 119.29,
                                             "description": "南后街"})
        out = street_view.map_street_view_tool.invoke({"location": "26.0824,119.2968"})
        assert "/api/map/street-view" in out
        assert MAP.TENCENT_LBS_KEY not in out and "key=" not in out

    def test_street_view_tool_reports_no_coverage(self, configured, monkeypatch):
        from backend.tools.map import street_view

        monkeypatch.setattr(street_view.api, "street_view_pano",
                            lambda *a, **k: {"pano": "", "lat": 26.0, "lng": 119.0})
        out = street_view.map_street_view_tool.invoke({"location": "26.0824,119.2968"})
        assert "没有街景数据" in out

    def test_navigation_tool_warns_on_backend_key(self, configured):
        from backend.tools.map import route as map_route

        out = map_route.map_navigation_tool.invoke(
            {"to_location": "26.049,119.3896", "to_name": "鼓山"})
        assert '"key_kind": "backend"' in out
        assert "warning" in out

    def test_navigation_tool_no_warning_with_frontend_key(self, configured, monkeypatch):
        from backend.tools.map import route as map_route

        monkeypatch.setattr(MAP, "TENCENT_LBS_FRONTEND_KEY", "FRONT")
        out = map_route.map_navigation_tool.invoke({"to_location": "26.049,119.3896"})
        assert '"key_kind": "frontend"' in out and "warning" not in out

    def test_navigation_tool_accepts_chinese_mode(self, configured):
        from backend.tools.map import route as map_route

        out = map_route.map_navigation_tool.invoke(
            {"to_location": "26.049,119.3896", "mode": "公交"})
        assert '"mode": "bus"' in out

    def test_navigation_tool_rejects_bad_coord(self, configured):
        from backend.tools.map import route as map_route

        out = map_route.map_navigation_tool.invoke({"to_location": "鼓山"})
        assert "error" in out


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math

    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi = math.radians(b[0] - a[0])
    dlmb = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))
