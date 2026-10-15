-- 旅游 V2 AI 请求账本承载持久化冲突确认生命周期。
ALTER TABLE travel_v2.ai_edit_requests
    ALTER COLUMN status TYPE VARCHAR(32);

ALTER TABLE travel_v2.ai_edit_requests
    DROP CONSTRAINT IF EXISTS ai_edit_requests_status_check;

ALTER TABLE travel_v2.ai_edit_requests
    ADD CONSTRAINT chk_travel_v2_ai_edit_request_status
    CHECK (status IN (
        'processing', 'succeeded', 'read_only', 'clarify', 'unsupported',
        'failed', 'unknown', 'pending_resolution', 'cancelled', 'expired'
    ));
