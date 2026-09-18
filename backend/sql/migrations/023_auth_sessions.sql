-- 023_auth_sessions.sql — 会话实体改造：一次设备登录 = auth.sessions 一行（refresh token family）
-- 背景：/security 会话列表原以 access token(jti) 为会话单位（Redis scan），
-- 同一浏览器登录/刷新/多标签各产生一条"会话"。本迁移引入持久会话实体：
--   auth.sessions        会话主表（user_id + device_id 维度，含 UA/IP/活跃时间/吊销状态）
--   auth.refresh_tokens.session_id   轮换链归组（旧行吊销、新行继承同一 session_id）
--   auth.refresh_tokens.revoked_at   重放检测/宽限期需要精确吊销时间（原只有 revoked BOOLEAN）
-- Redis 闸门键 auth:session:{userId}:{jti} 格式与 TTL 不变；管理面列表改查 DB。
-- 回滚策略：应用回退到旧逻辑即可兼容（新增列均 nullable，sessions 表闲置无害）；
-- 如需物理回滚见文末 ROLLBACK 段（破坏性，需先停用新代码）。

CREATE TABLE IF NOT EXISTS auth.sessions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            BIGINT       NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    device_id          VARCHAR(64)  NOT NULL DEFAULT '',
    user_agent         VARCHAR(256) NOT NULL DEFAULT '',
    ip                 VARCHAR(64)  NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_active_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    refresh_expires_at TIMESTAMPTZ  NOT NULL,
    revoked_at         TIMESTAMPTZ,
    revoke_reason      VARCHAR(64)  NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_active
    ON auth.sessions (user_id, revoked_at);

ALTER TABLE auth.refresh_tokens
    ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES auth.sessions(id);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_session
    ON auth.refresh_tokens (session_id);

-- 精确吊销时间：重放检测需区分"60s 宽限期内的并发竞态"与"超期重放（疑似泄露）"。
-- 存量已吊销行回填 created_at（必然远超 60s 宽限 → 走重放分支，符合预期）。
ALTER TABLE auth.refresh_tokens
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

UPDATE auth.refresh_tokens
SET revoked_at = created_at
WHERE revoked = TRUE AND revoked_at IS NULL;

-- ROLLBACK（破坏性，执行前必须先回退应用到旧逻辑）：
--   ALTER TABLE auth.refresh_tokens DROP COLUMN IF EXISTS revoked_at;
--   ALTER TABLE auth.refresh_tokens DROP COLUMN IF EXISTS session_id;
--   DROP TABLE IF EXISTS auth.sessions;
