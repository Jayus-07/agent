-- =====================================================
-- 044_chat_sessions_summary_version.sql — L5 摘要水位线 CAS 版本号
-- 背景：Context Budget 生产加固 STOP C（2026-09-23）。
--       save_summary_state 此前为无条件 UPDATE：多 FastAPI worker /
--       多副本并发对同一 session 做增量摘要时，旧摘要（较小水位线）
--       可能后写覆盖新摘要。加 summary_version 做乐观并发控制，
--       配合 WHERE summary_through_message_id = :expected 的 CAS 写。
-- 红线不变：只推进 active prompt projection 的摘要水位，
--       绝不删除/改写 chat_messages 原始行。
-- 目标库：agent_memory
-- 幂等：可重复执行；存量行经 DEFAULT 0 回填，兼容旧数据
-- =====================================================

-- 摘要版本号（乐观并发）：每次 CAS 写入成功 +1；0 = 从未写过
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS summary_version INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.chat_sessions.summary_version IS
    'L5摘要乐观并发版本号：CAS写入成功+1（0=从未写过）';
