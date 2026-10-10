-- =====================================================
-- 040_chat_sessions_summary_frontier.sql — L5 AutoCompact 摘要水位线
-- 背景：上下文预算管理 Phase 3（2026-09-22）。L5 AutoCompact 需要
--       「增量摘要」水位线：记录当前 summary 已覆盖到哪一条 chat_message，
--       避免每次摘要都把整段会话重新发给 LLM（成本/延迟翻倍）。
--       预算策略见 docs/architecture/ai-runtime.md#上下文预算与跨请求状态。
-- 红线：L5 只推进 active prompt projection 的摘要水位，绝不删除/改写
--       chat_messages 原始行。
-- 目标库：agent_memory
-- 幂等：可重复执行
-- =====================================================

-- 摘要覆盖游标：summary 已总结到的最新 chat_messages.id；NULL = 尚无增量摘要
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS summary_through_message_id INTEGER;

-- 摘要自身 token 数（观测：摘要膨胀监控 / 注入成本预估）
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS summary_token_count INTEGER;

-- 摘要最后更新时间（观测：摘要新鲜度）
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS summary_updated_at TIMESTAMPTZ;

COMMENT ON COLUMN public.chat_sessions.summary_through_message_id IS
    'L5增量摘要水位线：summary已覆盖到的最新chat_messages.id（NULL=无增量摘要）';
COMMENT ON COLUMN public.chat_sessions.summary_token_count IS
    'L5摘要自身token数（观测用）';
COMMENT ON COLUMN public.chat_sessions.summary_updated_at IS
    'L5摘要最后更新时间（观测用）';
