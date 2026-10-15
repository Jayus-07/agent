-- 097_llm_model_health_probe_source.sql — 区分周期状态扫描与管理员真实探测
--
-- 目标库：agent_memory
-- OCR 的周期扫描过去只检查了注册表和密钥是否存在，不能证明上游授权有效。
-- 保留现有 llm_model_health 表，仅记录探测来源，避免将旧的凭据存在状态误当作
-- 上游调用通过。旧记录默认为 scheduled；OCR 角色页会要求管理员主动验证。
-- 可重复执行。

ALTER TABLE llm_model_health
    ADD COLUMN IF NOT EXISTS probe_source TEXT NOT NULL DEFAULT 'scheduled';

COMMENT ON COLUMN llm_model_health.probe_source IS
    '健康结论来源：scheduled 为后台周期探测，manual 为管理员主动探测';
