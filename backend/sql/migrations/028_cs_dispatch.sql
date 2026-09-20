-- 028_cs_dispatch.sql
-- 客服自动派单、租户隔离与可靠事件的数据契约。
-- 本迁移只做加法：所有列/索引均幂等创建，不依赖 Redis 或应用代码。

CREATE SCHEMA IF NOT EXISTS customer_service;

-- 空库直接执行本迁移时，先建立本任务需要的最小完整表结构。
-- 存量库则由后面的 ADD COLUMN IF NOT EXISTS 兼容升级。
CREATE TABLE IF NOT EXISTS customer_service.cs_agents (
    id                  BIGSERIAL PRIMARY KEY,
    agent_id            VARCHAR(64) NOT NULL UNIQUE,
    tenant_id           VARCHAR(64) NOT NULL DEFAULT 'default',
    display_name        VARCHAR(128) NOT NULL,
    email               VARCHAR(128),
    role                VARCHAR(20) NOT NULL DEFAULT 'agent',
    available           BOOLEAN NOT NULL DEFAULT TRUE,
    auth_user_id        VARCHAR(64),
    enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    accepting           BOOLEAN NOT NULL DEFAULT TRUE,
    last_assigned_at    TIMESTAMPTZ,
    version             INTEGER NOT NULL DEFAULT 0,
    max_conversations   BIGINT NOT NULL DEFAULT 10,
    custom_fields       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS customer_service.conversations (
    id                  BIGSERIAL PRIMARY KEY,
    conversation_id     VARCHAR(64) NOT NULL UNIQUE,
    user_id             VARCHAR(64) NOT NULL,
    tenant_id           VARCHAR(64) NOT NULL DEFAULT 'default',
    conversation_status VARCHAR(20) NOT NULL DEFAULT 'open',
    handling_mode       VARCHAR(20) NOT NULL DEFAULT 'ai',
    channel             VARCHAR(20) NOT NULL DEFAULT 'web',
    assigned_agent_id   VARCHAR(64),
    ai_enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    summary             TEXT,
    context_summary     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ,
    CONSTRAINT fk_cs_handoff_assigned_agent
        FOREIGN KEY (assigned_agent_id)
        REFERENCES customer_service.cs_agents(agent_id)
        ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS customer_service.handoffs (
    id                  BIGSERIAL PRIMARY KEY,
    handoff_id          VARCHAR(64) NOT NULL UNIQUE,
    conversation_id     VARCHAR(64) NOT NULL,
    user_id             VARCHAR(64) NOT NULL,
    tenant_id           VARCHAR(64) NOT NULL DEFAULT 'default',
    handoff_state       VARCHAR(20) NOT NULL DEFAULT 'initiated',
    trigger_type        VARCHAR(30),
    trigger_reason      TEXT,
    ticket_id           VARCHAR(64),
    priority            INTEGER NOT NULL DEFAULT 50,
    assigned_agent_id   VARCHAR(64),
    assignment_version  INTEGER NOT NULL DEFAULT 0,
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    idempotency_key     VARCHAR(128),
    total_deadline_at   TIMESTAMPTZ,
    offered_at          TIMESTAMPTZ,
    offer_expires_at    TIMESTAMPTZ,
    closed_reason       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS customer_service.assignments (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           VARCHAR(64) NOT NULL DEFAULT 'default',
    handoff_id          VARCHAR(64),
    conversation_id     VARCHAR(64) NOT NULL,
    agent_id            VARCHAR(64),
    state               VARCHAR(20) NOT NULL DEFAULT 'offered',
    attempt_no          INTEGER NOT NULL DEFAULT 1,
    offer_version       INTEGER NOT NULL DEFAULT 0,
    offered_at          TIMESTAMPTZ,
    offer_expires_at    TIMESTAMPTZ,
    accepted_at         TIMESTAMPTZ,
    declined_at         TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ,
    assigned_by         VARCHAR(64),
    assigned_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    unassigned_at       TIMESTAMPTZ,
    CONSTRAINT ck_cs_assignment_active_handoff_required
        CHECK (state NOT IN ('offered', 'accepted') OR handoff_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS customer_service.events (
    id                  BIGSERIAL PRIMARY KEY,
    conversation_id     VARCHAR(64) NOT NULL,
    event_id            VARCHAR(64) NOT NULL UNIQUE,
    tenant_id           VARCHAR(64) NOT NULL DEFAULT 'default',
    handoff_id          VARCHAR(64),
    target_agent_id     VARCHAR(64),
    actor_user_id       VARCHAR(64),
    event_seq           BIGINT,
    type                VARCHAR(64) NOT NULL,
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    outbox_status       VARCHAR(20) NOT NULL DEFAULT 'pending',
    published_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 1. conversations：兼容 006 的 assigned_agent/status 命名，同时不覆盖旧字段。
DO $$
BEGIN
    IF to_regclass('customer_service.conversations') IS NOT NULL THEN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'customer_service'
              AND table_name = 'conversations'
              AND column_name = 'assigned_agent'
        ) AND NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'customer_service'
              AND table_name = 'conversations'
              AND column_name = 'assigned_agent_id'
        ) THEN
            ALTER TABLE customer_service.conversations
                RENAME COLUMN assigned_agent TO assigned_agent_id;
        END IF;

        ALTER TABLE customer_service.conversations
            ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64)
                NOT NULL DEFAULT 'default';
        UPDATE customer_service.conversations
        SET tenant_id = 'default'
        WHERE tenant_id IS NULL;
        ALTER TABLE customer_service.conversations
            ALTER COLUMN tenant_id SET DEFAULT 'default',
            ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
END $$;

-- 2. cs_agents：租户、身份绑定、启用/接单状态与乐观版本。
DO $$
BEGIN
    IF to_regclass('customer_service.cs_agents') IS NOT NULL THEN
        ALTER TABLE customer_service.cs_agents
            ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64)
                NOT NULL DEFAULT 'default',
            ADD COLUMN IF NOT EXISTS auth_user_id VARCHAR(64),
            ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS accepting BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS last_assigned_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0;
        UPDATE customer_service.cs_agents
        SET tenant_id = 'default'
        WHERE tenant_id IS NULL;
        ALTER TABLE customer_service.cs_agents
            ALTER COLUMN tenant_id SET DEFAULT 'default',
            ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
END $$;

-- 3. handoffs：派单生命周期、版本与超时字段。
DO $$
BEGIN
    IF to_regclass('customer_service.handoffs') IS NOT NULL THEN
        ALTER TABLE customer_service.handoffs
            ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64)
                NOT NULL DEFAULT 'default',
            ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 50,
            ADD COLUMN IF NOT EXISTS assigned_agent_id VARCHAR(64),
            ADD COLUMN IF NOT EXISTS assignment_version INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128),
            ADD COLUMN IF NOT EXISTS total_deadline_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS offered_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS offer_expires_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS closed_reason TEXT;
        UPDATE customer_service.handoffs
        SET tenant_id = 'default'
        WHERE tenant_id IS NULL;
        ALTER TABLE customer_service.handoffs
            ALTER COLUMN tenant_id SET DEFAULT 'default',
            ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
END $$;

-- 4. assignments：保留旧认领字段，新增 offer 状态与并发版本。
DO $$
BEGIN
    IF to_regclass('customer_service.assignments') IS NOT NULL THEN
        ALTER TABLE customer_service.assignments
            ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64)
                NOT NULL DEFAULT 'default',
            ADD COLUMN IF NOT EXISTS handoff_id VARCHAR(64),
            ADD COLUMN IF NOT EXISTS state VARCHAR(20)
                NOT NULL DEFAULT 'offered',
            ADD COLUMN IF NOT EXISTS attempt_no INTEGER NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS offer_version INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS offered_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS offer_expires_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS declined_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
        UPDATE customer_service.assignments
        SET tenant_id = 'default'
        WHERE tenant_id IS NULL;
        ALTER TABLE customer_service.assignments
            ALTER COLUMN tenant_id SET DEFAULT 'default',
            ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
END $$;

-- legacy assignment 没有 handoff_id，不能被视为当前活动派单。
-- 统一降级为 released；NULL handoff 由后续复合 FK/唯一性检查排除。
UPDATE customer_service.assignments
SET state = 'released'
WHERE handoff_id IS NULL
  AND state IN ('offered', 'accepted');

-- 5. events：持久事件与 outbox 状态；event_seq 不设应用硬编码默认值。
DO $$
BEGIN
    IF to_regclass('customer_service.events') IS NOT NULL THEN
        ALTER TABLE customer_service.events
            ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64)
                NOT NULL DEFAULT 'default',
            ADD COLUMN IF NOT EXISTS handoff_id VARCHAR(64),
            ADD COLUMN IF NOT EXISTS target_agent_id VARCHAR(64),
            ADD COLUMN IF NOT EXISTS actor_user_id VARCHAR(64),
            ADD COLUMN IF NOT EXISTS event_seq BIGINT,
            ADD COLUMN IF NOT EXISTS outbox_status VARCHAR(20)
                NOT NULL DEFAULT 'pending',
            ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
        UPDATE customer_service.events
        SET tenant_id = 'default'
        WHERE tenant_id IS NULL;
        ALTER TABLE customer_service.events
            ALTER COLUMN tenant_id SET DEFAULT 'default',
            ALTER COLUMN tenant_id SET NOT NULL;
    END IF;
END $$;

-- 复合关联保持 ORM 与数据库一致；存量孤儿引用显式阻断，不静默改写。
-- 复合键的父端唯一索引必须先存在，才能添加复合外键。
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_agent_tenant_agent_id
    ON customer_service.cs_agents (tenant_id, agent_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_conversation_tenant_conversation_id
    ON customer_service.conversations (tenant_id, conversation_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_handoff_tenant_handoff_id
    ON customer_service.handoffs (tenant_id, handoff_id);

-- 0003 及早期 028 的单列 assignment 外键不再表达租户边界。
-- 仅删除约束，不删除表或行；IF EXISTS 保证升级可重复执行。
ALTER TABLE customer_service.assignments
    DROP CONSTRAINT IF EXISTS assignments_conversation_id_fkey,
    DROP CONSTRAINT IF EXISTS assignments_agent_id_fkey,
    DROP CONSTRAINT IF EXISTS fk_cs_assignment_handoff;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer_service.handoffs'::regclass
          AND conname = 'fk_cs_handoff_tenant_agent'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM customer_service.handoffs h
            WHERE h.assigned_agent_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM customer_service.cs_agents a
                  WHERE a.tenant_id = h.tenant_id
                    AND a.agent_id = h.assigned_agent_id
              )
        ) THEN
            RAISE EXCEPTION
                'customer_service.handoffs has orphan tenant/assigned_agent_id values';
        END IF;
        ALTER TABLE customer_service.handoffs
            ADD CONSTRAINT fk_cs_handoff_tenant_agent
            FOREIGN KEY (tenant_id, assigned_agent_id)
            REFERENCES customer_service.cs_agents(tenant_id, agent_id)
            ON DELETE SET NULL (assigned_agent_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer_service.assignments'::regclass
          AND conname = 'fk_cs_assignment_tenant_conversation'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM customer_service.assignments a
            WHERE NOT EXISTS (
                SELECT 1
                FROM customer_service.conversations c
                WHERE c.tenant_id = a.tenant_id
                  AND c.conversation_id = a.conversation_id
            )
        ) THEN
            RAISE EXCEPTION
                'customer_service.assignments has orphan tenant/conversation_id values';
        END IF;
        ALTER TABLE customer_service.assignments
            ADD CONSTRAINT fk_cs_assignment_tenant_conversation
            FOREIGN KEY (tenant_id, conversation_id)
            REFERENCES customer_service.conversations(tenant_id, conversation_id)
            ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer_service.assignments'::regclass
          AND conname = 'fk_cs_assignment_tenant_handoff'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM customer_service.assignments a
            WHERE a.handoff_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM customer_service.handoffs h
                  WHERE h.tenant_id = a.tenant_id
                    AND h.handoff_id = a.handoff_id
              )
        ) THEN
            RAISE EXCEPTION
                'customer_service.assignments has orphan tenant/handoff_id values';
        END IF;
        ALTER TABLE customer_service.assignments
            ADD CONSTRAINT fk_cs_assignment_tenant_handoff
            FOREIGN KEY (tenant_id, handoff_id)
            REFERENCES customer_service.handoffs(tenant_id, handoff_id)
            ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer_service.assignments'::regclass
          AND conname = 'fk_cs_assignment_tenant_agent'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM customer_service.assignments a
            WHERE a.agent_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM customer_service.cs_agents agent
                  WHERE agent.tenant_id = a.tenant_id
                    AND agent.agent_id = a.agent_id
              )
        ) THEN
            RAISE EXCEPTION
                'customer_service.assignments has orphan tenant/agent_id values';
        END IF;
        ALTER TABLE customer_service.assignments
            ADD CONSTRAINT fk_cs_assignment_tenant_agent
            FOREIGN KEY (tenant_id, agent_id)
            REFERENCES customer_service.cs_agents(tenant_id, agent_id)
            ON DELETE SET NULL (agent_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer_service.assignments'::regclass
          AND conname = 'ck_cs_assignment_active_handoff_required'
    ) THEN
        ALTER TABLE customer_service.assignments
            ADD CONSTRAINT ck_cs_assignment_active_handoff_required
            CHECK (state NOT IN ('offered', 'accepted') OR handoff_id IS NOT NULL);
    END IF;
END $$;

-- 在创建活动唯一索引前显式检查冲突，避免静默丢弃存量状态。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM customer_service.handoffs
        WHERE handoff_state <> 'closed'
        GROUP BY tenant_id, conversation_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'customer_service.handoffs has duplicate active tenant/conversation rows';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM customer_service.assignments
        WHERE handoff_id IS NOT NULL
          AND state IN ('offered', 'accepted')
        GROUP BY tenant_id, handoff_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'customer_service.assignments has duplicate active tenant/handoff rows';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM customer_service.cs_agents
        WHERE auth_user_id IS NOT NULL
        GROUP BY tenant_id, auth_user_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'customer_service.cs_agents has duplicate tenant/auth_user rows';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM customer_service.events
        WHERE handoff_id IS NOT NULL AND event_seq IS NOT NULL
        GROUP BY tenant_id, handoff_id, event_seq
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'customer_service.events has duplicate tenant/handoff/event_seq rows';
    END IF;
END $$;

-- handoff 派单队列与同会话活动唯一性。
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_handoff_tenant_conversation_active
    ON customer_service.handoffs (tenant_id, conversation_id)
    WHERE handoff_state <> 'closed';
CREATE INDEX IF NOT EXISTS idx_cs_handoff_dispatch_queue
    ON customer_service.handoffs
        (tenant_id, handoff_state, priority DESC, created_at, id);

-- 坐席租户/身份查询与非空身份唯一性。
CREATE INDEX IF NOT EXISTS idx_cs_agent_tenant_status
    ON customer_service.cs_agents (tenant_id, enabled, accepting);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_agent_tenant_auth_user
    ON customer_service.cs_agents (tenant_id, auth_user_id)
    WHERE auth_user_id IS NOT NULL;

-- assignment 活动 offer 唯一性与按坐席状态查询。
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_assignment_tenant_handoff_active
    ON customer_service.assignments (tenant_id, handoff_id)
    WHERE handoff_id IS NOT NULL
      AND state IN ('offered', 'accepted');
CREATE INDEX IF NOT EXISTS idx_cs_assignment_tenant_agent_state
    ON customer_service.assignments (tenant_id, agent_id, state);

-- 事件序号唯一性允许 handoff_id 为 NULL 的普通消息事件。
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_event_tenant_handoff_seq
    ON customer_service.events (tenant_id, handoff_id, event_seq);
CREATE INDEX IF NOT EXISTS idx_cs_event_outbox_pending
    ON customer_service.events (outbox_status, created_at, id)
    WHERE outbox_status = 'pending';

-- 会话租户/状态查询；兼容 0003 以前仍叫 status 的存量表。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'customer_service'
          AND table_name = 'conversations'
          AND column_name = 'conversation_status'
    ) THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_cs_conv_tenant_status '
                'ON customer_service.conversations '
                '(tenant_id, conversation_status, updated_at)';
    ELSIF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'customer_service'
          AND table_name = 'conversations'
          AND column_name = 'status'
    ) THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_cs_conv_tenant_status '
                'ON customer_service.conversations '
                '(tenant_id, status, updated_at)';
    END IF;
END $$;
