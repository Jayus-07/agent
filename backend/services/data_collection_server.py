"""backend/services/data_collection_server.py — DataCollection Service（端口 8082）

Month 1 Skill 升级：将 data_collection_tool 封装为独立微服务。

提供三种调用方式：
1. Standard:  POST /call/data_collection_tool
              {"source": "products", "enable_write": false, "enable_analysis": true}
2. Batch:     POST /batch/calls
3. Streaming: GET  /stream/data_collection_tool?source=products  (SSE)

附加端点：
- GET /datasets  列出本地可用数据集（static fetcher）

启动:
    python -m backend.services.data_collection_server
"""
from pathlib import Path

from backend.services.base import ToolServer
from backend.tools.data_collection import data_collection_tool

server = ToolServer(
    title="DataCollection Service",
    version="1.0.0",
    description="MCP 风格封装的数据采集服务（Month 1 Skill 升级）",
)
server.register(data_collection_tool)

app = server.app  # 供 uvicorn backend.services.data_collection_server:app 使用


@app.get("/datasets")
async def list_datasets() -> dict:
    """列出 static fetcher 可用的本地数据集文件"""
    datasets_dir = Path(__file__).resolve().parent.parent / "data_collection" / "datasets"
    files = sorted(p.name for p in datasets_dir.glob("*") if p.is_file())
    return {"dir": str(datasets_dir), "files": files, "count": len(files)}


if __name__ == "__main__":
    server.run(host="0.0.0.0", port=8082)
