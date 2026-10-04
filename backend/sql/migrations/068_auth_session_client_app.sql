-- 068_auth_session_client_app.sql
-- 会话台账记录登录来源，并为用户端 / 管理端 / 客服端隔离 refresh Cookie。

ALTER TABLE auth.sessions
    ADD COLUMN IF NOT EXISTS client_id VARCHAR(32) NOT NULL DEFAULT 'unknown';

CREATE INDEX IF NOT EXISTS idx_auth_sessions_client_active
    ON auth.sessions (client_id, revoked_at, refresh_expires_at);
