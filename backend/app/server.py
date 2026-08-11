"""FastAPI 服务入口 — 一行启动: uvicorn app.server:app"""
import sys
import os

# 确保项目根路径在 sys.path 中 (server.py 在 backend/app/, 需要 3 层 dirname 到项目根)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.router import api_router
from backend.app.exceptions import (
    http_exception_handler,
    global_exception_handler,
    memory_db_unavailable_handler,
)
from backend.app.api.middleware.concurrency import concurrency_limit_middleware
from backend.app.api.middleware.auth import api_key_middleware
from backend.observability.metrics import render_metrics
from backend.shared.logger import logger
from backend.config.rag import RAG_MAX_FILE_SIZE
from backend.config import CORS_ORIGINS
from mcp_servers.servers import register_all as register_mcp_servers

app = FastAPI(
    title="Agent Platform API",
    description="LangGraph Multi-Agent 智能问答与报告系统",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── 中间件（按执行顺序：auth → size limit → concurrency）────────────────
# 1. 认证：未认证请求尽早 401，不消耗下游资源
app.middleware("http")(api_key_middleware)

# 2. 上传大小限制：在请求体接收前拦截超大文件
@app.middleware("http")
async def upload_size_limit_middleware(request, call_next):
    """P0-1: 在 endpoint 之前检查 Content-Length, 超过限制直接 413 拒绝.
    避免 FastAPI 等客户端发完整 body 才在 endpoint 拒 (耗带宽/磁盘).
    """
    if request.method == "POST" and "/rag/upload" in str(request.url.path):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > RAG_MAX_FILE_SIZE * 1024 * 1024:
            from fastapi.responses import JSONResponse, HTMLResponse
            return JSONResponse(
                {"ok": False, "error": f"file too large (max {RAG_MAX_FILE_SIZE}MB, Content-Length={cl})"},
                status_code=413,
            )
    return await call_next(request)

# 3. 并发控制：最后，只限流已认证的合法请求
app.middleware("http")(concurrency_limit_middleware)

# ── CORS ────────────────────────────────────────
# 生产环境通过 CORS_ORIGINS 环境变量配置（逗号分隔多个域名）
# 默认 http://localhost:3000（开发环境前端地址）
_origins = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 全局异常处理 ──────────────────────────────────
from starlette.exceptions import HTTPException as StarletteHTTPException
from backend.memory.database import MemoryDatabaseUnavailable
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
# 比 Exception 兜底更具体：Starlette 按 MRO 查找，配置缺失会命中这个而非 500
app.add_exception_handler(MemoryDatabaseUnavailable, memory_db_unavailable_handler)
app.add_exception_handler(Exception, global_exception_handler)

# ── 注册所有路由（聚合在 api/router.py） ──────────────
app.include_router(api_router)

# ── Prometheus /metrics 端点（PR-0.3）────────────────
@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint():
    """Prometheus 拉取端点 — 不受 auth / CORS 限制（K8s scrape）。"""
    from fastapi.responses import Response
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)

# ── 注册 MCP Server ─────────────────────────────────
register_mcp_servers()


# ═══════════════════════════════════════════════════
# 启动时后台初始化 RAG Pipeline（避免首次上传等 13 秒）
# ═══════════════════════════════════════════════════
@app.on_event("startup")
async def eager_init_rag_pipeline():
    """后台线程预热 RAG pipeline，不阻塞服务启动。"""
    import threading
    def _warmup():
        try:
            from backend.app.api.deps import get_rag_pipeline
            logger.info("[Startup] 后台预热 RAG 管道...")
            get_rag_pipeline()
            logger.info("[Startup] RAG 管道预热完成")
        except Exception as e:
            logger.warning(f"[Startup] RAG 管道预热失败（首次请求会重试）: {e}")
        # 预热 jieba 分词词典（首次加载 ~1s）
        try:
            import jieba
            jieba.initialize()
            logger.info("[Startup] jieba 词典预热完成")
        except Exception:
            logger.warning("[Startup] jieba 预热失败，分词可能较慢", exc_info=True)
    threading.Thread(target=_warmup, daemon=True, name="rag-warmup").start()


# ═══════════════════════════════════════════════════
# 启动时后台预热 MultiAgent（避免首请求 5-15s 图编译）
# ═══════════════════════════════════════════════════
@app.on_event("startup")
async def eager_init_multi_agent():
    """后台异步预热 MultiAgent 运行时。失败不阻塞服务。"""
    import threading
    def _warmup():
        try:
            from backend.app.api.deps import warmup_multi_agent
            logger.info("[Startup] 后台预热 MultiAgent 运行时...")
            if warmup_multi_agent():
                logger.info("[Startup] MultiAgent 预热完成")
        except Exception as e:
            logger.warning(f"[Startup] MultiAgent 预热异常（首请求会重试）: {e}")
    threading.Thread(target=_warmup, daemon=True, name="agent-warmup").start()


# ═══════════════════════════════════════════════════
# 启动时后台预热 NLI 模型（避免首请求 5-10s Faithfulness 延迟）
# ═══════════════════════════════════════════════════
@app.on_event("startup")
async def eager_init_nli_model():
    """后台预热 NLI 校验模型（~500MB），首请求 Faithfulness 校验无延迟。

    失败不阻塞启动，懒加载兜底（首次调用时仍会触发加载）。
    """
    import threading
    def _warmup():
        try:
            from backend.rag.guardrails.nli_checker import _get_nli_model
            logger.info("[Startup] 后台预热 NLI 模型...")
            _get_nli_model()
            logger.info("[Startup] NLI 模型预热完成")
        except Exception as e:
            logger.warning(f"[Startup] NLI 模型预热失败（首请求会重试）: {e}")
    threading.Thread(target=_warmup, daemon=True, name="nli-warmup").start()


# ═══════════════════════════════════════════════════
# 启动时计算运营指标（metadata 完整度）
# ═══════════════════════════════════════════════════
@app.on_event("startup")
async def eager_init_ops_metrics():
    """启动期计算 doc_metadata_coverage 运营指标（2026-08-11）。"""
    import threading
    def _warmup():
        try:
            from backend.observability.metrics import update_metadata_coverage
            update_metadata_coverage()
            logger.info("[Startup] doc_metadata_coverage 指标已更新")
        except Exception as e:
            logger.warning(f"[Startup] 运营指标初算失败: {e}")
    threading.Thread(target=_warmup, daemon=True, name="ops-metrics-warmup").start()


# ═══════════════════════════════════════════════════
# 启动时扫描过期文档（2026-08-11 P2 文档生命周期）
# ═══════════════════════════════════════════════════
@app.on_event("startup")
async def eager_init_lifecycle_scan():
    """启动期扫描过期文档 → 自动归档（2026-08-11 P2）。"""
    import threading
    def _scan():
        try:
            from backend.rag.indexing.doc_registry import DocumentRegistry
            reg = DocumentRegistry()
            reg.ensure_expire_at_column()  # 兼容老数据库
            archived = reg.archive_expired()
            if archived > 0:
                logger.info(f"[Startup] 文档生命周期扫描：归档 {archived} 个过期文档")
            else:
                logger.info("[Startup] 文档生命周期扫描：无过期文档")
        except Exception as e:
            logger.warning(f"[Startup] 文档生命周期扫描失败: {e}")
    threading.Thread(target=_scan, daemon=True, name="lifecycle-scan").start()


# ═══════════════════════════════════════════════════
# /ops 运营指标看板（HTML 自建，2026-08-11）
# ═══════════════════════════════════════════════════
_OPS_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>RAG 运营指标看板</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "Segoe UI", sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
  h1 { margin-bottom: 24px; font-size: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
  .card { background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }
  .card h2 { font-size: 14px; color: #94a3b8; margin-bottom: 12px; font-weight: 500; }
  .value { font-size: 36px; font-weight: 700; margin-bottom: 8px; }
  .threshold { font-size: 12px; color: #64748b; }
  .ok { color: #22c55e; }
  .warn { color: #eab308; }
  .bad { color: #ef4444; }
  .refresh { margin-bottom: 16px; padding: 10px 20px; background: #3b82f6; color: white; border: none; border-radius: 6px; cursor: pointer; }
</style>
</head>
<body>
<h1>📊 RAG 运营指标看板</h1>
<button class="refresh" onclick="loadMetrics()">🔄 刷新</button>
<div class="grid" id="metrics">
  <div class="card"><h2>加载中...</h2></div>
</div>
<script>
async function loadMetrics() {
  try {
    const res = await fetch('/metrics');
    const text = await res.text();
    const metrics = parseMetrics(text);
    render(metrics);
  } catch (e) {
    document.getElementById('metrics').innerHTML = '<div class="card">加载失败: ' + e + '</div>';
  }
}

function parseMetrics(text) {
  const out = {};
  text.split('\\n').forEach(line => {
    if (!line.startsWith('rag_') && !line.startsWith('feedback_') && !line.startsWith('doc_metadata_')) return;
    const m = line.match(/^(\\w+)\\s+([\\d.]+)$/);
    if (m) out[m[1]] = parseFloat(m[2]);
  });
  return out;
}

function render(m) {
  const grid = document.getElementById('metrics');
  grid.innerHTML = `
    <div class="card">
      <h2>RAG 命中率</h2>
      <div class="value ${ok(m.rag_hit_rate, 0.7)}">${pct(m.rag_hit_rate)}</div>
      <div class="threshold">阈值: > 70%</div>
    </div>
    <div class="card">
      <h2>RAG 拒答率</h2>
      <div class="value ${warn(m.rag_reject_rate, 0.3)}">${pct(m.rag_reject_rate)}</div>
      <div class="threshold">阈值: < 30%</div>
    </div>
    <div class="card">
      <h2>文档 metadata 完整度</h2>
      <div class="value ${ok(m.doc_metadata_coverage, 0.8)}">${pct(m.doc_metadata_coverage)}</div>
      <div class="threshold">阈值: > 80%</div>
    </div>
    <div class="card">
      <h2>用户反馈 👍 比例</h2>
      <div class="value ${ok(m.feedback_positive_rate, 0.7)}">${pct(m.feedback_positive_rate)}</div>
      <div class="threshold">阈值: > 70%</div>
    </div>
    <div class="card">
      <h2>RAG 查询总数</h2>
      <div class="value">${m.rag_query_total ?? 0}</div>
      <div class="threshold">累计</div>
    </div>
    <div class="card">
      <h2>用户反馈总数</h2>
      <div class="value">${m.feedback_total ?? 0}</div>
      <div class="threshold">累计</div>
    </div>
  `;
}

function pct(v) { return v == null ? '—' : (v * 100).toFixed(1) + '%'; }
function ok(v, t) { return v == null ? '' : (v >= t ? 'ok' : 'bad'); }
function warn(v, t) { return v == null ? '' : (v < t ? 'ok' : 'bad'); }

loadMetrics();
setInterval(loadMetrics, 10000);
</script>
</body>
</html>"""


@app.get("/ops", response_class=HTMLResponse)
async def ops_dashboard():
    """RAG 运营指标看板（HTML 自建，2026-08-11）。"""
    return _OPS_DASHBOARD_HTML


# ═══════════════════════════════════════════════════
# 启动时注册 Workflow + 定时任务
# ═══════════════════════════════════════════════════
@app.on_event("startup")
async def register_workflows_and_schedules():
    """注册所有 workflow + 启动定时调度器"""
    try:
        from backend.orchestration.workflow.registry import get_workflow_registry
        from backend.orchestration.workflow.scheduler import get_workflow_scheduler
        from backend.orchestration.workflows.daily_report import DailyReport
        from backend.orchestration.workflows.inventory_alert import InventoryAlert

        reg = get_workflow_registry()
        if reg.get("daily_report") is None:
            reg.register(DailyReport)
        if reg.get("inventory_alert") is None:
            reg.register(InventoryAlert)

        sched = get_workflow_scheduler()
        sched.register_daily("daily_report", hour=9, minute=0)
        sched.register_daily("inventory_alert", hour=8, minute=0)

        # 2026-08-11 P1 Golden Dataset 周自动评测（每周日 2:00 跑）
        def _run_weekly_eval():
            try:
                from backend.eval import run_golden_eval
                summary = run_golden_eval()
                logger.info(
                    f"[WeeklyEval] 完成: hit={summary['hit_rate']:.1%} "
                    f"pass={summary['pass_rate']:.1%} rej={summary['reject_rate']:.1%}"
                )
            except Exception as e:
                logger.error(f"[WeeklyEval] 失败: {e}")
        sched.register_cron("weekly_eval", _run_weekly_eval, "0 2 * * 0")

        sched.start()

        logger.info("[Startup] Workflow 定时任务注册完成")
    except Exception as e:
        logger.warning(f"[Startup] 定时任务注册失败（非致命）: {e}")


# ═══════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )