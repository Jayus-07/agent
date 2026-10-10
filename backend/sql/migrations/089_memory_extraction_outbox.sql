-- 089: 与聊天 turn 同事务写入的 L3 提取工作账本；队列只传 job UUID。
CREATE TABLE IF NOT EXISTS public.memory_extraction_jobs (
    id UUID PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    source_message_id INTEGER NOT NULL,
    assistant_message_id INTEGER NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_enqueued_at TIMESTAMPTZ NULL,
    claimed_at TIMESTAMPTZ NULL,
    last_error_code VARCHAR(64) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_memory_extraction_source
        UNIQUE (tenant_id, user_id, source_message_id),
    CONSTRAINT ck_memory_extraction_status
        CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    CONSTRAINT ck_memory_extraction_attempts CHECK (attempts >= 0)
);

CREATE INDEX IF NOT EXISTS idx_memory_extraction_dispatch
    ON public.memory_extraction_jobs (status, last_enqueued_at);
CREATE INDEX IF NOT EXISTS idx_memory_extraction_session
    ON public.memory_extraction_jobs (tenant_id, user_id, session_id);

COMMENT ON TABLE public.memory_extraction_jobs IS
    'L3 提取持久工作账本；正文留在 chat_messages，Celery 仅传 job UUID';
