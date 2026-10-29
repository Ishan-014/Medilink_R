ALTER TABLE workflow_steps ADD CONSTRAINT workflow_steps_id_workflow_unique UNIQUE (id, workflow_id);

CREATE TABLE tool_calls (
    id UUID PRIMARY KEY,
    workflow_id UUID NOT NULL REFERENCES workflow_runs(id),
    workflow_step_id UUID NOT NULL,
    actor_user_id TEXT NOT NULL,
    server TEXT NOT NULL CHECK (server IN ('ehr','lab','scheduling','insurance','billing')),
    tool TEXT NOT NULL CHECK (tool ~ '^[A-Za-z0-9_]{1,128}$'),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','succeeded','failed','uncertain')),
    request_fingerprint TEXT NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    error_code TEXT,
    result_reference TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER CHECK (duration_ms >= 0),
    FOREIGN KEY (workflow_step_id, workflow_id) REFERENCES workflow_steps(id, workflow_id),
    CHECK ((status = 'pending') = (finished_at IS NULL)),
    CHECK ((status = 'succeeded') = (result_reference IS NOT NULL)),
    CHECK ((status IN ('failed','uncertain')) = (error_code IS NOT NULL))
);

CREATE INDEX tool_calls_workflow_started_idx ON tool_calls(workflow_id, started_at, id);
CREATE UNIQUE INDEX tool_calls_one_pending_per_step_idx ON tool_calls(workflow_step_id) WHERE status = 'pending';
