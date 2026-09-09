"""backend/services/web_search_server.py — Web Search Service（端口 8081）

Month 1 Skill 升级：将 web_search_tool / web_crawl_tool 封装为独立微服务。

提供三种调用方式：
1. Standard:  POST /call/web_search   {"query": "...", "num_results": 5}
2. Batch:     POST /batch/calls       {"calls": [{"tool": "web_search", "params": {...}}, ...]}
3. Streaming: GET  /stream/web_search?query=...   (SSE)

启动:
    python -m backend.services.web_search_server
"""
from backend.services.base import ToolServer
from backend.tools.web import web_search_tool, web_crawl_tool

server = ToolServer(
    title="Web Search Service",
    version="1.0.0",
    description="MCP 风格封装的 Web 搜索/抓取服务（Month 1 Skill 升级）",
)
server.register(web_search_tool)
server.register(web_crawl_tool)

app = server.app  # 供 uvicorn backend.services.web_search_server:app 使用

if __name__ == "__main__":
    server.run(host="0.0.0.0", port=8081)
