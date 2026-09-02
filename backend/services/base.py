"""backend/services/ — Month 1: Tool → Service 微服务封装层

将 LangChain Tool 封装为独立 FastAPI 微服务，提供三种调用方式：
1. Standard:  POST /call/{tool_name}          单次调用
2. Batch:     POST /batch/calls               批量并行调用
3. Streaming: GET  /stream/{tool_name}        SSE 流式推送

设计要点：
- 同步 Tool 统一放入线程池执行（asyncio.to_thread），避免阻塞事件循环
- 统一响应模型：{success, data, error, latency_ms, tool}
- 入参经 tool.args_schema 校验，非法参数返回 422
"""
import asyncio
import json
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from backend.shared.logger import logger


# ==================== 统一响应模型 ====================

class ToolCallResponse(BaseModel):
    """单次工具调用响应"""
    success: bool
    tool: str
    data: str | None = None
    error: str | None = None
    latency_ms: float = 0.0


class BatchCallItem(BaseModel):
    """批量调用中的单项"""
    tool: str = Field(..., description="工具名")
    params: dict[str, Any] = Field(default_factory=dict, description="调用参数")


class BatchCallRequest(BaseModel):
    """批量调用请求"""
    calls: list[BatchCallItem] = Field(..., description="工具调用列表")
    max_concurrency: int = Field(default=5, ge=1, le=20, description="并发上限")


class BatchCallResponse(BaseModel):
    """批量调用响应"""
    results: list[ToolCallResponse]
    total_latency_ms: float = 0.0


class ToolInfo(BaseModel):
    """工具元信息（供 /tools 发现接口）"""
    name: str
    description: str
    parameters: dict[str, Any]


# ==================== 服务框架 ====================

class ToolServer:
    """通用工具微服务框架

    用法:
        server = ToolServer(
            title="Web Search Service",
            tools={"web_search": web_search_tool},
        )
        app = server.app          # 供 uvicorn / TestClient 使用
        server.run(port=8081)     # 直接启动
    """

    def __init__(self, title: str, version: str = "1.0.0", description: str = ""):
        self._tools: dict[str, BaseTool] = {}
        self.app = FastAPI(title=title, version=version, description=description)
        self._register_routes()

    # ── 工具注册 ──

    def register(self, tool: BaseTool) -> None:
        """注册一个工具到服务"""
        self._tools[tool.name] = tool
        logger.info(f"[{self.app.title}] 注册工具: {tool.name}")

    def _get_tool(self, tool_name: str) -> BaseTool:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise HTTPException(
                status_code=404,
                detail=f"未知工具: {tool_name}，可用: {list(self._tools)}",
            )
        return tool

    # ── 核心调用逻辑（同步，运行在线程池） ──

    def _invoke_sync(self, tool_name: str, params: dict[str, Any]) -> ToolCallResponse:
        """同步执行工具调用（含计时与异常兜底）"""
        start = time.perf_counter()
        try:
            tool = self._get_tool(tool_name)
            # 经 args_schema 校验入参
            if tool.args_schema is not None:
                params = tool.args_schema(**params).model_dump()
            result = tool.invoke(params)
            latency = (time.perf_counter() - start) * 1000
            return ToolCallResponse(
                success=True, tool=tool_name, data=result, latency_ms=latency,
            )
        except HTTPException:
            raise
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            logger.warning(f"[{self.app.title}] {tool_name} 调用失败: {e}")
            return ToolCallResponse(
                success=False, tool=tool_name, error=str(e), latency_ms=latency,
            )

    async def _invoke_async(self, tool_name: str, params: dict[str, Any]) -> ToolCallResponse:
        """线程池异步执行，避免同步 Tool 阻塞事件循环"""
        return await asyncio.to_thread(self._invoke_sync, tool_name, params)

    # ── 路由 ──

    def _register_routes(self) -> None:
        app = self.app

        @app.get("/health")
        async def health() -> dict[str, Any]:
            """健康检查"""
            return {"status": "ok", "service": app.title, "tools": list(self._tools)}

        @app.get("/tools", response_model=list[ToolInfo])
        async def list_tools() -> list[ToolInfo]:
            """工具发现接口（MCP tools/list 对应物）"""
            return [
                ToolInfo(
                    name=t.name,
                    description=(t.description or "").strip(),
                    parameters=t.args if isinstance(t.args, dict) else {},
                )
                for t in self._tools.values()
            ]

        @app.post("/call/{tool_name}", response_model=ToolCallResponse)
        async def call_tool(tool_name: str, params: dict[str, Any]) -> ToolCallResponse:
            """标准调用：单次执行指定工具"""
            return await self._invoke_async(tool_name, params)

        @app.post("/batch/calls", response_model=BatchCallResponse)
        async def batch_calls(req: BatchCallRequest) -> BatchCallResponse:
            """批量调用：信号量限流 + 并行执行"""
            start = time.perf_counter()
            semaphore = asyncio.Semaphore(req.max_concurrency)

            async def guarded(item: BatchCallItem) -> ToolCallResponse:
                async with semaphore:
                    return await self._invoke_async(item.tool, item.params)

            results = await asyncio.gather(*(guarded(c) for c in req.calls))
            return BatchCallResponse(
                results=list(results),
                total_latency_ms=(time.perf_counter() - start) * 1000,
            )

        @app.get("/stream/{tool_name}")
        async def stream_tool(tool_name: str, request: Request) -> StreamingResponse:
            """流式调用：SSE 推送 start → complete/error → [DONE]，查询参数即工具入参"""
            self._get_tool(tool_name)  # 提前校验工具存在
            query_params = dict(request.query_params)

            async def event_generator():
                yield _sse({"event": "start", "tool": tool_name, "params": query_params})
                resp = await self._invoke_async(tool_name, query_params)
                if resp.success:
                    yield _sse({
                        "event": "complete",
                        "data": resp.data,
                        "latency_ms": round(resp.latency_ms, 1),
                    })
                else:
                    yield _sse({"event": "error", "message": resp.error})
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                event_generator(), media_type="text/event-stream",
            )

    # ── 启动 ──

    def run(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """直接启动服务"""
        import uvicorn
        uvicorn.run(self.app, host=host, port=port)


def _sse(payload: dict[str, Any]) -> str:
    """构造一条 SSE data 消息"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
