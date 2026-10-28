-- 119_tool_discovery_policy.sql — 管理端 Tool Discovery 策略（决策 #18，B1）
-- 目标：工具发现（候选解析）的策略表 + 只读 DiscoveryService + 模拟器的
--   最小充分存储：单表承载各租户的发现策略（模式 / top_k / 轮数 / 费用
--   预算 / 超时 / prompt_key），无 active 行时代码内默认值兜底。
-- 语义（B1 诚实边界）：
--   - 本表只被管理端读写与模拟器读取，B1 不进运行时路由链路
--     （/agent 路由行为零改动）；「生效策略」= active 行，upsert 时
--     同租户其余行落 active=false，保证单 active 不变量；
--   - cost_budget_milliunits = 单次发现的费用预算（毫单位），模拟器
--     仅展示，不做扣费。
-- 回滚（按逆序 DROP）：
--   DROP TABLE IF EXISTS ai.tool_discovery_policy;

CREATE TABLE IF NOT EXISTS ai.tool_discovery_policy (
    id            TEXT PRIMARY KEY,               -- disc_pol_<hex>
    name          TEXT NOT NULL,
    active        BOOLEAN NOT NULL DEFAULT false,
    mode          TEXT NOT NULL DEFAULT 'adaptive',
        -- adaptive（灰区自适应）| reasoning（仅推理档）| fast（仅 Fast Path）
    top_k         INTEGER NOT NULL DEFAULT 3,
    max_rounds    INTEGER NOT NULL DEFAULT 2,
    cost_budget_milliunits INTEGER NOT NULL DEFAULT 50,
        -- 单次发现费用预算（毫单位）
    timeout_ms    INTEGER NOT NULL DEFAULT 8000,
    prompt_key    TEXT NOT NULL DEFAULT 'router.tool_selector',
    tenant_id     TEXT NOT NULL DEFAULT 'default',
    created_by    TEXT NOT NULL DEFAULT '',
    updated_by    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_tool_disc_policy_mode
        CHECK (mode IN ('adaptive', 'reasoning', 'fast')),
    CONSTRAINT uq_tool_disc_policy_name UNIQUE (name)
);
CREATE INDEX IF NOT EXISTS idx_tool_disc_policy_tenant
    ON ai.tool_discovery_policy(tenant_id, active DESC, updated_at DESC);
