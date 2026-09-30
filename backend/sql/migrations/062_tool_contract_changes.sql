-- 061_tool_contract_changes.sql — Tool 契约变更历史台账（治理验收 #9）
-- 目标：契约 lock 的 diff 此前只在 gen_tool_contract_lock --check 的即时
--   输出里，不落库——管理端无法回答「这个 Tool 的契约何时变过、怎么变的」。
-- 本表由生成器在检测到变更时自动追加（soft-fail 不阻断生成）：
--   classification ∈ BREAKING / DEGRADED / COMPATIBLE / INIT（首次生成）
--   changed_tools  = [{tool, change_kind, detail}]（与 --check 输出同构）
-- 查询：GET /api/admin/tools/changes（管理端 Tool 治理中心历史 tab 数据源）。
-- 幂等：IF NOT EXISTS。回滚：DROP TABLE。

CREATE TABLE IF NOT EXISTS ai.tool_contract_changes (
    id             BIGSERIAL PRIMARY KEY,
    from_lock_hash TEXT NOT NULL DEFAULT '',
    to_lock_hash   TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL,
    changed_tools  JSONB NOT NULL DEFAULT '[]',
    tool_count     INTEGER NOT NULL DEFAULT 0,
    git_sha        TEXT NOT NULL DEFAULT '',
    detected_by    TEXT NOT NULL DEFAULT 'ci',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_tool_contract_class CHECK (
        classification IN ('BREAKING', 'DEGRADED', 'COMPATIBLE', 'INIT'))
);

CREATE INDEX IF NOT EXISTS idx_tool_contract_changes_ts
    ON ai.tool_contract_changes(created_at DESC);
