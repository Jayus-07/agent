-- 118_mcp_health_monitor.sql — 连接健康巡检（Batch 5-3，决策 #16）
-- 目标：连接级「自动健康监测」的最小充分实现：
--   - 非副作用探活（http_api=HEAD 可达性；streamable_http=MCP 握手+tools/list，
--     只读语义，与 /admin/mcp/connections/{id}/test 完全同口径）；
--   - 断线退避重连：连续失败按 2^n 退避降低探活频率，探活成功即恢复
--     connected（**不动 enabled 管理开关**，重连 ≠ 重新启用）；
--   - 健康事件留痕：ai.mcp_health_events 供管理端「运行健康」Tab 呈现
--     时间线与影响面；7 天自动裁剪。
-- 不做的事（诚实边界）：不自动发现（契约漂移隔离仍走「发现 → schema_changed
-- → 执行拒绝 → 重新审核」既有链）；不改执行门；不自动启停连接。
-- 回滚（按逆序 DROP）：
--   DROP TABLE IF EXISTS ai.mcp_health_events;
--   ALTER TABLE ai.mcp_connections DROP COLUMN IF EXISTS last_checked_at,
--     DROP COLUMN IF EXISTS probe_fail_count;

ALTER TABLE ai.mcp_connections
    ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS probe_fail_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS ai.mcp_health_events (
    id            BIGSERIAL PRIMARY KEY,
    connection_id TEXT NOT NULL
        REFERENCES ai.mcp_connections(id) ON DELETE CASCADE,
    tenant_id     TEXT NOT NULL DEFAULT 'default',
    ok            BOOLEAN NOT NULL,
    error         TEXT NOT NULL DEFAULT '',
    elapsed_ms    INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL DEFAULT 'monitor',
        -- monitor（自动巡检）| manual（管理端「测试健康」按钮）
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_mcp_health_source CHECK (source IN ('monitor', 'manual'))
);
CREATE INDEX IF NOT EXISTS idx_mcp_health_events_conn
    ON ai.mcp_health_events(connection_id, created_at DESC);
