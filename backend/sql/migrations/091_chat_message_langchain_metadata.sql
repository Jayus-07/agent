-- 091：L2 保存 LangChain 消息类型及工具调用元数据，兼容既有 user/assistant 行。
ALTER TABLE public.chat_messages
    ADD COLUMN IF NOT EXISTS message_type VARCHAR(32);
ALTER TABLE public.chat_messages
    ADD COLUMN IF NOT EXISTS message_metadata JSONB;

COMMENT ON COLUMN public.chat_messages.message_type IS
    'LangChain 消息类型（human/ai/tool）；NULL 表示兼容旧文本消息';
COMMENT ON COLUMN public.chat_messages.message_metadata IS
    '不含正文和消息 ID 的 LangChain JSON 元数据，用于恢复工具调用协议';
