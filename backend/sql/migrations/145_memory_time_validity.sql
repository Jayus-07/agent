-- 145_memory_time_validity.sql — 记忆事实时间有效性（ADR-0012 第 2 期 D3，2026-11-10）
--
-- 目标库：memory（agent_memory）
--
-- 背景：D3 事实带时间有效性，"失效而非删除"。valid_from = 事实生效时刻
-- （首建写入，存量回填 created_at）；invalid_at = 失效时刻（supersede /
-- 矛盾失效 / dreaming 合并时与 is_active=False 双写）。失效行不参与召回，
-- 但行保留 + superseded_by 版本链可审计还原。
-- 幂等：ADD COLUMN IF NOT EXISTS；回填仅写 NULL；DEFAULT/NOT NULL 重复执行无害。
-- 红线：不删除/不改写任何记忆正文。回滚：依赖备份恢复（forward-only）。
-- 时间列形态与表内既有列一致（timestamp without time zone，写入按 UTC 约定）；
-- DEFAULT 显式取 UTC，防会话时区漂移。

ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS invalid_at TIMESTAMP WITHOUT TIME ZONE;

-- 存量回填：事实自写入起有效
UPDATE public.memory_records SET valid_from = created_at WHERE valid_from IS NULL;

ALTER TABLE public.memory_records
    ALTER COLUMN valid_from SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE public.memory_records
    ALTER COLUMN valid_from SET NOT NULL;
