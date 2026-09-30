-- 063_release_records.sql — 发布记录表（M8 / 台账 D8）
-- 目标：release.sh 12 门 Release Gate 此前唯一留痕是控制台输出 + 镜像内
--   GIT_COMMIT，无法回答「现在线上是什么版本、上次发布何时、门禁过没过」。
--   本表由 verify_release_gate.py 跑完 12 门后直连落库（旁路治理数据，
--   不进请求执行路径；写入失败使发布 exit 1——无记录的发布视为违规）。
-- gates：12 门布尔结果 JSON（{Gate0_BuildIdentity: true, ...}）；
-- gate_details：各门诊断串（{g0: "commit=...", ...}）。
-- 回滚语义：上一条 result='PASS' 的 git_sha 即「回滚到哪个 commit 契约
--   兼容」的判据（/releases 页直接展示）。
-- 幂等：IF NOT EXISTS。回滚：DROP TABLE。

CREATE TABLE IF NOT EXISTS ai.release_records (
    id           BIGSERIAL PRIMARY KEY,
    git_sha      TEXT NOT NULL,
    build_time   TEXT NOT NULL DEFAULT '',
    gates        JSONB NOT NULL,
    gate_details JSONB NOT NULL DEFAULT '{}',
    result       TEXT NOT NULL CHECK (result IN ('PASS', 'FAIL')),
    operator     TEXT NOT NULL DEFAULT '',
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_release_records_created
    ON ai.release_records(created_at DESC);
