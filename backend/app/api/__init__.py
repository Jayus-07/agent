"""API 层 — FastAPI 路由集合

零侵入设计：所有 Agent 通过 api/deps.py 惰性导入现有模块，不修改任何业务代码。

启动:
    uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload

端点:
    POST /chat    — Multi-Agent 对话
    POST /sql     — SQL 安全查询
    POST /rag     — 知识库检索
    POST /report  — 报告生成
    GET  /health  — 健康检查
    GET  /docs    — Swagger UI
"""
