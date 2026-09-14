"""scripts/verify_tencent_lbs.py — 腾讯位置服务接入自检

逐个跑通各能力，重点验证三件容易出错的事：

1. **单位口径**。``/direction`` 的 duration 是「分钟」，``/distance/matrix``
   的 duration 是「秒」——用同一段路交叉比对，若两者折算后不接近，
   说明归一化写反了。
2. **坐标系**。本地换算（infra.lbs.geo）与服务端换算（/coord/translate）
   结果应当一致；偏差超过几十米即说明算法实现有问题。
3. **失败路径**。未配置 Key / 错误状态码必须走优雅降级，不能抛到调用方。

用法::

    .venv/Scripts/python.exe backend/scripts/verify_tencent_lbs.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.config import map as MAP  # noqa: E402
from backend.infra.http import tencent_lbs as T  # noqa: E402
from backend.infra.lbs import api, geo  # noqa: E402

PASS = "  [PASS]"
FAIL = "  [FAIL]"
WARN = "  [WARN]"

# 福州三坊七巷 → 鼓山，贯穿市区，够长以便暴露单位换算错误
FZ_SANFANG = (26.082410, 119.296820)
FZ_GUSHAN = (26.049030, 119.389640)

_failures: list[str] = []


def _check(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        print(f"{PASS} {label}{' — ' + detail if detail else ''}")
    else:
        print(f"{FAIL} {label}{' — ' + detail if detail else ''}")
        _failures.append(label)


def _title(text: str) -> None:
    print(f"\n{'=' * 62}\n{text}\n{'=' * 62}")


def main() -> int:
    print("\n腾讯位置服务（Tencent LBS）接入自检")

    # ── 0. 配置 ───────────────────────────────────────────
    _title("0. 配置与鉴权")
    report = api.capability_report()
    for k, v in report.items():
        if k != "endpoints":
            print(f"  {k:22} = {v}")
    print(f"  {'端点数量':22} = {len(report['endpoints'])}")
    if not report["configured"]:
        print(f"\n{WARN} 未配置 TENCENT_LBS_KEY，后续用例全部跳过"
              "（这本身即为预期的降级行为）")
        return 0

    # ── 1. IP 定位 ────────────────────────────────────────
    _title("1. IP 定位 /ws/location/v1/ip")
    ip_loc = api.ip_location()
    if ip_loc:
        print(f"  {ip_loc['province']}{ip_loc['city']} "
              f"({ip_loc['lat']:.5f}, {ip_loc['lng']:.5f}) adcode={ip_loc['adcode']}")
    _check(ip_loc is not None and bool(ip_loc.get("city")), "IP 定位返回城市")

    # ── 2. 地理编码 ───────────────────────────────────────
    _title("2. 地址 → 坐标 /ws/geocoder/v1/")
    geo_r = api.geocode("福州市鼓楼区南后街139号", region="福州")
    if geo_r:
        print(f"  {geo_r['formatted_address']}")
        print(f"  坐标 {geo_r['lat']:.6f}, {geo_r['lng']:.6f}  "
              f"相似度={geo_r['similarity']} 可信度={geo_r['reliability']}")
    _check(geo_r is not None, "地理编码有结果")
    if geo_r:
        # 三坊七巷在南后街，坐标应落在 26.08 / 119.29 附近
        _check(abs(geo_r["lat"] - 26.082) < 0.02 and abs(geo_r["lng"] - 119.297) < 0.02,
               "地理编码坐标落在预期范围内",
               f"({geo_r['lat']:.5f}, {geo_r['lng']:.5f})")

    # ── 3. 逆地理编码 ─────────────────────────────────────
    _title("3. 坐标 → 地址 /ws/geocoder/v1/ (reverse)")
    rev = api.reverse_geocode(*FZ_SANFANG, get_poi=True, poi_page_size=5)
    if rev:
        print(f"  {rev['formatted_address']}  |  {rev['province']}{rev['city']}"
              f"{rev['district']} adcode={rev['adcode']}")
        print(f"  周边 POI {len(rev['pois'])} 条，例如："
              + "、".join(p["name"] for p in rev["pois"][:3]))
    _check(rev is not None and bool(rev.get("district")), "逆地理编码返回行政区")
    _check(bool(rev and rev.get("pois")), "逆地理编码附带周边 POI")

    # ── 4. 地点检索 ───────────────────────────────────────
    _title("4. 地点检索 /ws/place/v1/search")
    by_region = api.place_search("景点", region="福州", page_size=5)
    _check(bool(by_region), "城市限定检索有结果", f"{len(by_region or [])} 条")
    if by_region:
        for p in by_region[:3]:
            print(f"    - {p['name']}  [{p['category']}]  {p['address']}")

    near = api.place_search("美食", near=FZ_SANFANG, radius=2000, page_size=5)
    _check(bool(near), "周边检索有结果", f"{len(near or [])} 条")
    if near:
        for p in near[:3]:
            print(f"    - {p['name']}  {p['distance_m']}m  {p['address']}")

    # ── 5. 输入提示 ───────────────────────────────────────
    _title("5. 关键词提示 /ws/place/v1/suggestion")
    sug = api.place_suggestion("三坊", region="福州")
    _check(bool(sug), "输入提示有结果", f"{len(sug or [])} 条")
    if sug:
        print("    " + "、".join(p["name"] for p in sug[:5]))

    # ── 6. 行政区划 ───────────────────────────────────────
    _title("6. 行政区划 /ws/district/v1/search + getchildren")
    provinces = api.district_provinces()
    _check(len(provinces or []) == 34, "省级列表恰好 34 个省级行政区",
           f"实际 {len(provinces or [])} 条")

    dists = api.district_search("福州")
    _check(len(dists or []) > 0 and (dists or [{}])[0]["fullname"] == "福州市",
           "关键词检索命中福州市",
           f"{len(dists or [])} 条")
    fz_id = ""
    if dists:
        fz_id = dists[0]["id"]
        print(f"    {dists[0]['fullname']} id={fz_id} adcode={fz_id} "
              f"({dists[0]['lat']:.5f}, {dists[0]['lng']:.5f}) level={dists[0]['level']}")
        _check(fz_id == "350100", "福州 adcode = 350100", f"实际 {fz_id}")
        kids = api.district_children(fz_id)
        _check(bool(kids), "下钻查区县有结果", f"{len(kids or [])} 个")
        if kids:
            print("    " + "、".join(k["name"] for k in kids[:8]))
            _check(any(k["name"] == "鼓楼" for k in kids), "福州下辖含鼓楼区")

    resolved = api.resolve_district("厦门")
    _check(bool(resolved and resolved["fullname"] == "厦门市"),
           "城市名归一解析正确",
           resolved["fullname"] if resolved else "")

    # ── 7. 坐标系 ─────────────────────────────────────────
    _title("7. 坐标换算：本地纯函数 vs 服务端接口")
    local = geo.wgs84_to_gcj02(*FZ_SANFANG)
    remote = api.coord_translate([FZ_SANFANG], from_type=1)
    print(f"    WGS-84 输入      {FZ_SANFANG}")
    print(f"    本地换算 GCJ-02  ({local[0]:.6f}, {local[1]:.6f})")
    if remote:
        print(f"    服务端换算       ({remote[0][0]:.6f}, {remote[0][1]:.6f})")
        delta_m = geo_distance_m(local, remote[0])
        _check(delta_m < 30, "本地与服务端换算偏差 < 30m", f"实际 {delta_m:.1f}m")
    else:
        _check(False, "服务端坐标换算可用")

    # 往返一致性：GCJ-02 → WGS-84 → GCJ-02 应回到原点
    back = geo.gcj02_to_wgs84(*local)
    round_trip_m = geo_distance_m(local, geo.wgs84_to_gcj02(*back))
    _check(round_trip_m < 3, "GCJ↔WGS 往返误差 < 3m", f"实际 {round_trip_m:.2f}m")

    # 坐标串解析：纬度在前 vs 经度在前
    _check(geo.parse_lat_lng("26.08,119.29") == (26.08, 119.29), "解析 lat,lng")
    _check(geo.parse_lat_lng("119.29,26.08") == (26.08, 119.29),
           "解析 lat,lng（自动纠正倒序）")
    _check(geo.parse_lat_lng("abc") is None, "非法坐标串返回 None")

    # ── 8. 路线规划 ───────────────────────────────────────
    _title("8. 路线规划 /ws/direction/v1/{mode}/")
    driving = api.direction("driving", *FZ_SANFANG, *FZ_GUSHAN)
    if driving:
        print(f"    驾车 {driving['distance_km']}km / {driving['duration_min']}min "
              f"/ 打车约 {driving['taxi_fare_cny']} 元 / 红绿灯 {driving['traffic_light_count']} 个")
        print(f"    首条导航指令：{driving['steps'][0]['instruction'] if driving['steps'] else '—'}")
    _check(driving is not None, "驾车路线可用")
    _check(bool(driving and driving["duration_s"] == int(driving["duration_min"] * 60)),
           "驾车时长秒/分归一一致")

    walking = api.direction("walking", *FZ_SANFANG, *FZ_GUSHAN)
    _check(walking is not None, "步行路线可用",
           f"{walking['distance_km']}km / {walking['duration_min']}min" if walking else "")

    transit = api.direction("transit", *FZ_SANFANG, *FZ_GUSHAN)
    _check(transit is not None, "公交路线可用",
           f"{transit['distance_km']}km / {transit['duration_min']}min / "
           f"{transit['price_cny']} 元" if transit else "")

    # ── 9. 单位口径交叉验证（本脚本的核心价值）─────────────
    _title("9. 距离矩阵 vs 单段路线：单位口径交叉验证")
    matrix = api.distance_matrix("driving", [FZ_SANFANG], [FZ_GUSHAN])
    _check(bool(matrix and matrix[0]), "距离矩阵返回结果")
    if matrix and driving:
        cell = matrix[0][0]
        print(f"    /direction      {driving['distance_m']}m  "
              f"{driving['duration_min']}min (= {driving['duration_s']}s)")
        print(f"    /distance/matrix {cell['distance_m']}m  "
              f"{cell['duration_s']}s (= {cell['duration_min']}min)")
        # 两个接口算法一致，距离应几乎相同；时长允许路况波动
        dist_gap = abs(cell["distance_m"] - driving["distance_m"])
        _check(dist_gap < 500, "两接口距离一致（< 500m）", f"差 {dist_gap}m")
        minute_gap = abs(cell["duration_min"] - driving["duration_min"])
        _check(minute_gap <= max(3.0, driving["duration_min"] * 0.3),
               "两接口时长归一后一致（分钟级）", f"差 {minute_gap:.1f}min")

    # ── 10. 静态图 ────────────────────────────────────────
    _title("10. 静态图 /ws/staticmap/v2/")
    png = api.static_map_bytes(
        center=FZ_SANFANG, zoom=14, size="600*400",
        markers=[
            {"lat": FZ_SANFANG[0], "lng": FZ_SANFANG[1], "color": "red", "label": "A"},
            {"lat": FZ_GUSHAN[0], "lng": FZ_GUSHAN[1], "color": "blue", "label": "B"},
        ],
    )
    is_png = bool(png) and png[:8] == b"\x89PNG\r\n\x1a\n"
    _check(is_png, "静态图返回合法 PNG", f"{len(png or b'')} 字节")
    url = api.static_map_url(center=FZ_SANFANG, zoom=14)
    _check(bool(url and "key=" in (url or "")), "静态图 URL 已携带 Key（仅服务端使用）")

    # ── 11. 缓存与降级 ────────────────────────────────────
    _title("11. 缓存与失败降级")
    T.clear_cache()
    api.geocode("福州市鼓楼区南屏路", region="福州")
    api.geocode("福州市鼓楼区南屏路", region="福州")
    # 无法直接观测是否命中，改查缓存条目数（命中时不会新增）
    with T._cache_lock:  # noqa: SLF001 — 自检脚本，允许触碰内部状态
        cached_entries = len(T._cache)
    _check(cached_entries == 1, "重复查询命中缓存（仅 1 条条目）", f"缓存 {cached_entries} 条")

    cleared = T.clear_cache()
    _check(cleared >= 1, "缓存可清空", f"清除 {cleared} 条")

    # 模拟业务错误码走降级而非抛异常
    class _FakeResp:
        status_code = 200
        headers = {"content-type": "application/json; charset=utf-8"}

        @staticmethod
        def json() -> dict:
            return {"status": 121, "message": "此key每秒请求量已达上限",
                    "request_id": "selfcheck"}

    try:
        T._parse(_FakeResp())  # type: ignore[arg-type]  # noqa: SLF001
        _check(False, "限流状态码被正确识别")
    except T.TencentLbsError as e:
        _check(e.status == 121 and e.is_quota, "限流状态码被正确识别并标记为配额类",
               str(e))

    # ── 12. 天气 ──────────────────────────────────────────
    _title("12. 天气 /ws/weather/v1/")
    w_now = api.weather(location=FZ_SANFANG, kind="now")
    if w_now:
        cur = w_now["current"]
        print(f"    {w_now['province']}{w_now['city']}{w_now['district']} "
              f"{cur.get('weather')} {cur.get('temperature')}℃ "
              f"湿度{cur.get('humidity')}% {cur.get('wind_direction')}{cur.get('wind_power')}")
    _check(bool(w_now and w_now.get("current")), "实时天气可用")
    # 实测：传坐标能到区县，传 adcode 只到市 — 这是选择入参方式的依据
    _check(bool(w_now and w_now.get("district")),
           "坐标入参精度到区县", w_now.get("district", "（为空）") if w_now else "")
    w_by_adcode = api.weather(adcode="350100", kind="now")
    _check(bool(w_by_adcode) and not (w_by_adcode or {}).get("district"),
           "adcode 入参只到市级（documented 差异）")

    w_future = api.weather_for_city("福州", kind="future")
    days = (w_future or {}).get("days") or []
    _check(len(days) >= 3, "未来预报返回多天", f"{len(days)} 天")
    if days:
        d0 = days[0]
        print(f"    {d0['date']} {d0['week']} 白天 {d0['day'].get('weather')} "
              f"{d0['day'].get('temperature')}℃ / 夜间 {d0['night'].get('weather')} "
              f"{d0['night'].get('temperature')}℃")
        _check(bool(d0.get("day") and d0.get("night")), "预报含昼夜两组数据")

    w_hours = api.weather_for_city("厦门", kind="hours")
    hours = (w_hours or {}).get("hours") or []
    _check(len(hours) >= 12, "逐小时预报可用", f"{len(hours)} 条")

    # ── 13. 街景（预期未授权）──────────────────────────────
    _title("13. 街景 /ws/streetview/v1/*")
    try:
        pano = api.street_view_pano(*FZ_SANFANG, radius=100)
        _check(bool(pano.get("pano")), "街景可用", f"pano={pano.get('pano')}")
        img = api.street_view_image_bytes(pano["pano"], width=400, height=300)
        _check(bool(img), "街景图片可取", f"{len(img or b'')} 字节")
    except T.TencentLbsError as e:
        if e.status == 113:
            print(f"{WARN} 街景未授权（113）—— 这是**预期状态**，非缺陷")
            print("        说明：街景为申请制服务，需邮件申请配额，3 个工作日审批")
            # 关键：异常消息里必须带可执行的申请路径，否则用户只会在控制台里白找
            _check("mapapi@vip.qq.com" in str(e),
                   "113 的异常消息包含申请邮箱（指引可透出）")
            _check("街景" in str(e) and "申请" in str(e), "113 的异常消息说明了该服务为申请制")
            print(f"        实际消息：{str(e)[:110]}...")
        else:
            _check(False, "街景返回了非预期的错误码", f"status={e.status}")

    # ── 14. 导航调起（URI API）─────────────────────────────
    _title("14. 导航调起 /uri/v1/routeplan")
    from backend.config import map as _MAP  # noqa: PLC0415

    nav = api.navigation_uri(to_lat=FZ_GUSHAN[0], to_lng=FZ_GUSHAN[1], to_name="鼓山",
                             from_lat=FZ_SANFANG[0], from_lng=FZ_SANFANG[1],
                             from_name="三坊七巷", mode="drive", policy=1)
    _check(nav["url"].startswith("https://apis.map.qq.com/uri/v1/routeplan"),
           "调起链接已生成", nav["url"][:60] + "…")
    _check("referer=" in nav["url"], "链接带 referer（腾讯必填）")
    _check(nav["policy"] == "避免拥堵", "驾车策略已生效", nav["policy"])
    # 未配前端专用 Key 时必须回退并明确告警 —— 静默外发等于泄露后端配额
    if not _MAP.TENCENT_LBS_FRONTEND_KEY:
        _check(nav["key_kind"] == "backend" and bool(nav.get("warning")),
               "未配前端 Key 时回退后端 Key 并附告警")
        print(f"{WARN} 未配置 TENCENT_LBS_FRONTEND_KEY，调起链接含后端密钥，不可外发")
    else:
        _check(nav["key_kind"] == "frontend", "使用前端专用 Key 生成调起链接")

    bad_policy = False
    try:
        api.navigation_uri(to_lat=26.0, to_lng=119.0, policy=9)
    except ValueError:
        bad_policy = True
    _check(bad_policy, "非法驾车策略被拒绝")

    # ── 15. HTTP 路由（后端代理）────────────────────────────
    _title("15. 后端代理路由 /map/* （前端实际调用的链路）")
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.app.api.routes.maps import router as map_router

        # 只挂地图路由，避免拉起整个 server（RAG/模型加载等重依赖）
        demo_app = FastAPI()
        demo_app.include_router(map_router)
        client = TestClient(demo_app)

        health = client.get("/map/health")
        _check(health.status_code == 200 and health.json()["configured"],
               "GET /map/health", f"HTTP {health.status_code}")

        gc = client.get("/map/geocode", params={"address": "福州市鼓楼区南后街139号",
                                               "city": "福州"})
        _check(gc.status_code == 200 and gc.json().get("found"),
               "GET /map/geocode", f"HTTP {gc.status_code}")

        bad = client.get("/map/reverse-geocode", params={"location": "不是坐标"})
        _check(bad.status_code == 400, "非法坐标返回 400 而非 500",
               f"HTTP {bad.status_code}")

        rt = client.get("/map/route", params={"from": "26.0824,119.2968",
                                              "to": "26.049,119.3896",
                                              "mode": "driving"})
        _check(rt.status_code == 200 and rt.json().get("found"),
               "GET /map/route", f"HTTP {rt.status_code}")

        bad_mode = client.get("/map/route", params={"from": "26.08,119.29",
                                                    "to": "26.09,119.30",
                                                    "mode": "teleport"})
        _check(bad_mode.status_code == 400, "非法出行方式返回 400",
               f"HTTP {bad_mode.status_code}")

        img = client.get("/map/static-map", params={
            "center": "26.0824,119.2968", "zoom": "14", "size": "400*300",
            "markers": "26.0824,119.2968,A;26.049,119.3896,B",
        })
        _check(img.status_code == 200 and img.headers.get("content-type") == "image/png",
               "GET /map/static-map 返回 PNG", f"HTTP {img.status_code}")
        _check(img.content[:8] == b"\x89PNG\r\n\x1a\n", "静态图字节流为合法 PNG")

        demo_page = client.get("/map/demo")
        _check(demo_page.status_code == 200 and "能力演示台" in demo_page.text,
               "GET /map/demo 演示页可访问", f"HTTP {demo_page.status_code}")

        wx = client.get("/map/weather", params={"city": "福州", "kind": "now"})
        _check(wx.status_code == 200 and wx.json().get("found"),
               "GET /map/weather", f"HTTP {wx.status_code}")

        wx_bad = client.get("/map/weather", params={"city": "福州", "kind": "tomorrow"})
        _check(wx_bad.status_code == 400, "非法天气类型返回 400", f"HTTP {wx_bad.status_code}")

        sv = client.get("/map/street-view/pano",
                        params={"location": "26.0824,119.2968", "radius": "100"})
        # 未开通街景时期望 502 且详情里带申请指引；开通后应为 200
        if sv.status_code == 502:
            _check("mapapi@vip.qq.com" in sv.text or "申请" in sv.text,
                   "街景未授权时 502 详情含申请指引", f"HTTP {sv.status_code}")
        else:
            _check(sv.status_code == 200, "GET /map/street-view/pano", f"HTTP {sv.status_code}")

        nv = client.get("/map/navigate", params={"to": "26.049,119.3896", "to_name": "鼓山"})
        _check(nv.status_code == 200 and nv.json().get("url"),
               "GET /map/navigate", f"HTTP {nv.status_code}")

        svc = client.get("/map/services")
        _check(svc.status_code == 200 and "天气" in svc.json(),
               "GET /map/services 探活", f"HTTP {svc.status_code}")

        # 密钥绝不出现在任何响应体里
        key = MAP.TENCENT_LBS_KEY
        leaked = [name for name, resp in
                  (("health", health), ("geocode", gc), ("route", rt), ("demo", demo_page),
                   ("weather", wx), ("services", svc))
                  if key and key in resp.text]
        # 例外：/map/navigate 在后端 Key 模式下必然含密钥，故单独判定其告警而非泄露
        nav_exposed = bool(key and key in nv.text)
        if nav_exposed:
            _check(nv.json().get("key_kind") == "backend"
                   and bool(nv.json().get("warning")),
                   "navigate 含密钥时带 key_kind=backend 与告警（非静默泄露）")
        _check(not leaked, "其余代理响应均不含密钥",
               "泄露于 " + "、".join(leaked) if leaked else "")
    except ImportError as e:
        _check(False, "路由自检需要 fastapi/testclient", str(e))

    # ── 汇总 ──────────────────────────────────────────────
    _title("自检结果")
    if _failures:
        print(f"{FAIL} {len(_failures)} 项未通过：")
        for f in _failures:
            print(f"      - {f}")
        return 1
    print(f"{PASS} 全部用例通过")
    return 0


def geo_distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """两点球面距离（米），仅用于自检比对。"""
    import math

    lat1, lng1 = a
    lat2, lng2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    h = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


if __name__ == "__main__":
    raise SystemExit(main())
