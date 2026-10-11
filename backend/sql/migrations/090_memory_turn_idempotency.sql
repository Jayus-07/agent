-- 090: 对话轮次收据与 L2 消息、L3 outbox 同事务提交，防重试重复落库。
CREATE TABLE IF NOT EXISTS public.memory_turn_idempotency (
    id UUID PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    idempotency_key_hash CHAR(64) NOT NULL,
    question_hash CHAR(64) NOT NULL,
    user_message_id INTEGER NOT NULL,
    assistant_message_id INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_memory_turn_request
        UNIQUE (tenant_id, user_id, session_id, idempotency_key_hash),
    CONSTRAINT fk_memory_turn_session
        FOREIGN KEY (session_id) REFERENCES public.chat_sessions(session_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_memory_turn_user_message
        FOREIGN KEY (user_message_id) REFERENCES public.chat_messages(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_memory_turn_assistant_message
        FOREIGN KEY (assistant_message_id) REFERENCES public.chat_messages(id)
        ON DELETE CASCADE,
    CONSTRAINT ck_memory_turn_hashes
        CHECK (idempotency_key_hash ~ '^[0-9a-f]{64}$'
               AND question_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_memory_turn_session
    ON public.memory_turn_idempotency (tenant_id, user_id, session_id);

COMMENT ON TABLE public.memory_turn_idempotency IS
    'L2 对话轮次幂等收据；不保留请求键或问题正文，随会话删除级联清理';
