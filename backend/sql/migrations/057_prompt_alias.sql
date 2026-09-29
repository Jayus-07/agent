-- 057_prompt_alias.sql — Prompt 版本语义 + 命名指针（M4 / 台账 D4）
-- 目标：整数版本无语义（答不了 major/minor/patch）、单 active_version 指针
--   无 staging 环境。本迁移补两件事：
--   1. prompt_versions.change_kind：major(行为改变)/minor(能力增强)/patch(文字修复)，
--      NULL = 存量版本（语义未标注，向后兼容）；
--   2. prompt_aliases 命名指针表：production 与 prompts.active_version 保持同步
--      （切 production = 发布语义）；staging 独立指向（预发验收），不参与
--      运行时读路径（读路径仍走 active_version，staging 消费属 Phase 2 灰度）。
-- 数据迁移：production 初值 = 各 prompt 当前 active_version（幂等）。
-- 回滚：DROP TABLE prompt_aliases; ALTER TABLE prompt_versions DROP COLUMN change_kind;

ALTER TABLE prompt_versions ADD COLUMN IF NOT EXISTS change_kind VARCHAR(8);
ALTER TABLE prompt_versions DROP CONSTRAINT IF EXISTS ck_prompt_version_change_kind;
ALTER TABLE prompt_versions ADD CONSTRAINT ck_prompt_version_change_kind
    CHECK (change_kind IS NULL OR change_kind IN ('major','minor','patch'));

CREATE TABLE IF NOT EXISTS prompt_aliases (
    id          BIGSERIAL PRIMARY KEY,
    prompt_id   INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    alias       VARCHAR(32) NOT NULL,
    version     INTEGER NOT NULL,
    updated_by  VARCHAR(128) NOT NULL DEFAULT 'system',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_prompt_alias UNIQUE (prompt_id, alias),
    CONSTRAINT ck_prompt_alias CHECK (alias IN ('production','staging'))
);
CREATE INDEX IF NOT EXISTS ix_prompt_aliases_prompt_id ON prompt_aliases(prompt_id);

-- production 指针初始化（active_version 为空的 prompt 不建指针）
INSERT INTO prompt_aliases (prompt_id, alias, version, updated_by)
SELECT id, 'production', active_version, 'migration-057'
FROM prompts
WHERE active_version IS NOT NULL
ON CONFLICT (prompt_id, alias) DO NOTHING;
