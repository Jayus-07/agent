-- Gate 放行审批绑定到不可变裁决；只记录人工决定，不触发 CI 或部署。
CREATE UNIQUE INDEX IF NOT EXISTS uq_eval_v2_gate_decision_tenant_id
    ON evaluation_v2.eval_gate_decisions (tenant_id, decision_id);

CREATE TABLE IF NOT EXISTS evaluation_v2.eval_gate_approvals (
    approval_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         VARCHAR(128) NOT NULL,
    run_id            UUID NOT NULL,
    gate_decision_id  UUID NOT NULL,
    request_id        VARCHAR(128) NOT NULL,
    request_hash      CHAR(64) NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    decision          VARCHAR(16) NOT NULL CHECK (decision IN ('approved', 'rejected')),
    release_reference VARCHAR(160) NOT NULL,
    rationale         VARCHAR(2000) NOT NULL CHECK (length(btrim(rationale)) >= 8),
    decided_by        VARCHAR(128) NOT NULL,
    decided_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, request_id),
    UNIQUE (tenant_id, approval_id),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES evaluation_v2.eval_runs (tenant_id, run_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, gate_decision_id)
        REFERENCES evaluation_v2.eval_gate_decisions (tenant_id, decision_id) ON DELETE RESTRICT,
    CHECK (release_reference ~ '^[A-Za-z0-9][A-Za-z0-9._:/@+#-]{2,159}$')
);

CREATE INDEX IF NOT EXISTS idx_eval_v2_gate_approvals_run
    ON evaluation_v2.eval_gate_approvals (tenant_id, run_id, decided_at DESC);

GRANT SELECT, INSERT ON evaluation_v2.eval_gate_approvals TO evaluation_v2_app;

DROP TRIGGER IF EXISTS trg_eval_v2_gate_approval_immutable
    ON evaluation_v2.eval_gate_approvals;
CREATE TRIGGER trg_eval_v2_gate_approval_immutable
    BEFORE UPDATE OR DELETE ON evaluation_v2.eval_gate_approvals
    FOR EACH ROW EXECUTE FUNCTION evaluation_v2.reject_immutable_mutation();

ALTER TABLE evaluation_v2.eval_gate_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_v2.eval_gate_approvals FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope ON evaluation_v2.eval_gate_approvals;
CREATE POLICY tenant_scope ON evaluation_v2.eval_gate_approvals
    USING (tenant_id = evaluation_v2.current_tenant_id())
    WITH CHECK (tenant_id = evaluation_v2.current_tenant_id());

COMMENT ON TABLE evaluation_v2.eval_gate_approvals IS
    '人工发布意见，绑定 PASS Gate 决策并追加审计；不代表自动发布或部署';
