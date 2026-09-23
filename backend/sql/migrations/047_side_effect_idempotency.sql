-- 047_side_effect_idempotency.sql（target: memory）
-- Phase2 Step6（Side-effect Idempotency）：
--   1) ai.idempotency_records 补 owner_execution_id——logical key 之外的
--      执行者身份（execution_id 每次拾取换发，只能做 owner，不能做 key）。
--      complete/fail 以 (lease_id, owner_execution_id) 双条件 CAS，
--      旧 execution 不得覆盖新 execution 已接管的记录（Step4 owner CAS 同思路）。
--   2) stale claim 排查索引——reconcile/运维查询扫描「running 且租约已过期」。
--   3) ai.side_effect_probe 实机验收探针表——Step6 T1-T8 的可数副作用载体
--      （每次真实执行插一行 (probe_key, execution_id)，天然不幂等，
--      因此能证明「attempts > 1 而 effect = 1」；仅供测试任务写入）。

ALTER TABLE ai.idempotency_records
    ADD COLUMN IF NOT EXISTS owner_execution_id VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_idempotency_records_stale_claim
    ON ai.idempotency_records (status, lease_expires_at)
    WHERE status = 'running';

CREATE TABLE IF NOT EXISTS ai.side_effect_probe (
    probe_key     VARCHAR(128) NOT NULL,
    execution_id  VARCHAR(64)  NOT NULL,
    payload       JSONB,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (probe_key, execution_id)
);
