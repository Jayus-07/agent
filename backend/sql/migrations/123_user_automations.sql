-- 123_user_automations.sql — 用户自动任务（决策 #27）
-- 目标：用户在用户端管理自己的自动任务（cron 定时 / webhook 事件触发），
--   到期或回调后复用既有执行链（tasks.execute_agent：create_task +
--   enqueue_task），不新建第二执行器。
-- 语义边界（v1，诚实声明）：
--   - confirm_policy 仅允许 'auto'：自动执行按纯只读语义理解（payload
--     只允许纯问题文本 question）；写操作不因自动化获得绕过——既有
--     Tool Governance / 确认门在执行链内照常生效。'require_confirmation'
--     词表预留，服务层写入时拒绝（400）；
--   - webhook_secret_cipher 存加密密文（Fernet，照 mcp_connection_credentials
--     模式）：HMAC-SHA256 验签需要原始密钥（哈希无法验签），密钥经
--     shared/crypto 加密落库，明文只在创建响应返回一次；
--   - 去重 = ai.automation_events(automation_id, external_event_id) 部分唯一
--     索引（external_event_id <> '' 时生效），cron 用时间窗、webhook 用
--     X-Event-Id 头；
--   - 调度（cron 到期扫描）由 agent-beat 单实例驱动（60s 扫描 + 原子认领），
--     多实例安全由条件 UPDATE + 唯一索引双保险。
-- 回滚（按逆序 DROP）：
--   DROP TABLE IF EXISTS ai.automation_events;
--   DROP TABLE IF EXISTS ai.user_automations;

CREATE TABLE IF NOT EXISTS ai.user_automations (
    id                    TEXT PRIMARY KEY,              -- auto_<hex>
    user_id               TEXT NOT NULL,                 -- 归属用户（CRUD 隔离强制，user_id 强制取当前身份）
    tenant_id             TEXT NOT NULL DEFAULT 'default',
    name                  TEXT NOT NULL,
    trigger_type          TEXT NOT NULL DEFAULT 'cron',
        -- cron（定时）| webhook（业务事件回调）
    cron_expr             TEXT NOT NULL DEFAULT '',      -- 5 字段 cron（UTC）；trigger_type='cron' 时必填
    webhook_secret_cipher TEXT NOT NULL DEFAULT '',      -- enc:<fernet>（HMAC-SHA256 签名密钥密文）
    payload               JSONB NOT NULL DEFAULT '{}',   -- {"question": "..."}（v1 仅纯问题文本）
    confirm_policy        TEXT NOT NULL DEFAULT 'auto',
        -- auto（v1 唯一允许值：自动执行只读语义）| require_confirmation（预留未开放）
    enabled               BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at           TIMESTAMPTZ,                   -- 最近派发时刻（cron 窗口对齐分钟）
    last_status           TEXT NOT NULL DEFAULT '',      -- 最近事件终态（queued/succeeded/failed/skipped_duplicate）
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_user_automations_trigger
        CHECK (trigger_type IN ('cron', 'webhook')),
    CONSTRAINT ck_user_automations_confirm_policy
        CHECK (confirm_policy IN ('auto', 'require_confirmation')),
    CONSTRAINT uq_user_automations_identity UNIQUE (user_id, name, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_user_automations_cron_enabled
    ON ai.user_automations (enabled, trigger_type)
    WHERE trigger_type = 'cron';
CREATE INDEX IF NOT EXISTS idx_user_automations_tenant
    ON ai.user_automations (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ai.automation_events (
    id                BIGSERIAL PRIMARY KEY,
    automation_id     TEXT NOT NULL
        REFERENCES ai.user_automations(id) ON DELETE CASCADE,
    source            TEXT NOT NULL,
        -- cron（定时扫描派发）| webhook（外部回调）
    external_event_id TEXT NOT NULL DEFAULT '',
        -- 去重键：cron = cron:<epoch_minute>，webhook = X-Event-Id 头；
        -- 空串表示不去重（手动/内部路径不使用）
    payload           JSONB NOT NULL DEFAULT '{}',   -- 触发载荷 / 派发结果（task_id 等）
    status            TEXT NOT NULL DEFAULT 'queued',
        -- queued | running | succeeded | failed | skipped_duplicate
    detail            TEXT NOT NULL DEFAULT '',      -- 失败原因 / 任务 id 等人读信息
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_automation_events_source CHECK (source IN ('cron', 'webhook')),
    CONSTRAINT ck_automation_events_status
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped_duplicate'))
);

-- 部分唯一索引 = 事件去重（同 automation 下 external_event_id 非空必须唯一）
CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_events_dedup
    ON ai.automation_events (automation_id, external_event_id)
    WHERE external_event_id <> '';
CREATE INDEX IF NOT EXISTS idx_automation_events_recent
    ON ai.automation_events (automation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_automation_events_status
    ON ai.automation_events (status, created_at DESC);
