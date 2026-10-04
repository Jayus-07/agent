-- 065_prompt_runtime_hot_reload.sql — Prompt Runtime epoch 与补偿事件
-- DB 是运行时变更计数的持久化事实源；Redis 只承载跨进程传播信号。
-- 幂等：可重复执行；初始 epoch 固定为 1。

CREATE TABLE IF NOT EXISTS prompt_runtime_state (
    id           BIGINT PRIMARY KEY,
    global_epoch BIGINT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by   VARCHAR(128) NOT NULL DEFAULT 'system',
    CONSTRAINT ck_prompt_runtime_epoch_nonnegative CHECK (global_epoch >= 1)
);

INSERT INTO prompt_runtime_state (id, global_epoch, updated_by)
VALUES (1, 1, 'migration-065')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS prompt_reload_events (
    id          BIGSERIAL PRIMARY KEY,
    epoch       BIGINT NOT NULL,
    event_type  VARCHAR(64) NOT NULL,
    status      VARCHAR(16) NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_prompt_reload_event_status
        CHECK (status IN ('pending', 'published', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_prompt_reload_events_pending
    ON prompt_reload_events(status, created_at)
    WHERE status IN ('pending', 'failed');
