-- 141_api_keys.sql — 服务凭据（API Key）治理表（2026-11-08 安全审计 T9）
--
-- 背景：平台此前只有一把共享 API_KEY（env），无法按调用方拆分、单独吊销
-- 或审计。本表保存调用方级 Key 的 SHA-256 摘要（不存明文）与生命周期字段；
-- 中间件先比对 env 里的 legacy Key，再按 ak_ 前缀查本表。
--
-- 生效延迟：应用侧 15s 进程缓存；吊销/停用最迟 15s 生效。

CREATE TABLE IF NOT EXISTS auth.api_keys (
    id             BIGSERIAL PRIMARY KEY,
    key_id         TEXT NOT NULL UNIQUE,
    key_hash       TEXT NOT NULL UNIQUE,
    caller         TEXT NOT NULL,
    tenant_id      TEXT,
    scopes         TEXT[] NOT NULL DEFAULT ARRAY['service']::TEXT[],
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at     TIMESTAMPTZ,
    last_used_at   TIMESTAMPTZ,
    revoked_at     TIMESTAMPTZ,
    revoked_reason TEXT,
    CONSTRAINT ck_api_keys_hash_len CHECK (length(key_hash) = 64)
);

CREATE INDEX IF NOT EXISTS idx_api_keys_enabled
    ON auth.api_keys(enabled)
    WHERE enabled IS TRUE;
