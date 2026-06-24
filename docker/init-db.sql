-- Agent Platform — PostgreSQL 初始化脚本
-- 创建 demo 数据库的 schema 和示例数据

-- ============================================
-- 部门表
-- ============================================
CREATE TABLE IF NOT EXISTS departments (
    id        SERIAL PRIMARY KEY,
    name      VARCHAR(100) NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES departments(id)
);

-- ============================================
-- 用户表
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(50)  NOT NULL,
    email      VARCHAR(100),
    phone      VARCHAR(20),
    dept_id    INTEGER REFERENCES departments(id),
    role       VARCHAR(20) DEFAULT 'staff',
    created_at DATE DEFAULT CURRENT_DATE
);

-- ============================================
-- 项目表
-- ============================================
CREATE TABLE IF NOT EXISTS projects (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(200) NOT NULL,
    owner_id   INTEGER REFERENCES users(id),
    budget     NUMERIC(12, 2) DEFAULT 0,
    status     VARCHAR(20) DEFAULT 'planning',
    start_date DATE,
    end_date   DATE
);

-- ============================================
-- 项目成员关联表
-- ============================================
CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER REFERENCES projects(id),
    user_id    INTEGER REFERENCES users(id),
    role       VARCHAR(20) DEFAULT 'member',
    PRIMARY KEY (project_id, user_id)
);

-- ============================================
-- 示例数据
-- ============================================

-- 部门
INSERT INTO departments (id, name, parent_id) VALUES
    (1, '总经办', NULL),
    (2, '技术部', 1),
    (3, '产品部', 1),
    (4, '市场部', 1)
ON CONFLICT (id) DO NOTHING;

-- 用户
INSERT INTO users (id, name, email, phone, dept_id, role, created_at) VALUES
    (1, '张伟', 'zhangwei@corp.com', '13800138001', 2, 'admin',    '2024-01-15'),
    (2, '李娜', 'lina@corp.com',    '13800138002', 2, 'manager',  '2024-02-20'),
    (3, '王强', 'wangqiang@corp.com','13800138003', 2, 'staff',    '2024-03-10'),
    (4, '赵敏', 'zhaomin@corp.com', '13800138004', 3, 'manager',  '2024-01-10'),
    (5, '孙鹏', 'sunpeng@corp.com', '13800138005', 1, 'admin',    '2023-06-01')
ON CONFLICT (id) DO NOTHING;

-- 项目
INSERT INTO projects (id, name, owner_id, budget, status, start_date, end_date) VALUES
    (1, 'BI报表系统',  1, 350.00, 'active',    '2025-01-01', '2025-12-31'),
    (2, '数据中台建设', 2, 800.00, 'active',    '2025-03-01', '2026-06-30'),
    (3, '智能客服平台', 1, 500.00, 'planning',  '2025-06-01', NULL),
    (4, '移动办公App',  3, 200.00, 'completed', '2024-06-01', '2025-03-31')
ON CONFLICT (id) DO NOTHING;

-- 项目成员
INSERT INTO project_members (project_id, user_id, role) VALUES
    (1, 1, 'lead'),
    (1, 2, 'member'),
    (1, 3, 'member'),
    (2, 2, 'lead'),
    (2, 1, 'member'),
    (2, 3, 'member'),
    (3, 1, 'lead'),
    (3, 3, 'member'),
    (4, 3, 'lead'),
    (4, 4, 'reviewer'),
    (4, 2, 'member')
ON CONFLICT (project_id, user_id) DO NOTHING;

-- 更新序列
SELECT setval('departments_id_seq', (SELECT COALESCE(MAX(id), 1) FROM departments));
SELECT setval('users_id_seq', (SELECT COALESCE(MAX(id), 1) FROM users));
SELECT setval('projects_id_seq', (SELECT COALESCE(MAX(id), 1) FROM projects));

-- ============================================
-- 记忆系统表（L2 + L3）
-- 来源: memory/migrations/001_init.sql
-- ============================================
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Session Memory (L2)
CREATE TABLE IF NOT EXISTS chat_sessions (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL UNIQUE,
    user_id         VARCHAR(64)  NOT NULL DEFAULT 'default',
    summary         TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role            VARCHAR(16)  NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT         NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages (session_id, created_at);

-- LongTerm Memory (L3)
CREATE TABLE IF NOT EXISTS memory_records (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         VARCHAR(64)  NOT NULL DEFAULT 'default',
    session_id      VARCHAR(128) NOT NULL DEFAULT '',
    memory_type     VARCHAR(32)  NOT NULL CHECK (memory_type IN ('user_fact', 'preference', 'decision', 'knowledge')),
    content         TEXT         NOT NULL,
    embedding       vector(512),
    importance_score FLOAT       NOT NULL DEFAULT 0.5,
    confidence_score FLOAT       NOT NULL DEFAULT 1.0,
    access_count    INT          NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_access_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expire_at       TIMESTAMPTZ,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    superseded_by   UUID         REFERENCES memory_records(id)
);
CREATE INDEX IF NOT EXISTS idx_memory_embedding ON memory_records
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_memory_user_active ON memory_records (user_id, is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_records (memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_importance ON memory_records (importance_score DESC) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_last_access ON memory_records (last_access_at DESC);
