-- 120_tool_semantic_contract.sql — AI 语义契约（决策 #19）
-- 目标：动态工具（ai.mcp_tools）的 AI 语义契约「草稿 + 已并入」两阶段存储。
-- 语义（决策 #19 边界）：
--   - semantic_draft：AI 生成 / 管理员编辑的语义契约候选（summary /
--     scenarios / exclusions / capability_hint + status/generated_at/
--     generated_by），仅是候选，不影响任何运行时行为；
--   - semantic_approved：管理员「审核通过并入」后的正式语义契约快照
--     （并入 description 的原文 + 场景清单），并入动作同时更新
--     ai.mcp_tools.description 并在审核留痕中可见；
--   - 为什么用独立列而非塞进 governance JSONB：governance 是审核时整体
--     落库的治理配置（approve 走 CAS 绑定 content_hash），草稿高频覆写
--     若混入 governance 会绕过/污染审核语义——独立列互不干扰。
-- 回滚（按逆序 DROP）：
--   ALTER TABLE ai.mcp_tools DROP COLUMN IF EXISTS semantic_approved;
--   ALTER TABLE ai.mcp_tools DROP COLUMN IF EXISTS semantic_draft;

ALTER TABLE ai.mcp_tools ADD COLUMN IF NOT EXISTS semantic_draft JSONB;
ALTER TABLE ai.mcp_tools ADD COLUMN IF NOT EXISTS semantic_approved JSONB;
