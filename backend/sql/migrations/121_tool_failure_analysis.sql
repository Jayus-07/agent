-- 121_tool_failure_analysis.sql — AI 失败分析（决策 #20）
-- 目标：Tool 失败「聚类归因 + 优化建议 + 候选评测用例」的最小充分存储。
--   管理端 POST /admin/tools/failure-analysis 把时间窗内（tool, error_class）
--   聚类统计快照送 LLM，产出归因/建议/候选用例后整体落本表；只生成建议，
--   不自动应用/改写任何正式配置（Prompt/语义契约/Golden 一概不动）。
-- 语义（诚实边界）：
--   - input_summary = 聚类统计快照（计数 + 截断 300 字符的代表性错误摘要），
--     绝不含用户参数原文与凭据（送 LLM 的就是这份数据）；
--   - result = LLM 完整分析 JSON（clusters：attribution / suggestions /
--     candidate_eval_cases），候选评测用例仅供参考，应用需走评测中心审核；
--   - LLM 不可用或解析失败时不落库（不伪造分析结果）。
-- 保留策略：分析记录 7 天后参考价值有限；本迁移不做自动清理（与本库其他
--   治理表口径一致），如需回收由运维按
--   created_at < now() - interval '7 days' 手动裁剪。
-- 回滚（按逆序 DROP）：
--   DROP TABLE IF EXISTS ai.tool_failure_analyses;

CREATE TABLE IF NOT EXISTS ai.tool_failure_analyses (
    id             TEXT PRIMARY KEY,            -- tfa_<hex>
    tenant_id      TEXT NOT NULL DEFAULT 'default',
    window_minutes INTEGER NOT NULL,
        -- 聚类输入的回溯时间窗（分钟，5-1440）
    input_summary  JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- 聚类统计快照：groups=[{tool, error_class, count, samples[]}]，
        -- samples 为截断 300 字符的错误摘要（URL 已清洗），不含用户参数原文
    result         JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- LLM 完整分析 JSON：clusters=[{tool, error_class, attribution,
        -- suggestions[], candidate_eval_cases[]}]
    model          TEXT NOT NULL DEFAULT '',
        -- 生成所用模型（空聚类跳过 LLM 时为空串，如实反映）
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tool_failure_analyses_tenant
    ON ai.tool_failure_analyses(tenant_id, created_at DESC);
