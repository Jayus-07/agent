"""
mock_api/server.py — FastAPI Mock 数据源

模拟第三方电商平台 API，返回 datasets/ 中的数据。

启动方式:
    python -m data_collection.mock_api.server
    → 监听 http://localhost:8001

端点:
    GET /mock/products           → 全部商品（可筛选 ?category=电子产品）
    GET /mock/orders             → 全部订单
    GET /mock/shops              → 全部店铺
    GET /mock/inventory          → 全部库存
    GET /mock/suppliers          → 全部供应商
    GET /mock/health             → 健康检查
    GET /mock/datasets           → 列出所有可用数据集

设计: 独立 ASGI 应用，不依赖主服务。
"""

import json
import os
import sys
from pathlib import Path

# 确保项目根在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import uvicorn

from backend.data_collection.config import DC_MOCK_API_HOST, DC_MOCK_API_PORT

# ── 数据集加载 ──
_DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"


def _load_dataset(name: str) -> list[dict]:
    """加载数据集 JSON 文件"""
    file_path = _DATASETS_DIR / f"{name}.json"
    if not file_path.exists():
        return []
    return json.loads(file_path.read_text(encoding="utf-8"))


# 启动时全部加载到内存
_DATASETS_CACHE: dict[str, list[dict]] = {}
for _ds_name in ["products", "orders", "shops", "inventory", "suppliers"]:
    _DATASETS_CACHE[_ds_name] = _load_dataset(_ds_name)

# ── FastAPI 应用 ──
app = FastAPI(
    title="DCC Mock API",
    description="Data Collection Center 模拟数据源 — 模拟 Amazon/Shopify/eBay 电商数据 API",
    version="1.0.0",
    docs_url=None,      # Mock API 不需要文档
    redoc_url=None,
)


@app.get("/mock/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "service": "DCC Mock API",
        "datasets": list(_DATASETS_CACHE.keys()),
        "record_counts": {k: len(v) for k, v in _DATASETS_CACHE.items()},
    }


@app.get("/mock/datasets")
async def list_datasets():
    """列出所有可用数据集及记录数"""
    return {
        ds: {"count": len(records), "sample_fields": list(records[0].keys()) if records else []}
        for ds, records in _DATASETS_CACHE.items()
    }


@app.get("/mock/products")
async def get_products(category: str = Query("", description="按品类筛选")):
    """获取商品数据，支持 ?category=电子产品"""
    data = _DATASETS_CACHE.get("products", [])
    if category:
        data = [d for d in data if d.get("品类") == category]
    return JSONResponse(content=data)


@app.get("/mock/orders")
async def get_orders(channel: str = Query("", description="按渠道筛选")):
    """获取订单数据，支持 ?channel=Amazon"""
    data = _DATASETS_CACHE.get("orders", [])
    if channel:
        data = [d for d in data if d.get("渠道") == channel]
    return JSONResponse(content=data)


@app.get("/mock/shops")
async def get_shops(platform: str = Query("", description="按平台筛选")):
    """获取店铺数据"""
    data = _DATASETS_CACHE.get("shops", [])
    if platform:
        data = [d for d in data if d.get("平台") == platform]
    return JSONResponse(content=data)


@app.get("/mock/inventory")
async def get_inventory(status: str = Query("", description="按库存状态筛选: 充足/偏低/断货")):
    """获取库存数据"""
    data = _DATASETS_CACHE.get("inventory", [])
    if status:
        data = [d for d in data if d.get("状态") == status]
    return JSONResponse(content=data)


@app.get("/mock/suppliers")
async def get_suppliers(status: str = Query("", description="按合作状态筛选")):
    """获取供应商数据"""
    data = _DATASETS_CACHE.get("suppliers", [])
    if status:
        data = [d for d in data if d.get("状态") == status]
    return JSONResponse(content=data)


# ── 命令行启动 ──
def main():
    """启动 Mock API Server"""
    print(f"\n{'='*60}")
    print(f"  DCC Mock API Server")
    print(f"  监听: http://{DC_MOCK_API_HOST}:{DC_MOCK_API_PORT}")
    print(f"  数据集: {list(_DATASETS_CACHE.keys())}")
    for ds, records in _DATASETS_CACHE.items():
        print(f"    /mock/{ds}  → {len(records)} 条")
    print(f"  健康检查: http://{DC_MOCK_API_HOST}:{DC_MOCK_API_PORT}/mock/health")
    print(f"{'='*60}\n")

    uvicorn.run(
        "data_collection.mock_api.server:app",
        host=DC_MOCK_API_HOST,
        port=DC_MOCK_API_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
