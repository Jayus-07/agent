"""test_admin_travel_services.py — 商旅与文旅扩展服务管理端点与小红书联动测试

覆盖：
1. GET /admin/travel-services/config 读取与 Key 脱敏（不泄露密钥明文）
2. PUT /admin/travel-services/config 热加载生效（无需重启服务）
3. POST /admin/travel-services/test 连通性测试
4. 小红书文旅攻略与打卡机位知识库检索
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from backend.app.api.deps import OperatorIdentity
from backend.app.api.routes import admin_travel_services as mod
from backend.app.api.routes.admin_travel_services import TravelServicesConfigUpdate
from backend.travel.services.xiaohongshu_service import (
    get_poi_xiaohongshu_spot,
    search_xiaohongshu_guides,
)


@pytest.fixture
def fake_admin_operator() -> OperatorIdentity:
    return OperatorIdentity(
        actor="user:usr_admin_001",
        role="admin",
    )


class TestAdminTravelServices:
    @pytest.mark.asyncio
    async def test_get_config_and_masking(self, fake_admin_operator, monkeypatch):
        monkeypatch.setenv("AMAP_KEY", "test_amap_secret_key_123456")
        res = await mod.get_travel_services_config(operator=fake_admin_operator)
        assert res["status"] == "ok"
        services = res["services"]
        assert "amap" in services
        assert "xiaohongshu" in services
        assert "train_12306" in services
        assert "weather" in services
        assert "zhihu" in services

        # 检查 Key 必须脱敏，不能暴露完整明文
        preview = services["amap"]["key_preview"]
        assert "****" in preview
        assert "test_amap_secret_key_123456" != preview

    @pytest.mark.asyncio
    async def test_update_config_hot_reload(self, fake_admin_operator):
        payload = TravelServicesConfigUpdate(
            xiaohongshu_api_key="xhs_secret_key_888",
            xiaohongshu_enabled=True,
            guide_source_priority="xiaohongshu",
            train_12306_enabled=True,
        )
        res = await mod.update_travel_services_config(body=payload, operator=fake_admin_operator)
        assert res["status"] == "ok"
        assert "热加载即时生效" in res["message"]

        # 验证热加载已生效
        conf = await mod.get_travel_services_config(operator=fake_admin_operator)
        assert conf["guide_source_priority"] == "xiaohongshu"
        assert conf["services"]["xiaohongshu"]["configured"] is True
        assert "****" in conf["services"]["xiaohongshu"]["key_preview"]

    @pytest.mark.asyncio
    async def test_service_connectivity_probes(self, fake_admin_operator):
        for s in ("amap", "train_12306", "xiaohongshu", "weather"):
            probe = await mod.test_travel_service(service_id=s, operator=fake_admin_operator)
            assert probe["service"] == s
            assert "message" in probe


class TestXiaohongshuService:
    def test_search_xiaohongshu_guides(self):
        # 命中内置高频城市
        shanghai_spots = search_xiaohongshu_guides("上海", limit=2)
        assert len(shanghai_spots) == 2
        assert any("武康大楼" in s["title"] or "外滩" in s["title"] for s in shanghai_spots)

        # 兜底城市
        other_spots = search_xiaohongshu_guides("拉萨", limit=1)
        assert len(other_spots) == 1
        assert "拉萨" in other_spots[0]["title"]

    def test_get_poi_xiaohongshu_spot(self):
        spot = get_poi_xiaohongshu_spot("上海外滩观景台")
        assert spot is not None
        assert "九江路" in spot["note"] or "水滴" in spot["note"]

        spot_none = get_poi_xiaohongshu_spot("某个未知的小村庄")
        assert spot_none is None
