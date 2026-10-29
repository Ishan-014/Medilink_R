# Backend integration contracts (Part 4)

Implemented: health, patient auth, current identity, admin provisioning, workflow
creation/list/detail/plan/transitions/cancellation, and persistent document APIs.
See [part-2.md](part-2.md) for auth/workflows and [part-3.md](part-3.md) for document
setup, permissions and extraction handoff. [Part 4](part-4.md) adds controlled
hospital-tool execution and trace metadata. Audit routes remain planned. Existing
unprefixed routes retain their response formats.
Ownership and current roles are always resolved server-side.

## Response format

Success:

```json
{"data":{"status":"ok","service":"medilink-backend"},"request_id":"generated-id"}
```

Error (with the corresponding HTTP status):

```json
{"error":{"code":"FORBIDDEN","message":"Access denied","details":[]},"request_id":"generated-id"}
```

The server generates `X-Request-ID` for correlation. Validation error details
contain location/type, not submitted input. Unexpected failures return generic
messages. Stable codes currently include `INVALID_REQUEST`, `UNAUTHENTICATED`,
`FORBIDDEN`, `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `CONFLICT`, `FILE_TOO_LARGE`,
`UNSUPPORTED_MEDIA_TYPE`, `VALIDATION_ERROR`, `RATE_LIMITED`, `SERVICE_UNAVAILABLE`,
and `INTERNAL_ERROR`; other HTTP statuses use `HTTP_ERROR`.

## API boundaries

| Method/path | Request | Response data / enforcement |
| --- | --- | --- |
| POST `/api/auth/register` | `full_name`, `email`, password (12–1024 characters) | 201: current patient; callers cannot submit a role |
| POST `/api/auth/login` | Patient `email`, `password`; staff use `/auth/login/*` stages | Token, bearer type, `expires_in`, current user |
| GET `/api/me` | Bearer token | `CurrentUser` (`user_id`, `email`, `role`) |
| POST `/api/workflows` | `WorkflowCreate` | `WorkflowRun`; owner assigned server-side; 201 |
| GET `/api/workflows` | `limit` (1–100), `offset`, `status`, `workflow_type` | Authorized workflow collection |
| GET `/api/workflows/{id}` | Workflow UUID | `WorkflowRun` and ordered steps; ownership/role checks |
| POST `/api/admin/users` | `full_name`, `email`, `password`, `role` | Admin-only account creation; 201 |
| PUT `/api/workflows/{id}/plan` | `expected_version`, ordered `steps` (`action`) | Staff-only plan persistence during PLANNING |
| POST `/api/workflows/{id}/transitions` | `expected_version`, target `status` | Staff-only, valid transition; conflicts return 409 |
| POST `/api/workflows/{id}/cancel` | `expected_version` | Owner/authorized staff cancellation |
| POST `/api/documents` | Multipart `file`; PDF/TXT/PNG/JPEG, 5 MiB default | `DocumentMetadata`; 201, extraction pending |
| GET `/api/documents` | `limit` (1–100), `offset` | Scoped metadata list |
| GET `/api/documents/{id}` | Document UUID | Authorized `DocumentMetadata`; no public storage URL |
| GET `/api/documents/{id}/content` | Document UUID | Authenticated source attachment; no JSON envelope |
| GET `/api/documents/{id}/extraction` | Document UUID | Stored extraction status, version, fields/text and safe error code |
| GET `/api/integrations` | Bearer token | Admin/auditor: configured flags and transport types |
| GET `/api/integrations/{name}/health` | Configured server name | Admin/auditor: protocol/schema health, available backend read names |
| POST `/api/workflows/{id}/tool-calls` | `expected_version`, `step_id`, `operation`, `arguments` | Doctor/admin: persisted call metadata and validated structured result |
| GET `/api/workflows/{id}/tool-calls` | `limit`, `offset` | Authorized workflow readers: call metadata without payloads |
| GET `/api/workflows/{id}/trace` (planned) | Workflow UUID | Authorized plan, steps, tool-call references, approvals, errors, and audit events; finalized in Part 8 |
| GET `/api/audit` (planned) | Pagination/date/status filters; finalized in Part 8 | Authorized `AuditEvent` collection |

Roles: `patient`, `doctor`, `receptionist`, `admin`, `auditor`. Doctor and receptionist
are distinct staff roles; there is no unrestricted shared `staff` role.

`WorkflowCreate` accepts only:

```json
{"workflow_type":"onboarding","request":"Register this synthetic patient","document_ids":[]}
```

Workflow types: `onboarding`, `insurance_claim`, `appointment`. Requests must contain
1–10,000 characters after trimming. The schema reserves up to 20 document UUID
references; Part 3 checks existence/content access, rejects duplicates, and persists
foreign-key-backed links atomically. Other patients' documents return 404.
Unknown fields (including owner, status, risk, and approval) are rejected. Raw
documents are stored separately from workflow records.

`WorkflowRun` contains UUID `id`, `owner_user_id`, `workflow_type`, `status`, UTC
`created_at`/`updated_at`, optimistic concurrency `version`, and ordered `steps`.
`document_ids` contains linked source UUIDs. Each step contains UUID `id`, a
zero-based `position`, `action`, `status`, optional `risk`, and optional
`result_reference`. References are not raw tool payloads or documents.

Workflow statuses: `CREATED`, `PLANNING`, `WAITING_FOR_DATA`, `EXECUTING`,
`WAITING_FOR_APPROVAL`, `COMPLETED`, `FAILED`, `REJECTED`, `CANCELLED`. The Part 2 service enforces
transitions, authenticated ownership, optimistic versions, and protected approval
states. It persists plans without invoking the AI/MCP layer. Risk tiers are `low`, `medium`,
`high`; authoritative risk classification/approval enforcement arrives in Part 7.

`DocumentMetadata` contains UUID `id`, `filename`, `media_type`, `size_bytes`,
`extraction_status` (`pending`, `processing`, `completed`, `failed`), `created_at`,
`updated_at`, `owner_user_id`, `extraction_version` and optional safe
`extraction_error_code`. Raw files remain in private storage. Extraction results
are persisted separately from metadata and are subject to content-access checks.
`AuditEvent` contains UUID `id`, optional `workflow_id`, `actor_user_id`, `action`,
`resource_type`, `resource_id`, `outcome` (`success`, `denied`, `failure`), and
`created_at`. Document persistence is implemented in Part 3; audit persistence
remains Part 8 work.

The backend contract is separate from LangGraph's internal agent state. AI/MCP
owners should map their execution results to these boundaries without sharing
credentials or giving the agent authority to self-approve actions. API routes,
payloads, and error extensions must be reviewed across owners before integration.

Part 4 integration errors add `MCP_NOT_CONFIGURED`, `MCP_CONFIG_INVALID`,
`MCP_UNAVAILABLE`, `MCP_TIMEOUT`, `MCP_TOOL_UNAVAILABLE`, `MCP_SCHEMA_INVALID`,
`MCP_INPUT_INVALID`, `MCP_OUTPUT_INVALID`, `MCP_REMOTE_ERROR`, and
`MCP_WRITE_BLOCKED`. Raw remote exceptions and credentials are never returned.
Tool calls require the matching ordered plan step in an executing workflow. All
writes remain blocked until the Part 7 gate exists. The unchanged agent's direct
function calls are not routed through this new boundary by Part 4.
