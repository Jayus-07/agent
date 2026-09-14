"""scripts/serve_map_demo.py — 单独起一个只挂地图路由的轻量服务，用来跑演示台。

用途：`python backend/scripts/serve_map_demo.py` 后打开
<http://127.0.0.1:8085/map/demo>

**端口**：默认 8085（可用 `MAP_DEMO_PORT` 覆盖）。**不要用 8080**——那是
api-gateway 的统一入口；本机同时存在两者时，Windows 会优先把
``127.0.0.1:8080`` 的请求交给更具体的绑定（演示台），导致经网关的登录/业务
请求被劫持，排查成本很高（实测踩过）。

**为什么不直接用主服务**：主服务启动时要拉起 RAG 索引、jieba、LLM 客户端、
Redis/Postgres 连接等一堆重依赖，只为看一个地图演示台不值当。这里只挂
``maps.router``，秒级启动，且演示台与接口同源，没有 CORS 问题。

生产/联调请用主服务（``http://localhost:8000/map/demo``）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402

from backend.app.api.routes.maps import router as map_router  # noqa: E402
from backend.config import map as MAP  # noqa: E402


def build_app() -> FastAPI:
    app = FastAPI(
        title="腾讯位置服务演示台",
        description="只挂 /map/* 路由，用于本地快速验证 LBS 接入",
        version="1.0.0",
    )
    app.include_router(map_router)

    @app.get("/", include_in_schema=False)
    async def _root():
        return RedirectResponse("/map/demo")

    return app


def main() -> int:
    import uvicorn

    host = os.getenv("MAP_DEMO_HOST", "127.0.0.1")
    # 默认端口不能再用 8080：那是 api-gateway 的统一入口。演示台若占着
    # 127.0.0.1:8080，Windows 会优先匹配这个更具体的绑定，把访问网关的
    # 请求（含 /api/auth/** 登录）全部劫持到演示台，表现为网关"明明起了却
    # 打不通"。改用 8085，与 8000(app)/8080(gateway)/8081(business)/8090(rag)
    # /8091(mcp) 均不冲突。
    port = int(os.getenv("MAP_DEMO_PORT", "8085"))

    print("=" * 62)
    print("腾讯位置服务演示台")
    print(f"  Key 已配置 : {MAP.is_configured()}")
    if not MAP.is_configured():
        print("  ⚠ 未配置 TENCENT_LBS_KEY，演示台会全部返回 503")
    print(f"  打开地址   : http://{host}:{port}/map/demo")
    print(f"  接口文档   : http://{host}:{port}/docs")
    print("=" * 62)

    uvicorn.run(build_app(), host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
