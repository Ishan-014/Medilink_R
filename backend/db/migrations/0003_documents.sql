CREATE TABLE documents (
    id UUID PRIMARY KEY,
    owner_user_id TEXT NOT NULL CHECK (length(btrim(owner_user_id)) > 0),
    filename TEXT NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
    media_type TEXT NOT NULL CHECK (media_type IN
        ('application/pdf','text/plain','image/png','image/jpeg')),
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    storage_key TEXT NOT NULL UNIQUE CHECK (storage_key ~
        '^[0-9a-f-]{36}\.(pdf|txt|png|jpg)$'),
    extraction_status TEXT NOT NULL DEFAULT 'pending' CHECK
        (extraction_status IN ('pending','processing','completed','failed')),
    extraction_version INTEGER NOT NULL DEFAULT 0 CHECK (extraction_version >= 0),
    extraction_token UUID,
    extraction_error_code TEXT CHECK (extraction_error_code IN
        ('EXTRACTION_FAILED','SOURCE_UNAVAILABLE','NO_READABLE_TEXT')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((extraction_status = 'processing') = (extraction_token IS NOT NULL)),
    CHECK ((extraction_status = 'failed') = (extraction_error_code IS NOT NULL))
);

CREATE TABLE document_extraction_results (
    document_id UUID PRIMARY KEY REFERENCES documents(id),
    result JSONB NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE workflow_documents (
    workflow_id UUID NOT NULL REFERENCES workflow_runs(id),
    document_id UUID NOT NULL REFERENCES documents(id),
    PRIMARY KEY (workflow_id, document_id)
);

-- Part 2 rejected nonempty references. Do not silently adopt unmanaged IDs.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM workflow_runs WHERE document_ids <> '[]'::jsonb) THEN
        RAISE EXCEPTION 'Unmanaged document references require an explicit migration';
    END IF;
END $$;

CREATE INDEX documents_owner_created_idx ON documents(owner_user_id, created_at DESC);
CREATE INDEX documents_extraction_status_idx ON documents(extraction_status, created_at);
