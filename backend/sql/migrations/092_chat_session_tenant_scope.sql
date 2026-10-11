-- 092：L2 会话按可信 tenant/user 双重隔离。
-- 旧会话仅能通过同库 auth.users 的权威归属映射租户；无法映射的记录隔离在
-- quarantine，运行时身份归一不会产出该值，因此不会猜测归属或串入真实租户。
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'quarantine';

UPDATE public.chat_sessions AS s
SET tenant_id = u.tenant_id
FROM auth.users AS u
WHERE s.user_id = u.id::text
  AND s.tenant_id = 'quarantine'
  AND u.tenant_id IS NOT NULL
  AND btrim(u.tenant_id) <> ''
  AND length(u.tenant_id) <= 64
  AND u.tenant_id <> 'quarantine';

COMMENT ON COLUMN public.chat_sessions.tenant_id IS
    'L2 会话租户归属；旧会话仅按 auth.users 权威映射，quarantine 表示无法确定的历史归属';

CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_user
    ON public.chat_sessions (tenant_id, user_id);
