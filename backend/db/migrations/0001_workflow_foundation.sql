-- Foundation only: no automatic alteration of existing users/patients tables.
-- Actor IDs are text because the existing identity store may use integer IDs.
CREATE TABLE workflow_runs (
    id UUID PRIMARY KEY,
    owner_user_id TEXT NOT NULL CHECK (length(btrim(owner_user_id)) > 0),
    workflow_type TEXT NOT NULL CHECK (workflow_type IN
        ('onboarding', 'insurance_claim', 'appointment')),
    request TEXT NOT NULL CHECK (length(btrim(request)) BETWEEN 1 AND 10000),
    document_ids JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(document_ids) = 'array'),
    status TEXT NOT NULL DEFAULT 'CREATED' CHECK (status IN
        ('CREATED', 'PLANNING', 'WAITING_FOR_DATA', 'EXECUTING',
         'WAITING_FOR_APPROVAL', 'COMPLETED', 'FAILED', 'REJECTED', 'CANCELLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE workflow_steps (
    id UUID PRIMARY KEY,
    workflow_id UUID NOT NULL REFERENCES workflow_runs(id),
    position INTEGER NOT NULL CHECK (position >= 0),
    action TEXT NOT NULL CHECK (length(btrim(action)) BETWEEN 1 AND 200),
    status TEXT NOT NULL DEFAULT 'CREATED' CHECK (status IN
        ('CREATED', 'PLANNING', 'WAITING_FOR_DATA', 'EXECUTING',
         'WAITING_FOR_APPROVAL', 'COMPLETED', 'FAILED', 'REJECTED', 'CANCELLED')),
    risk TEXT CHECK (risk IN ('low', 'medium', 'high')),
    result_reference TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workflow_id, position)
);

CREATE INDEX workflow_runs_owner_created_idx ON workflow_runs(owner_user_id, created_at DESC);
CREATE INDEX workflow_runs_status_idx ON workflow_runs(status);
