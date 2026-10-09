-- ═══════════════════════════════════════════════════════════════════
-- 087_llm_provider_extra_body.sql — 供应商级附加请求体（关闭思考等）
--
-- 目标库：agent_memory
--
-- 背景（2026-10-09 实测）：
--   供应商凭据层早已支持 extra_body（ProviderCredentials.extra_body，
--   providers/driver_compat.py 原样透传给 ChatOpenAI 的 extra_body），
--   但该值只能来自 env/代码 —— llm_providers 只有 extra_headers 列，
--   llm_provider_credentials 两列都没有。后果：运营无法在管理端关闭
--   思考型模型的推理（火山方舟 thinking / 通义 enable_thinking 等），
--   只能改代码或换模型。
--
--   实测影响（trace_store 431 个 LLM生成 span）：主模型
--   doubao-seed-2.0-mini 的 reasoning/completion = 0.73，
--   即约 73% 的生成 token 花在思考而非答案上；多查询改写因
--   max_tokens=200 被思考吃光而只产出 1 个变体（98% 的调用）。
--   开放该字段后可按供应商关闭思考，无需换模型。
--
-- 口径：
--   - 列加在 **llm_providers**（供应商级默认），与 extra_headers 同级；
--     凭据表不重复加列，避免两处配置的优先级歧义。
--   - 读取链沿用既有 extra_body 合并语义：
--     resolve_credentials(...).extra_body 已在 credentials.py 组装，
--     本列经 registry_store 注入 provider_entry（与 extra_headers 同路）。
--   - 语义为「OpenAI 兼容请求体的附加字段」，非 OpenAI 厂商由各
--     providers/*.py 自行决定是否消费（driver_compat 直接透传）。
--
-- 幂等：ADD COLUMN IF NOT EXISTS；已存在则零副作用。
-- ═══════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE llm_providers
    ADD COLUMN IF NOT EXISTS extra_body jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN llm_providers.extra_body IS
    'OpenAI 兼容请求体的附加字段（如 {"thinking":{"type":"disabled"}} 关闭思考）。'
    '与 extra_headers 同级：供应商默认值，凭据层可覆盖，最终由 driver 透传。';

COMMIT;
