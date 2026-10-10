-- =====================================================
-- 047_memory_provenance.sql — Memory provenance（STOP B，Memory Production Closure）
--
-- 背景：持久记忆需要区分来源，并能追溯到产生该事实的用户消息；
--       assistant 回答不能与用户明确提供的事实混为同一来源。
--
-- 变更：
--   origin             VARCHAR(16) NOT NULL DEFAULT 'legacy'
--                      写入通道语义：legacy=历史记录（存量 291 条迁移后统一标记，
--                      不伪装成 inferred/explicit）｜inferred=后台自动提取｜
--                      explicit=用户显式写入（memory_store_tool）。
--                      NOT NULL + DEFAULT 使查询/观测无需 NULL 分支。
--   source_message_id  INTEGER NULL
--                      产出该记忆的用户消息（chat_messages.id，role=user）。
--                      仅逻辑外键，**不建 DB FK**：chat_messages 随会话
--                      ON DELETE CASCADE（002），而长期记忆必须跨会话存活，
--                      FK CASCADE 会级联误删记忆；SET NULL FK 收益有限
--                      （本列当前无 JOIN 查询路径），从简。
--                      并发安全：由 save_turn 完成时确定的 user_message_id
--                      作为不可变参数透传给后台 store，禁止事后查 latest。
--
-- confidence_score（已存在列，本迁移不动）：由代码层激活写入——
--   explicit=0.98 默认、inferred=extractor 返回值 clamp[0,1]（非法回退 0.7），
--   历史记录保留原值（default 1.0）不回填。
--
-- 目标库：agent_memory
-- 幂等：可重复执行（IF NOT EXISTS）
-- 红线：不修改既有列的任何值；不重建表；不阻塞在线写入
--       （PG 11+ ADD COLUMN 常量 DEFAULT 为元数据级变更）。
-- =====================================================

ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS origin VARCHAR(16) NOT NULL DEFAULT 'legacy';

ALTER TABLE public.memory_records
    ADD COLUMN IF NOT EXISTS source_message_id INTEGER;

COMMENT ON COLUMN public.memory_records.origin IS
    '记忆来源通道：legacy(历史迁移)/inferred(后台自动提取)/explicit(用户显式写入)';
COMMENT ON COLUMN public.memory_records.source_message_id IS
    '产出该记忆的用户消息 id（chat_messages.id, role=user）；逻辑外键无 DB FK（会话级联删除不得波及长期记忆）；legacy 记录为 NULL';
