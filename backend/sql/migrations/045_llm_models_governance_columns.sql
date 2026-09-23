-- 045_llm_models_governance_columns.sql — 模型治理收口（STOP B）
-- 目标（Model Governance STOP B）：
--   1. llm_models 增加 max_output_tokens 列 —— 把「模型支持的最大输出」与
--      「上下文窗口 / 业务默认 max_tokens」两个概念分开（此前 8 处构建器
--      直接拿 LLM_CONTEXT_LENGTH 兼任输出上限）。
--   2. 为当前生产活跃模型登记 context_length（此前 16/16 全 NULL，
--      Context Budget 只能吃 env LLM_CONTEXT_LENGTH=8192）。
--   3. 为活跃模型登记 capabilities 初始值（capabilities JSONB 容器已存在
--      但全空零消费；本迁移只登记官方文档可确认的能力，查不到的保持空）。
-- 幂等：IF NOT EXISTS + 条件 UPDATE，重复执行无副作用。
-- 向后兼容：新列可空；runtime 未登记时回退现状行为（NULL=fail-safe）。

ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS max_output_tokens INTEGER;

-- ── context_length 落地 ─────────────────────────────────────────────
-- 来源与查证时间（B7 要求 source/checked_at 可追溯）：
--   doubao-seed-2.0-mini = 262144 (256K)
--     source: 火山引擎方舟模型列表（volcengine.com）+ Coze 模型服务文档
--     （docs.coze.cn，"支持 256K 上下文"）；checked_at: 2026-09-23
--   qwen3.8-flash = 1000000 (1M)
--     source: Alibaba Cloud Model Studio（help.aliyun.com 模型列表）+
--     qwen.ai 官方发布（"1M-token context window"）；checked_at: 2026-09-23
--     ⚠️ 当前实例经第三方中转（custom-api），中转商实际限制可能更小；
--     registry 读取方按 min(env, entry) 取窗口，env 仍是最小约束。
--   kimi-k3 = 1048576 (1M)
--     source: Kimi API Platform（platform.kimi.ai 模型列表）+
--     openrouter.ai（"context window 1,048,576"）；checked_at: 2026-09-23
--   其余模型（qwen3.7-flash / qwen-ocr / embedding / rerank / builtin 停用模型）
--   未获可靠官方来源 → 按规约保持 NULL（fail-safe 用全局更小窗口，不编造）。
UPDATE llm_models SET context_length = 262144
 WHERE name = 'doubao-seed-2.0-mini' AND context_length IS NULL;
UPDATE llm_models SET context_length = 1000000
 WHERE name = 'qwen3.8-flash' AND context_length IS NULL;
UPDATE llm_models SET context_length = 1048576
 WHERE name = 'kimi-k3' AND context_length IS NULL;

-- ── capabilities 初始值（仅登记官方可确认能力，未确认的键不写 = fail-closed）──
-- doubao-seed-2.0-mini：函数调用 / 视觉输入 / 深度思考（方舟官方文档确认）
UPDATE llm_models
   SET capabilities = '{"tools": true, "vision": true, "thinking": true}'::jsonb
 WHERE name = 'doubao-seed-2.0-mini'
   AND NOT (capabilities ? 'tools');
-- qwen3.8-flash：Agent/工具执行能力（官方发布确认）；vision 经中转未确认 → 不写
UPDATE llm_models
   SET capabilities = '{"tools": true}'::jsonb
 WHERE name = 'qwen3.8-flash'
   AND NOT (capabilities ? 'tools');
-- kimi-k3：原生视觉（官方模型页确认）；tools 未确认 → 不写
UPDATE llm_models
   SET capabilities = '{"vision": true}'::jsonb
 WHERE name = 'kimi-k3'
   AND NOT (capabilities ? 'vision');
