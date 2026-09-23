-- =====================================================
-- 048_memory_scope_and_versioning.sql — Memory scope + 事实版本管理
-- （STOP C，Memory Production Closure）
--
-- 背景：STOP A/B 审计确认（docs/2026-09-24-Memory-Production-Closure-STOPA-Audit.md
--       / STOPB-Provenance.md）：①memory_records 无 tenant_id，隔离只靠
--       user_id（P0-5）；②事实版本管理纯靠 embedding 相似度（P0-1/P0-2/P0-3）。
--
-- 变更：
--   tenant_id        VARCHAR(64) NOT NULL DEFAULT 'quarantine'
--                    Memory 正式 scope = (tenant_id, user_id)。
--                    backfill 权威来源 = auth.users（同库，migration 008 起）：
--                      memory_records.user_id = auth.users.id::text
--                    实库核验（2026-09-24）：user '42'（122 条）命中
--                    auth.users.id=42（tenant_id='default'，status=1）；
--                    'default'(156)/'p4-eval'(13) 为未认证兜底/评测桶，
--                    无身份表行 → 无法权威映射 → 按任务书 §7 归入
--                    'quarantine' 保留哨兵（永不被任何真实租户查询命中，
--                    即确定性隔离下线，不做猜测式归属）。
--                    运行时归一：normalize_tenant_id() 把未声明租户归一为
--                    'default'（本部署唯一真实租户，与 auth.users 现值一致），
--                    任何查询永远携带具体 tenant 值，不存在"缺省查全表"。
--   memory_key       VARCHAR(128) NULL — 属性身份（如 project.main_llm），
--                    表达"这是同一属性的不同版本"；legacy 不做 LLM 回填，
--                    一律 NULL（§21）。非法 key 由代码层置 NULL，不入库。
--   structured_value VARCHAR(256) NULL — 规范化属性值（如 doubao），
--                    与 memory_key 成对出现（有 key 无合法值 → 双双 NULL）。
-- 索引：
--   uq_memory_active_key  UNIQUE (tenant_id, user_id, memory_key)
--                         WHERE is_active AND memory_key IS NOT NULL
--                         —— keyed memory「同 scope 同 key 最多一个 active」
--                         的 DB 级 invariant（I1/I12），partial unique 不约束
--                         历史 superseded 行（保留全部版本链）。
--   idx_memory_scope      (tenant_id, user_id, is_active) — 检索/维护常用过滤。
--   不建 HNSW/IVFFlat（向量索引属 retrieval/performance 后续）。
-- 幂等：可重复执行；backfill 为确定性条件 UPDATE（重复执行 no-op）。
-- 红线：不修改 content/embedding/origin/confidence/importance/is_active/
--       superseded_by 任何既有值（§70）。
-- 目标库：agent_memory
-- =====================================================

ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'quarantine';

ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS memory_key VARCHAR(128);

ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS structured_value VARCHAR(256);

-- 权威 backfill：身份表映射（auth.users.id::text = memory_records.user_id）
UPDATE public.memory_records m
SET tenant_id = u.tenant_id
FROM auth.users u
WHERE m.user_id = u.id::text
  AND u.tenant_id IS NOT NULL
  AND u.tenant_id <> ''
  AND m.tenant_id = 'quarantine';

-- 无法权威映射的行保持 'quarantine'（确定性隔离，非猜测归属）
COMMENT ON COLUMN public.memory_records.tenant_id IS
    'Memory scope 租户维度：auth.users.tenant_id 权威 backfill；quarantine=无身份表映射的 legacy 行（永久隔离）；运行时未声明租户归一为 default';
COMMENT ON COLUMN public.memory_records.memory_key IS
    '属性身份（dot-separated snake_case），同 key 不同值构成事实版本链；NULL=无稳定结构化身份（走语义去重）';
COMMENT ON COLUMN public.memory_records.structured_value IS
    '规范化属性值（NFKC+trim+lower），与 memory_key 成对；NULL=无结构化值';

CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_active_key
    ON public.memory_records (tenant_id, user_id, memory_key)
    WHERE is_active = TRUE AND memory_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_scope
    ON public.memory_records (tenant_id, user_id, is_active);
