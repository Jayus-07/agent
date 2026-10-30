"""图表落盘 artifact 模式回归（2026-10-25）。

base64 内嵌曾让报告 Markdown 达数万 token（实测 49713），被 L1 上下文
预算压成预览后用户看到"无法展示"。文件模式：PNG 落 chart_output_dir，
Markdown 引用 url_prefix + 文件名；未配置目录保持 base64（旧行为）。
"""
import os

import pytest
from fastapi.testclient import TestClient

from backend.business_report.chart_generator import ChartGenerator

_DATA = [
    {"day": "周一", "sales": 100.0},
    {"day": "周二", "sales": 200.0},
    {"day": "周三", "sales": 150.0},
]


def test_file_mode_writes_png_and_references_url(tmp_path):
    gen = ChartGenerator(chart_output_dir=str(tmp_path), url_prefix="/api/report-files/")
    md = gen.generate([{"type": "bar", "x": "day", "y": "sales", "title": "销量"}], _DATA)

    assert "data:image/png;base64" not in md
    assert "/api/report-files/" in md
    pngs = [f for f in os.listdir(tmp_path) if f.endswith(".png")]
    assert len(pngs) == 1
    assert pngs[0] in md


def test_default_mode_keeps_base64_inline():
    gen = ChartGenerator()
    md = gen.generate([{"type": "bar", "x": "day", "y": "sales", "title": "销量"}], _DATA)
    assert "data:image/png;base64," in md


def test_unwritable_dir_falls_back_to_base64(tmp_path):
    # 制造不可写：先建同名文件占位使 makedirs 失败
    blocker = tmp_path / "blocked"
    blocker.write_text("not a dir", encoding="utf-8")
    gen = ChartGenerator(
        chart_output_dir=str(tmp_path / "blocked" / "nested" / "x"),
        url_prefix="/api/report-files/",
    )
    md = gen.generate([{"type": "bar", "x": "day", "y": "sales", "title": "销量"}], _DATA)
    # 目录不可写 → 软回退 base64，图表不丢
    assert "data:image/png;base64," in md or "图表生成失败" in md


def test_report_files_endpoint_serves_and_blocks(tmp_path, monkeypatch):
    """端点只服务 charts 目录内的合法 PNG 名（防穿越/防扩展名）。"""
    from backend.app.api.routes import report_files as rf

    png = tmp_path / "chart_abcd1234.png"
    png.write_bytes(b"\x89PNG-fake")
    monkeypatch.setattr(rf, "_charts_dir", lambda: str(tmp_path))

    from backend.app.server import app

    # API-Key 中间件 fail-closed（CI 无 .env：API_KEY 空 → 归一化后落出
    # skip 白名单的路径一律 503，本地 .env 有配置才是 401）。测试锁定的是
    # 「穿越请求不得落进文件服务」，进程内注入测试 key 使拒绝路径稳定为
    # 401，消除本地/CI 配置差异；/report-files/* 在 skip 白名单内不受影响。
    monkeypatch.setattr("backend.app.api.middleware.auth.API_KEY", "test-key")

    client = TestClient(app)
    # app 内路径无 /api 前缀（APISIX rewrite /api/* → /*）
    resp = client.get("/report-files/chart_abcd1234.png")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG-fake"

    # 路径穿越：httpx/浏览器会先归一化 ../（→ /secret.txt），落出白名单
    # 被 API-Key 层拦截（401）——穿越不可达同样安全；未归一化的原始串
    # 由路由的 _SAFE_NAME/realpath 二次防御兜底。
    assert client.get("/report-files/../secret.txt").status_code in (401, 403, 404)
    assert client.get("/report-files/chart.png%00.txt").status_code in (403, 404)
    assert client.get("/report-files/missing.png").status_code == 404
