-- 078_cs_handoff_state_alignment.sql
-- STOP CS-A P0-6：handoffs.handoff_state 与 Python HandoffState 枚举对齐。
--
-- 背景（审计 2026-10-06 §六）：DB 列 default/注释含第 7 个值 ``initiated``，
-- Python HandoffState 没有它 —— 读到即 ValueError，曾被调用方兜底吞成
-- ai_active（未知状态静默当正常状态）。本迁移：
--   1. 历史数据收敛：initiated → ai_active（语义等价：AI 接管中）；
--   2. 删除非法 default（新行 default 改为 ai_active，与 ORM 一致）；
--   3. 加 CHECK 约束：数据库允许的状态集合 = canonical 枚举，脏数据从此
--      在写入层被拒绝（fail loud），不再依赖读取侧兜底。
-- 可回滚：约束名固定，drop constraint + 恢复 default 即可（initiated 历史
-- 值不可逆，回滚后也不再产生新 initiated）。

-- 1) 历史数据收敛（幂等：无 initiated 行时 0 rows affected）
UPDATE customer_service.handoffs
SET handoff_state = 'ai_active',
    updated_at = NOW()
WHERE handoff_state = 'initiated';

-- 2) 删除非法 default，对齐 canonical 枚举首状态
ALTER TABLE customer_service.handoffs
    ALTER COLUMN handoff_state SET DEFAULT 'ai_active';

-- 3) CHECK 约束：禁止幽灵状态再写入
ALTER TABLE customer_service.handoffs
    DROP CONSTRAINT IF EXISTS ck_cs_handoff_state_enum;
ALTER TABLE customer_service.handoffs
    ADD CONSTRAINT ck_cs_handoff_state_enum
    CHECK (handoff_state IN (
        'ai_active',
        'handoff_requested',
        'waiting_human',
        'agent_offered',
        'human_active',
        'closed'
    ));
