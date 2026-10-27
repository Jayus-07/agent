-- 115_http_api_tools.sql — HTTP API Tool 接入统一动态工具注册表（Batch 1）
-- 目标：外部 REST API 的"连接 + 工具定义"复用 ai.mcp_* 注册表与审核/授权/
--   快照/漂移体系（ADR-0011 扩展），不建与 MCP 不互通的第二套注册系统。
--   - 连接：ai.mcp_connections.transport 增列 'http_api'（base_url = 受批准
--     的 API base，SSRF 校验与 MCP 同一 url_guard 通道）；
--   - 工具：ai.mcp_tools.invocation JSONB = 受控调用定义
--     （method/path/query 模板/headers 模板/body 参数名）；MCP 工具为空；
--     invocation 变化计入 content_hash（漂移 → 重审），历史存 snapshots。
-- 回滚（按逆序）：
--   ALTER TABLE ai.mcp_tool_snapshots DROP COLUMN IF EXISTS invocation;
--   ALTER TABLE ai.mcp_tools DROP COLUMN IF EXISTS invocation;
--   ALTER TABLE ai.mcp_connections DROP CONSTRAINT IF EXISTS ck_mcp_conn_transport;
--   ALTER TABLE ai.mcp_connections ADD CONSTRAINT ck_mcp_conn_transport
--     CHECK (transport IN ('streamable_http'));

-- ── 连接传输类型：放宽为 MCP / HTTP API ─────────────────────
ALTER TABLE ai.mcp_connections DROP CONSTRAINT IF EXISTS ck_mcp_conn_transport;
ALTER TABLE ai.mcp_connections ADD CONSTRAINT ck_mcp_conn_transport
    CHECK (transport IN ('streamable_http', 'http_api'));

-- ── 工具调用定义（当前态 + 快照历史）────────────────────────
ALTER TABLE ai.mcp_tools
    ADD COLUMN IF NOT EXISTS invocation JSONB NOT NULL DEFAULT '{}';
ALTER TABLE ai.mcp_tool_snapshots
    ADD COLUMN IF NOT EXISTS invocation JSONB;
