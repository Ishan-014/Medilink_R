ALTER TABLE workflow_cases DROP CONSTRAINT workflow_cases_phase_check;
ALTER TABLE workflow_cases ADD CONSTRAINT workflow_cases_phase_check CHECK
    (phase IN ('waiting_data','collecting','awaiting_approval','confirmed','failed','cancelled','rejected'));
ALTER TABLE claims DROP CONSTRAINT claims_status_check;
ALTER TABLE claims ADD CONSTRAINT claims_status_check CHECK
    (status IN ('waiting_for_data','preparing','prepared','submitted','failed','cancelled','rejected'));
ALTER TABLE patient_profiles DROP CONSTRAINT patient_profiles_status_check;
ALTER TABLE patient_profiles ADD CONSTRAINT patient_profiles_status_check CHECK
    (status IN ('draft','active','cancelled','failed','rejected'));

CREATE TABLE approvals (
    id UUID PRIMARY KEY,
    workflow_id UUID NOT NULL REFERENCES workflow_runs(id),
    workflow_step_id UUID NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('ehr.create_patient','insurance.submit_claim')),
    risk TEXT NOT NULL CHECK (risk IN ('medium','high')),
    policy_version TEXT NOT NULL,
    scope_fingerprint TEXT NOT NULL CHECK (scope_fingerprint ~ '^[0-9a-f]{64}$'),
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    request_reason TEXT NOT NULL CHECK (length(btrim(request_reason)) BETWEEN 1 AND 1000),
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','consumed','invalidated')),
    decision_mode TEXT CHECK (decision_mode IN ('human','automatic')),
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    decision_reason TEXT,
    consumed_by TEXT,
    consumed_at TIMESTAMPTZ,
    consumed_call_id UUID UNIQUE,
    FOREIGN KEY (workflow_step_id,workflow_id) REFERENCES workflow_steps(id,workflow_id),
    CHECK ((status IN ('approved','rejected','consumed') AND decision_mode IS NOT NULL
        AND decided_by IS NOT NULL AND decided_at IS NOT NULL AND length(btrim(decision_reason)) BETWEEN 1 AND 1000)
        OR status IN ('pending','invalidated')),
    CHECK ((status = 'consumed' AND consumed_by IS NOT NULL AND consumed_at IS NOT NULL AND consumed_call_id IS NOT NULL)
        OR (status <> 'consumed' AND consumed_by IS NULL AND consumed_at IS NULL AND consumed_call_id IS NULL))
);
CREATE UNIQUE INDEX approvals_one_open_step_idx ON approvals(workflow_step_id) WHERE status IN ('pending','approved');
CREATE INDEX approvals_queue_idx ON approvals(status,requested_at);

CREATE TABLE approval_events (
    id UUID PRIMARY KEY,
    approval_id UUID NOT NULL REFERENCES approvals(id),
    actor_user_id TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('requested','approved','rejected','automatic','invalidated','consumed')),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE FUNCTION prevent_approval_event_changes() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Approval events are append-only'; END $$;
CREATE TRIGGER approval_events_append_only BEFORE UPDATE OR DELETE ON approval_events
    FOR EACH ROW EXECUTE FUNCTION prevent_approval_event_changes();

ALTER TABLE tool_calls ADD COLUMN risk TEXT NOT NULL DEFAULT 'low' CHECK (risk IN ('low','medium','high'));
ALTER TABLE tool_calls ADD COLUMN approval_id UUID UNIQUE REFERENCES approvals(id);
ALTER TABLE tool_calls ADD COLUMN action_reservation_id UUID UNIQUE REFERENCES workflow_action_keys(id);
ALTER TABLE approvals ADD FOREIGN KEY (consumed_call_id) REFERENCES tool_calls(id);
ALTER TABLE workflow_graph_queries ADD COLUMN risk TEXT NOT NULL DEFAULT 'low' CHECK (risk='low');

-- Classify historical traces without fabricating retrospective approvals.
UPDATE tool_calls SET risk = CASE
    WHEN server='ehr' AND tool='create_patient' THEN 'medium'
    WHEN server='scheduling' AND tool IN ('book_slot','cancel_slot') THEN 'medium'
    ELSE 'high' END
WHERE (server='ehr' AND tool IN ('create_patient','update_record','update_patient_conditions','update_patient_medications','update_patient_allergies'))
    OR (server='scheduling' AND tool IN ('book_slot','cancel_slot')) OR (server='insurance' AND tool='submit_claim');
UPDATE workflow_steps SET risk='medium' WHERE action IN ('ehr.create_patient','scheduling.book_slot','scheduling.cancel_slot');
