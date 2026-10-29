ALTER TABLE workflow_runs ADD COLUMN managed_kind TEXT CHECK (managed_kind IN ('onboarding','insurance_claim'));
ALTER TABLE workflow_runs ADD COLUMN subject_user_id TEXT;

-- Local application registry; the external/legacy EHR patients table is untouched.
CREATE TABLE patient_profiles (
    id UUID PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    onboarding_workflow_id UUID NOT NULL UNIQUE REFERENCES workflow_runs(id),
    full_name TEXT,
    date_of_birth DATE,
    contact_email TEXT NOT NULL,
    phone TEXT,
    external_patient_id TEXT UNIQUE,
    confirmation_call_id UUID UNIQUE REFERENCES tool_calls(id),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','cancelled','failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((status = 'active' AND external_patient_id IS NOT NULL AND confirmation_call_id IS NOT NULL)
        OR (status <> 'active' AND external_patient_id IS NULL AND confirmation_call_id IS NULL))
);
CREATE UNIQUE INDEX patient_profiles_one_active_owner_idx ON patient_profiles(owner_user_id) WHERE status = 'active';

CREATE TABLE claims (
    id UUID PRIMARY KEY,
    workflow_id UUID NOT NULL UNIQUE REFERENCES workflow_runs(id),
    patient_profile_id UUID NOT NULL REFERENCES patient_profiles(id),
    owner_user_id TEXT NOT NULL,
    policy_id TEXT,
    procedure_code TEXT,
    amount NUMERIC(12,2) CHECK (amount > 0),
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    status TEXT NOT NULL CHECK (status IN ('waiting_for_data','preparing','prepared','submitted','failed','cancelled')),
    external_claim_id TEXT UNIQUE,
    submission_call_id UUID UNIQUE REFERENCES tool_calls(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((status = 'submitted' AND external_claim_id IS NOT NULL AND submission_call_id IS NOT NULL)
        OR (status <> 'submitted' AND external_claim_id IS NULL AND submission_call_id IS NULL))
);

CREATE TABLE workflow_cases (
    workflow_id UUID PRIMARY KEY REFERENCES workflow_runs(id),
    kind TEXT NOT NULL CHECK (kind IN ('onboarding','insurance_claim')),
    subject_user_id TEXT NOT NULL,
    entity_id UUID NOT NULL,
    intake JSONB NOT NULL CHECK (jsonb_typeof(intake) = 'object'),
    phase TEXT NOT NULL CHECK (phase IN ('waiting_data','collecting','awaiting_approval','confirmed','failed','cancelled')),
    missing_fields JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(missing_fields) = 'array'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX workflow_cases_one_onboarding_idx ON workflow_cases(subject_user_id)
    WHERE kind = 'onboarding' AND phase IN ('waiting_data','collecting','awaiting_approval');

-- Only selected, typed facts are retained; full clinical tool payloads stay out.
CREATE TABLE workflow_case_evidence (
    workflow_id UUID NOT NULL REFERENCES workflow_cases(workflow_id),
    operation TEXT NOT NULL,
    tool_call_id UUID NOT NULL UNIQUE REFERENCES tool_calls(id),
    facts JSONB NOT NULL CHECK (jsonb_typeof(facts) = 'object'),
    PRIMARY KEY (workflow_id, operation)
);

CREATE TABLE appointments (
    id UUID PRIMARY KEY,
    patient_profile_id UUID NOT NULL REFERENCES patient_profiles(id),
    owner_user_id TEXT NOT NULL,
    doctor_reference TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes BETWEEN 5 AND 240),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status = 'draft'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX claims_owner_created_idx ON claims(owner_user_id, created_at DESC);
CREATE INDEX appointments_owner_starts_idx ON appointments(owner_user_id, starts_at);
