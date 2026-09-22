-- 036: llm_models.upstream_model_name —— 登记名/上游模型名拆分（2026-09-22）。
--
-- 背景：llm_models 主键 = name（模型名全局唯一，价格/角色/账目按名引用）。
-- 用户需要「不同厂商下登记同名上游模型」（如中转站与官方各配一个
-- qwen3.7-plus）。方案：登记名继续全局唯一（用户可自定义，如
-- qwen3.7-plus@relay），新增 upstream_model_name 存**发给厂商 API 的
-- 真实模型名**；运行时调用/探测用 upstream，账目与绑定用登记名。
-- '' = 与 name 相同（默认，向后完全兼容）。
-- 幂等：ADD COLUMN IF NOT EXISTS，可安全重放。

ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS upstream_model_name TEXT NOT NULL DEFAULT '';
