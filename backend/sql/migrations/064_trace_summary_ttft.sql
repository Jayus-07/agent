-- 064_trace_summary_ttft.sql — TTFT 落库列（M13 尾项 / 治理台账 D13）
-- 目标：chat 流式首 token 延迟（TTFT）此前只在 Prometheus 直方图
--   （chat_ttft_seconds）有分布、无逐行记录——管理端答不了「这个会话/
--   这个模型当时的首字延迟是多少」。本列由 SSE API 层收尾旁路 UPDATE
--   补写（TTFT 与 runner 的 trace 分属不同线程，桥接偏差已记台账 D13；
--   save_dict 的 ON CONFLICT UPDATE SET 不含本列，任何写入时序互不清值）。
-- NULL 语义：未收到任何 delta（流式前失败/纯 status 流）或旁路迟到，
--   不回填不猜测。
-- 幂等：IF NOT EXISTS。回滚：ALTER TABLE trace_summary DROP COLUMN ttft_ms。

ALTER TABLE trace_summary ADD COLUMN IF NOT EXISTS ttft_ms INTEGER;

CREATE INDEX IF NOT EXISTS idx_trace_summary_ttft
    ON trace_summary (created_at DESC) WHERE ttft_ms IS NOT NULL;
