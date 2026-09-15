-- 008_local_auth.sql — py 自建用户体系（APISIX 迁移后替代 Java auth-service）
-- 目标库：agent_memory（与 chat/memory 同库，schema 隔离）
-- 设计：
--   auth.users           用户主表（pbkdf2_sha256 口令哈希，不存明文）
--   auth.refresh_tokens  刷新令牌表（只存 sha256 哈希；轮换/吊销语义）
--   黑名单不落库：logout 时写 Redis（auth:blacklist:<access_token>，TTL=剩余有效期），
--   由 APISIX gateway-auth 插件只读校验（签发方=py，写入方=py，读取方=网关）

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
    id            BIGSERIAL PRIMARY KEY,
    username      VARCHAR(20)  NOT NULL UNIQUE,
    password_hash TEXT         NOT NULL,              -- pbkdf2_sha256$iter$salt$hash
    real_name     VARCHAR(50)  NOT NULL DEFAULT '',
    dept          VARCHAR(50)  NOT NULL DEFAULT '',
    status        SMALLINT     NOT NULL DEFAULT 1,    -- 1=启用 0=禁用
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth.refresh_tokens (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    token_hash  TEXT        NOT NULL,                  -- sha256(token) hex
    device_id   VARCHAR(64) NOT NULL DEFAULT '',
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked     BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash   ON auth.refresh_tokens (token_hash);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user   ON auth.refresh_tokens (user_id, revoked);

-- 过期/已吊销令牌的定时清理由应用侧维护即可（量小），暂不建数据库级任务
