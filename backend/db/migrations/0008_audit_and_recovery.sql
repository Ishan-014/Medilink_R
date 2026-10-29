CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    workflow_id UUID REFERENCES workflow_runs(id),
    actor_user_id TEXT NOT NULL,
    request_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success','denied','failure')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX audit_logs_workflow_time_idx ON audit_logs(workflow_id,created_at,id);
CREATE INDEX audit_logs_created_idx ON audit_logs(created_at,id);
CREATE INDEX audit_logs_actor_time_idx ON audit_logs(actor_user_id,created_at,id);
CREATE INDEX audit_logs_resource_idx ON audit_logs(resource_type,resource_id,created_at,id);

CREATE TABLE system_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES workflow_runs(id),
    request_id TEXT,
    actor_user_id TEXT NOT NULL,
    component TEXT NOT NULL CHECK (component IN ('api','database','mcp','graph','ai','recovery')),
    code TEXT NOT NULL CHECK (code ~ '^[A-Z][A-Z0-9_]{0,79}$'),
    severity TEXT NOT NULL CHECK (severity IN ('info','warning','error')),
    http_status INTEGER CHECK (http_status BETWEEN 100 AND 599),
    duration_ms INTEGER CHECK (duration_ms>=0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX system_events_workflow_time_idx ON system_events(workflow_id,created_at,id);
CREATE INDEX system_events_created_idx ON system_events(created_at,id);
CREATE INDEX system_events_code_time_idx ON system_events(code,created_at,id);

CREATE TABLE external_reconciliations (
    id UUID PRIMARY KEY,
    workflow_id UUID NOT NULL REFERENCES workflow_runs(id),
    tool_call_id UUID NOT NULL REFERENCES tool_calls(id),
    actor_user_id TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 1000),
    previous_status TEXT NOT NULL CHECK (previous_status IN ('pending','uncertain')),
    original_error_code TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','succeeded','not_applied','unresolved','failed')),
    external_reference TEXT,
    error_code TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    CHECK ((status='pending' AND finished_at IS NULL) OR (status<>'pending' AND finished_at IS NOT NULL)),
    CHECK ((status IN ('succeeded','not_applied') AND external_reference IS NOT NULL)
        OR (status NOT IN ('succeeded','not_applied') AND external_reference IS NULL))
);
CREATE UNIQUE INDEX external_reconciliations_pending_idx ON external_reconciliations(tool_call_id) WHERE status='pending';
CREATE INDEX external_reconciliations_workflow_idx ON external_reconciliations(workflow_id,started_at,id);

CREATE FUNCTION prevent_trace_changes() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Trace records are append-only'; END $$;
CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION prevent_trace_changes();
CREATE TRIGGER system_events_append_only BEFORE UPDATE OR DELETE ON system_events FOR EACH ROW EXECUTE FUNCTION prevent_trace_changes();

-- Audit rows are committed or rolled back with their originating mutation.
-- Explicitly select metadata: never store requests, intake, credentials, results,
-- clinical facts, document names/text, arbitrary proposed actions or SQL errors.
CREATE FUNCTION backend_audit_metadata(row_data JSONB,old_data JSONB DEFAULT '{}'::jsonb)
RETURNS JSONB LANGUAGE SQL IMMUTABLE AS $$ SELECT jsonb_strip_nulls(jsonb_build_object(
        'status',row_data->>'status','previous_status',old_data->>'status',
        'phase',row_data->>'phase','previous_phase',old_data->>'phase',
        'version',row_data->'version','risk',row_data->>'risk','position',row_data->'position',
        'role',row_data->>'role','previous_role',old_data->>'role','is_active',row_data->'is_active',
        'server',row_data->>'server','tool',row_data->>'tool',
        'action',CASE WHEN row_data->>'action' IN ('billing.get_bill','billing.get_procedure_charge','billing.validate_claim_amount','ehr.create_patient','ehr.get_patient','ehr.get_patient_allergies','ehr.get_patient_appointment_info','ehr.get_patient_conditions','ehr.get_patient_lab_summary','ehr.get_patient_medications','ehr.get_patient_summary','ehr.get_record','ehr.search_patients','ehr.update_patient_allergies','ehr.update_patient_conditions','ehr.update_patient_medications','ehr.update_record','graph.claim_history','graph.find_dependencies','graph.query_relationships','insurance.get_claim_status','insurance.get_policy','insurance.submit_claim','insurance.validate_coverage','lab.get_results','lab.get_test_status','lab.list_tests','scheduling.book_slot','scheduling.cancel_slot','scheduling.find_slots') THEN row_data->>'action' END,
        'operation',CASE WHEN row_data->>'operation' IN ('billing.get_bill','billing.get_procedure_charge','billing.validate_claim_amount','ehr.create_patient','ehr.get_patient','ehr.get_patient_allergies','ehr.get_patient_appointment_info','ehr.get_patient_conditions','ehr.get_patient_lab_summary','ehr.get_patient_medications','ehr.get_patient_summary','ehr.get_record','ehr.search_patients','ehr.update_patient_allergies','ehr.update_patient_conditions','ehr.update_patient_medications','ehr.update_record','graph.claim_history','graph.find_dependencies','graph.query_relationships','insurance.get_claim_status','insurance.get_policy','insurance.submit_claim','insurance.validate_coverage','lab.get_results','lab.get_test_status','lab.list_tests','scheduling.book_slot','scheduling.cancel_slot','scheduling.find_slots','query_relationships','find_dependencies','claim_history') THEN row_data->>'operation' END,
        'graph_revision',row_data->>'graph_revision','item_count',row_data->'item_count',
        'unresolved_dependencies',row_data->'unresolved_dependencies',
        'error_code',row_data->>'error_code','result_reference',row_data->>'result_reference',
        'approval_id',row_data->>'approval_id','action_reservation_id',row_data->>'action_reservation_id',
        'tool_call_id',row_data->>'tool_call_id','document_id',row_data->>'document_id',
        'extraction_status',row_data->>'extraction_status','extraction_version',row_data->'extraction_version',
        'event',row_data->>'event','external_reference',row_data->>'external_reference')) $$;

CREATE FUNCTION record_backend_change() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    row_data JSONB; old_data JSONB; wf UUID; resource TEXT; meta JSONB;
    actor TEXT; rid TEXT;
BEGIN
    IF TG_OP='DELETE' THEN row_data=to_jsonb(OLD); ELSE row_data=to_jsonb(NEW); END IF;
    IF TG_OP='UPDATE' THEN
        old_data=to_jsonb(OLD);
        IF row_data=old_data THEN RETURN NULL; END IF;
    ELSE old_data='{}'::jsonb; END IF;
    resource=coalesce(row_data->>'id',row_data->>'user_id',row_data->>'document_id',row_data->>'workflow_id');
    wf=CASE WHEN TG_TABLE_NAME='workflow_runs' THEN (row_data->>'id')::uuid
        WHEN TG_TABLE_NAME='patient_profiles' THEN (row_data->>'onboarding_workflow_id')::uuid
        WHEN TG_TABLE_NAME='approval_events' THEN (SELECT workflow_id FROM approvals WHERE id=(row_data->>'approval_id')::uuid)
        ELSE (row_data->>'workflow_id')::uuid END;
    actor=CASE WHEN TG_TABLE_NAME='approval_events' THEN row_data->>'actor_user_id'
        ELSE coalesce(nullif(current_setting('medilink.actor',true),''),'system:backend') END;
    rid=nullif(current_setting('medilink.request_id',true),'');
    meta=backend_audit_metadata(row_data,old_data);
    INSERT INTO audit_logs (workflow_id,actor_user_id,request_id,action,resource_type,resource_id,outcome,metadata)
        VALUES (wf,actor,rid,lower(TG_TABLE_NAME || '.' || TG_OP),TG_TABLE_NAME,resource,'success',meta);
    IF TG_TABLE_NAME IN ('tool_calls','workflow_graph_queries') AND row_data->>'status' IN ('failed','uncertain')
        AND old_data->>'status' IS DISTINCT FROM row_data->>'status' THEN
        INSERT INTO system_events(workflow_id,request_id,actor_user_id,component,code,severity)
            VALUES (wf,rid,actor,CASE WHEN TG_TABLE_NAME='tool_calls' THEN 'mcp' ELSE 'graph' END,
                coalesce(row_data->>'error_code','EXTERNAL_OPERATION_FAILED'),'error');
    END IF;
    RETURN NULL;
END $$;

DO $$
DECLARE name TEXT;
BEGIN
    FOREACH name IN ARRAY ARRAY['workflow_runs','workflow_steps','workflow_documents','documents',
        'workflow_cases','workflow_case_evidence','patient_profiles','claims','appointments',
        'tool_calls','workflow_graph_queries','approvals','approval_events','workflow_action_keys',
        'external_reconciliations','users'] LOOP
        EXECUTE format('CREATE TRIGGER backend_change_audit AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION record_backend_change()',name);
    END LOOP;
END $$;

-- Earlier parts have no mutation audit history. Preserve their current metadata
-- as clearly labelled upgrade snapshots; never invent historical transitions.
WITH source AS (
    SELECT 'workflow_runs' AS resource_type,to_jsonb(row) AS data FROM workflow_runs row
    UNION ALL
    SELECT 'workflow_steps' AS resource_type,to_jsonb(row) AS data FROM workflow_steps row
    UNION ALL
    SELECT 'workflow_documents' AS resource_type,to_jsonb(row) AS data FROM workflow_documents row
    UNION ALL
    SELECT 'documents' AS resource_type,to_jsonb(row) AS data FROM documents row
    UNION ALL
    SELECT 'workflow_cases' AS resource_type,to_jsonb(row) AS data FROM workflow_cases row
    UNION ALL
    SELECT 'workflow_case_evidence' AS resource_type,to_jsonb(row) AS data FROM workflow_case_evidence row
    UNION ALL
    SELECT 'patient_profiles' AS resource_type,to_jsonb(row) AS data FROM patient_profiles row
    UNION ALL
    SELECT 'claims' AS resource_type,to_jsonb(row) AS data FROM claims row
    UNION ALL
    SELECT 'appointments' AS resource_type,to_jsonb(row) AS data FROM appointments row
    UNION ALL
    SELECT 'tool_calls' AS resource_type,to_jsonb(row) AS data FROM tool_calls row
    UNION ALL
    SELECT 'workflow_graph_queries' AS resource_type,to_jsonb(row) AS data FROM workflow_graph_queries row
    UNION ALL
    SELECT 'approvals' AS resource_type,to_jsonb(row) AS data FROM approvals row
    UNION ALL
    SELECT 'approval_events' AS resource_type,to_jsonb(row) AS data FROM approval_events row
    UNION ALL
    SELECT 'workflow_action_keys' AS resource_type,to_jsonb(row) AS data FROM workflow_action_keys row
    UNION ALL
    SELECT 'external_reconciliations' AS resource_type,to_jsonb(row) AS data FROM external_reconciliations row
    UNION ALL
    SELECT 'users' AS resource_type,to_jsonb(row) AS data FROM users row
)
INSERT INTO audit_logs(workflow_id,actor_user_id,action,resource_type,resource_id,outcome,metadata)
SELECT CASE WHEN resource_type='workflow_runs' THEN (data->>'id')::uuid
    WHEN resource_type='patient_profiles' THEN (data->>'onboarding_workflow_id')::uuid
    WHEN resource_type='approval_events' THEN (SELECT workflow_id FROM approvals WHERE id=(data->>'approval_id')::uuid)
    ELSE (data->>'workflow_id')::uuid END,
    'system:migration',resource_type || '.snapshot',resource_type,
    coalesce(data->>'id',data->>'user_id',data->>'document_id',data->>'workflow_id'),'success',
    backend_audit_metadata(data) || '{"snapshot":true}'::jsonb
FROM source;
