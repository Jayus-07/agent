-- 143_memory_product_scope.sql — 记忆键加产品维度（ADR-0012 第 1 期，2026-11-10）
--
-- 目标库：memory（agent_memory）
--
-- 背景（ADR-0012 / 审计 #68）：产品隔离此前靠调用方把 "customer_service::"
-- 前缀编码进 user_id 字符串（core/product_context.memory_user_id），数据库层
-- 无法按产品过滤；RAG 链调用点绕过换算导致跨产品串扰。本迁移把 product 提升为
-- 独立列：user_id 拆裸、唯一键全部纳入 product 维度。
--
-- 幂等：ADD COLUMN IF NOT EXISTS / DO 块判约束存在；唯一键重建先 DROP 再 CREATE。
-- 红线：不删除/不改写任何记忆正文；仅 user_id 剥离产品前缀（拆裸值语义等价，
-- 拆解前 (tenant, "customer_service::u") 与拆解后 (tenant, 'customer_service', "u")
-- 指向同一属主）。
-- 回滚：依赖备份恢复（forward-only，见 data-lifecycle 惯例）。

-- ── 1) 加列 ──────────────────────────────────────────────
ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS product VARCHAR(32) NOT NULL DEFAULT 'agent';
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS product VARCHAR(32) NOT NULL DEFAULT 'agent';
-- chat_messages：跨产品同 session_id 时消息行按 product 区分（无身份列，
-- 存量串写消息无法权威归类——保持 'agent' 与历史读取命名空间一致）。
ALTER TABLE public.chat_messages
    ADD COLUMN IF NOT EXISTS product VARCHAR(32) NOT NULL DEFAULT 'agent';
ALTER TABLE public.memory_extraction_jobs
    ADD COLUMN IF NOT EXISTS product VARCHAR(32) NOT NULL DEFAULT 'agent';
ALTER TABLE public.memory_turn_idempotency
    ADD COLUMN IF NOT EXISTS product VARCHAR(32) NOT NULL DEFAULT 'agent';

-- D9：抽取任务关联来源请求 trace（end_turn 写入；存量行 NULL）
ALTER TABLE public.memory_extraction_jobs
    ADD COLUMN IF NOT EXISTS source_trace_id VARCHAR(64) NULL;

-- ── 2) 回填：按前缀反解 product 并拆裸 user_id ────────────
-- "customer_service::" 恰 18 字符；sha256 长形态同前缀，同样剥。
UPDATE public.memory_records
    SET product = 'customer_service', user_id = substring(user_id FROM 19)
    WHERE user_id LIKE 'customer_service::%';
UPDATE public.chat_sessions
    SET product = 'customer_service', user_id = substring(user_id FROM 19)
    WHERE user_id LIKE 'customer_service::%';
-- chat_messages 无身份列：存量行保持 'agent'（历史串写消息的读取命名空间
-- 即 agent，行为一致），新行由 service 层按产品写入。
CREATE INDEX IF NOT EXISTS idx_chat_messages_product_session
    ON public.chat_messages (product, session_id);
UPDATE public.memory_extraction_jobs
    SET product = 'customer_service', user_id = substring(user_id FROM 19)
    WHERE user_id LIKE 'customer_service::%';
UPDATE public.memory_turn_idempotency
    SET product = 'customer_service', user_id = substring(user_id FROM 19)
    WHERE user_id LIKE 'customer_service::%';

-- ── 3) 唯一键 / 索引重建（全部纳入 product）────────────────

-- memory_records：048 唯一索引（active keyed 事实唯一）
DROP INDEX IF EXISTS public.uq_memory_active_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_active_key
    ON public.memory_records (tenant_id, product, user_id, memory_key)
    WHERE is_active = TRUE AND memory_key IS NOT NULL;

-- memory_records：048 普通索引
DROP INDEX IF EXISTS public.idx_memory_scope;
CREATE INDEX IF NOT EXISTS idx_memory_scope
    ON public.memory_records (tenant_id, product, user_id, is_active);

-- memory_records：088 画像索引 + scoped 唯一索引
DROP INDEX IF EXISTS public.idx_memory_profile_scope;
CREATE INDEX IF NOT EXISTS idx_memory_profile_scope
    ON public.memory_records (tenant_id, product, user_id, scope, domain, is_active);

DROP INDEX IF EXISTS public.uq_memory_active_scoped_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_active_scoped_key
    ON public.memory_records (tenant_id, product, user_id, scope, domain, memory_key)
    WHERE is_active = TRUE AND memory_key IS NOT NULL;

-- memory_extraction_jobs：089 表级唯一约束改复合（DO 块判存）
ALTER TABLE public.memory_extraction_jobs DROP CONSTRAINT IF EXISTS uq_memory_extraction_source;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_memory_extraction_source') THEN
        ALTER TABLE public.memory_extraction_jobs
            ADD CONSTRAINT uq_memory_extraction_source
            UNIQUE (tenant_id, product, user_id, source_message_id);
    END IF;
END $$;

DROP INDEX IF EXISTS public.idx_memory_extraction_session;
CREATE INDEX IF NOT EXISTS idx_memory_extraction_session
    ON public.memory_extraction_jobs (tenant_id, product, user_id, session_id);

-- memory_turn_idempotency：090 表级唯一约束改复合
ALTER TABLE public.memory_turn_idempotency DROP CONSTRAINT IF EXISTS uq_memory_turn_request;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_memory_turn_request') THEN
        ALTER TABLE public.memory_turn_idempotency
            ADD CONSTRAINT uq_memory_turn_request
            UNIQUE (tenant_id, product, user_id, session_id, idempotency_key_hash);
    END IF;
END $$;

DROP INDEX IF EXISTS public.idx_memory_turn_session;
CREATE INDEX IF NOT EXISTS idx_memory_turn_session
    ON public.memory_turn_idempotency (tenant_id, product, user_id, session_id);

-- chat_sessions：002 列级 UNIQUE(session_id)（自动命名 chat_sessions_session_id_key）
-- 改为 (product, session_id) 复合——跨产品同 session_id 各建各的行，运行时属主
-- 校验（SessionOwnerMismatch）继续承担同产品内的越权防护。
-- 顺序约束：090 的 fk_memory_turn_session 引用该唯一索引，须先卸后装（改复合）。
ALTER TABLE public.memory_turn_idempotency DROP CONSTRAINT IF EXISTS fk_memory_turn_session;
ALTER TABLE public.chat_sessions DROP CONSTRAINT IF EXISTS chat_sessions_session_id_key;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes
                   WHERE schemaname = 'public' AND indexname = 'uq_chat_sessions_product_session') THEN
        CREATE UNIQUE INDEX uq_chat_sessions_product_session
            ON public.chat_sessions (product, session_id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_memory_turn_session') THEN
        ALTER TABLE public.memory_turn_idempotency
            ADD CONSTRAINT fk_memory_turn_session
            FOREIGN KEY (product, session_id)
            REFERENCES public.chat_sessions (product, session_id)
            ON DELETE CASCADE;
    END IF;
END $$;

-- 092 租户索引加 product
DROP INDEX IF EXISTS public.idx_chat_sessions_tenant_user;
CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_user
    ON public.chat_sessions (tenant_id, product, user_id);

-- 抽取任务按来源 trace 关联查询
CREATE INDEX IF NOT EXISTS idx_memory_extraction_source_trace
    ON public.memory_extraction_jobs (source_trace_id)
    WHERE source_trace_id IS NOT NULL;
