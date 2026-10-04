-- 073_travel_decision_idempotency.sql — 决策留痕幂等（M 验收 #55，2026-10-04）
--
-- 为什么存在：decision POST 无幂等键时，双击/重试会落重复记录。
-- 部分唯一索引：同一会话内「同决策+同版本+同前端轮次」视为同一次
-- 决策（client_run_id 为空的调用不受约束——服务端/脚本调用无轮次概念）。
-- 应用侧：decision_store INSERT ON CONFLICT DO NOTHING + 回查既有 id。
-- 回滚（down）：
--   DROP INDEX IF EXISTS uq_travel_decision_idem;
--   （索引可安全回滚；travel_decision_audit 表本体归 072 迁移管）

CREATE UNIQUE INDEX IF NOT EXISTS uq_travel_decision_idem
    ON travel_decision_audit (conversation_id, decision, plan_version, client_run_id)
    WHERE client_run_id <> '';
