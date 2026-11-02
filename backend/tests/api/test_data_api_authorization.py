"""data API 服务端授权与上传路径穿越防线（S3/S4 审计修复回归）。

S3：/data/* 历史上无任何服务端授权依赖——任何取得有效 API 身份的低权限
用户可读 stg_* 预览、可触发 enable_write 直写数据库。修复后读类端点要求
editor+（对齐授权标准矩阵「数据读 = sql.read」），写类端点（upload/
pipeline/collect）要求 admin；客户端 enable_write 参数不构成授权依据。

S4：磁盘文件名完全由服务端生成（file_id + 净化后的展示名），realpath
commonpath 校验最终路径位于上传根目录内；解析失败只清理本次创建的文件。

纯 HTTP 层测试：鉴权走网关注入头（X-Auth-Type/X-User-Id/X-User-Roles），
无需 PG；generate/collect 走本地静态数据集回退路径，enable_write 缺省
False 不触库。上传目录 monkeypatch 到 tmp_path，不污染仓库。
"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import data as data_routes

pytestmark = pytest.mark.pg

VIEWER_HEADERS = {
    "X-Auth-Type": "jwt", "X-User-Id": "u-data-viewer",
    "X-User-Roles": "viewer", "X-Tenant-Id": "data-e2e",
}
EDITOR_HEADERS = {
    "X-Auth-Type": "jwt", "X-User-Id": "u-data-editor",
    "X-User-Roles": "editor", "X-Tenant-Id": "data-e2e",
}
ADMIN_HEADERS = {
    "X-Auth-Type": "jwt", "X-User-Id": "u-data-admin",
    "X-User-Roles": "admin", "X-Tenant-Id": "data-e2e",
}

_CSV = b"sku,price\nSKU-1,9.9\n"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(data_routes, "UPLOAD_DIR", str(tmp_path / "uploads"))
    os.makedirs(data_routes.UPLOAD_DIR, exist_ok=True)
    app = FastAPI()
    app.include_router(data_routes.router)
    app.include_router(data_routes.assets_router)
    app.include_router(data_routes.pipeline_router)
    return TestClient(app, raise_server_exceptions=False)


class TestServerSideAuthorization:
    def test_anonymous_denied(self, client):
        # 匿名（无 JWT 头）必须被拒。拒绝形态依赖环境的内部令牌配置：
        # 已配置 → 401/403；未配置 → 内部令牌通道 fail-closed 返回 503
        # （InternalTokenNotConfigured，main run 41512457337 实证）。
        # 断言语义是「不得放行」，不钉死具体拒绝码。
        r = client.post("/data/generate")
        assert r.status_code in (401, 403, 503), r.text

    def test_viewer_denied_read_and_write(self, client):
        for method, url, kwargs in (
            ("get", "/data/datasets", {}),
            ("post", "/data/generate", {}),
            ("get", "/assets", {}),
            ("get", "/data/collect/history", {}),
            ("post", "/data/collect", {"data": {"dataset": "products"}}),
            ("post", "/data/pipeline/run", {"data": {"file_id": "x"}}),
            ("post", "/data/upload", {"files": {"file": ("a.csv", _CSV)}}),
        ):
            r = getattr(client, method)(url, headers=VIEWER_HEADERS, **kwargs)
            assert r.status_code == 403, f"{method} {url}: {r.text}"

    def test_editor_reads_allowed_writes_denied(self, client):
        r = client.get("/data/datasets", headers=EDITOR_HEADERS)
        assert r.status_code == 200 and r.json()["total"] > 0
        r = client.get("/assets", headers=EDITOR_HEADERS)
        assert r.status_code == 200
        r = client.get("/data/collect/history", headers=EDITOR_HEADERS)
        assert r.status_code == 200
        # generate 走 DC 库失败 → 本地静态数据集回退（设计内路径）
        r = client.post("/data/generate", headers=EDITOR_HEADERS)
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        # 写类端点对 editor 全部拒绝：enable_write 参数不是授权依据
        r = client.post("/data/collect", headers=EDITOR_HEADERS,
                        data={"dataset": "products", "enable_write": "true"})
        assert r.status_code == 403, r.text
        r = client.post("/data/collect/all", headers=EDITOR_HEADERS,
                        data={"enable_write": "true"})
        assert r.status_code == 403, r.text
        r = client.post("/data/pipeline/run", headers=EDITOR_HEADERS,
                        data={"file_id": "whatever"})
        assert r.status_code == 403, r.text
        r = client.post("/data/upload", headers=EDITOR_HEADERS,
                        files={"file": ("a.csv", _CSV)})
        assert r.status_code == 403, r.text

    def test_admin_collect_runs_without_write(self, client):
        """admin 正例：collect 可执行（enable_write 缺省 False 不触写库）。"""
        r = client.post("/data/collect", headers=ADMIN_HEADERS,
                        data={"dataset": "products"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and body["task_id"]
        assert "inserted" not in body  # 未启用写通道


class TestUploadPathContainment:
    def test_traversal_filename_stays_inside_upload_root(self, client, tmp_path):
        r = client.post("/data/upload", headers=ADMIN_HEADERS,
                        files={"file": ("../../evil.csv", _CSV)})
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        body = r.json()
        assert body["filename"] == "../../evil.csv"  # 展示名保留原文
        saved = os.listdir(data_routes.UPLOAD_DIR)
        assert len(saved) == 1 and saved[0].startswith(body["file_id"])
        assert saved[0].endswith("evil.csv")
        # 穿越目标目录不得出现文件
        assert not os.path.exists(os.path.join(str(tmp_path), "evil.csv"))
        assert not os.path.exists(os.path.join(os.path.dirname(data_routes.UPLOAD_DIR), "evil.csv"))

    def test_backslash_traversal_and_unsafe_chars_sanitized(self, client):
        r = client.post("/data/upload", headers=ADMIN_HEADERS,
                        files={"file": ("..\\..\\my data<1>.csv", _CSV)})
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        saved = os.listdir(data_routes.UPLOAD_DIR)
        assert len(saved) == 1
        # 反斜杠与特殊字符全部净化，单层文件名且位于上传目录内
        assert "/" not in saved[0] and "\\" not in saved[0] and ".." not in saved[0]
        assert saved[0].endswith("my_data_1_.csv")

    def test_failed_parse_cleans_only_own_file(self, client, tmp_path):
        # 预置一个"穿越目标"哨兵文件，验证失败清理不触碰根目录外
        outside = tmp_path / "evil.csv"
        outside.write_bytes(b"sentinel")
        r = client.post("/data/upload", headers=ADMIN_HEADERS,
                        files={"file": ("../evil.csv", b"\xff\xfe not utf-8 json")})
        assert r.status_code == 200 and r.json()["ok"] is False
        assert "解析失败" in r.json()["error"]
        assert os.listdir(data_routes.UPLOAD_DIR) == [], "失败上传应清理本次文件"
        assert outside.read_bytes() == b"sentinel", "清理不得触碰根目录外文件"

    def test_upload_rejects_non_csv_json(self, client):
        r = client.post("/data/upload", headers=ADMIN_HEADERS,
                        files={"file": ("a.txt", b"hello")})
        assert r.status_code == 200 and r.json()["ok"] is False
        assert "不支持的文件格式" in r.json()["error"]
        assert os.listdir(data_routes.UPLOAD_DIR) == []
