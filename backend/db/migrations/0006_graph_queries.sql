CREATE TABLE workflow_graph_queries (
    id UUID PRIMARY KEY,
    workflow_id UUID NOT NULL REFERENCES workflow_runs(id),
    workflow_step_id UUID NOT NULL,
    patient_profile_id UUID NOT NULL REFERENCES patient_profiles(id),
    actor_user_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('query_relationships','find_dependencies','claim_history')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','succeeded','failed')),
    request_fingerprint TEXT NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER CHECK (duration_ms >= 0),
    error_code TEXT,
    result_reference TEXT,
    graph_revision TEXT,
    item_count INTEGER CHECK (item_count BETWEEN 0 AND 200),
    unresolved_dependencies INTEGER CHECK (unresolved_dependencies >= 0),
    FOREIGN KEY (workflow_step_id,workflow_id) REFERENCES workflow_steps(id,workflow_id),
    CHECK ((status = 'pending') = (finished_at IS NULL)),
    CHECK ((status = 'failed') = (error_code IS NOT NULL)),
    CHECK ((status = 'succeeded' AND result_reference IS NOT NULL AND graph_revision IS NOT NULL
        AND item_count IS NOT NULL AND unresolved_dependencies IS NOT NULL)
        OR (status <> 'succeeded' AND result_reference IS NULL AND graph_revision IS NULL
        AND item_count IS NULL AND unresolved_dependencies IS NULL))
);
CREATE INDEX workflow_graph_queries_workflow_idx ON workflow_graph_queries(workflow_id,started_at,id);
CREATE UNIQUE INDEX workflow_graph_queries_pending_step_idx ON workflow_graph_queries(workflow_step_id) WHERE status = 'pending';
