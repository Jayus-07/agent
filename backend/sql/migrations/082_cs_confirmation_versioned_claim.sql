-- Confirmation API version binding and durable idempotency.
-- Existing rows remain readable at version 1; no confirmation or audit row is deleted.

ALTER TABLE customer_service.confirmations
    ADD COLUMN IF NOT EXISTS proposal_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE customer_service.confirmations
    ADD COLUMN IF NOT EXISTS client_action_id VARCHAR(36);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'confirmations_proposal_version_positive'
          AND conrelid = 'customer_service.confirmations'::regclass
    ) THEN
        ALTER TABLE customer_service.confirmations
            ADD CONSTRAINT confirmations_proposal_version_positive
            CHECK (proposal_version > 0);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_confirmation_client_action
    ON customer_service.confirmations (tenant_id, user_id, client_action_id)
    WHERE tenant_id IS NOT NULL AND client_action_id IS NOT NULL;
