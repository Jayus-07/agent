-- 088: 三层记忆用户域/确认状态/显式版本。仅增加元数据并回填，不改正文或向量。
ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS scope VARCHAR(24) NOT NULL DEFAULT 'user_domain';
ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS domain VARCHAR(32) NOT NULL DEFAULT 'general';
ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS verification_status VARCHAR(24) NOT NULL DEFAULT 'legacy';

UPDATE public.memory_records
SET scope = CASE
        WHEN memory_key LIKE 'response.%' THEN 'user_global'
        ELSE 'user_domain'
    END,
    domain = CASE split_part(COALESCE(memory_key, ''), '.', 1)
        WHEN 'travel' THEN 'travel'
        WHEN 'trip' THEN 'travel'
        WHEN 'hotel' THEN 'travel'
        WHEN 'tourism' THEN 'travel'
        WHEN 'customer_service' THEN 'customer_service'
        WHEN 'support' THEN 'customer_service'
        WHEN 'ticket' THEN 'customer_service'
        WHEN 'rag' THEN 'knowledge'
        WHEN 'knowledge' THEN 'knowledge'
        WHEN 'document' THEN 'knowledge'
        WHEN 'sql' THEN 'sql'
        WHEN 'data' THEN 'sql'
        WHEN 'business' THEN 'business'
        WHEN 'product' THEN 'business'
        ELSE 'general'
    END,
    verification_status = CASE origin
        WHEN 'explicit' THEN 'verified'
        WHEN 'inferred' THEN 'pending'
        ELSE 'legacy'
    END;

WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY tenant_id, user_id, scope, domain, memory_key
               ORDER BY created_at, id
           )::INTEGER AS memory_version
    FROM public.memory_records
    WHERE memory_key IS NOT NULL
)
UPDATE public.memory_records AS m
SET version = ranked.memory_version
FROM ranked
WHERE m.id = ranked.id
  AND m.version <> ranked.memory_version;

CREATE INDEX IF NOT EXISTS idx_memory_profile_scope
    ON public.memory_records (tenant_id, user_id, scope, domain, is_active);
CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_active_scoped_key
    ON public.memory_records (tenant_id, user_id, scope, domain, memory_key)
    WHERE is_active = TRUE AND memory_key IS NOT NULL;

COMMENT ON COLUMN public.memory_records.scope IS
    '服务端授权范围：user_global、user_domain、session、tenant_shared；不得由 LLM 决定';
COMMENT ON COLUMN public.memory_records.domain IS
    '记忆所属业务域；默认 general，域映射由服务端 key 白名单确定';
COMMENT ON COLUMN public.memory_records.version IS
    '同一 tenant/user/scope/domain/key 的单调事实版本，删除屏障也占用版本号';
COMMENT ON COLUMN public.memory_records.verification_status IS
    'verified=用户明确确认，pending=待确认推断，legacy=迁移前存量，deleted=最小删除屏障';
