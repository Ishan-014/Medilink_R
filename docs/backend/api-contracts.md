# Backend integration contracts (Part 8)

Implemented: health, patient auth, current identity, admin provisioning, workflow
creation/list/detail/plan/transitions/cancellation, and persistent document APIs.
See [part-2.md](part-2.md) for auth/workflows and [part-3.md](part-3.md) for document
setup, permissions and extraction handoff. [Part 4](part-4.md) adds controlled
hospital-tool execution and trace metadata. [Part 5](part-5.md) adds managed
onboarding/claim preparation and local patient/claim/appointment records.
[Part 6](part-6.md) adds the graph service boundary and graph-dependent claim
evidence. [Part 7](part-7.md) adds authoritative risk policy, approval queues,
canonical action previews, independent approve/reject decisions and single-use
approved execution. [Part 8](part-8.md) adds audit/event feeds, paginated workflow
traces, consistent backend error codes and authoritative receipt reconciliation. Existing
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
| POST `/api/onboarding` | Patient intake; optional staff-selected patient_user_id | Managed case and draft profile; idempotent start |
| PATCH `/api/onboarding/{workflow_id}` | expected_version and supplied intake corrections | Resume a case waiting for data |
| POST `/api/claims` | Confirmed patient_profile_id, policy/procedure/amount/currency | Managed claim-preparation case |
| PATCH `/api/claims/{workflow_id}/intake` | expected_version and supplied corrections | Resume claim evidence collection |
| GET `/api/workflows/{id}/case` | Workflow UUID | Authorized managed phase/missing fields/entity reference |
| POST `/api/workflows/{id}/advance` | expected_version | Doctor/admin: one recipe-bound MCP operation |
| GET `/api/workflows/{id}/evidence` | Workflow UUID | Authorized selected typed facts with source-call IDs |
| GET `/api/patients/{id}` | Local profile UUID | Authorized local profile; not the legacy EHR endpoint |
| GET `/api/claims/{id}` | Claim UUID | Authorized claim record |
| POST `/api/appointments` | Profile UUID, doctor reference, future aware time, duration | Persistent appointment draft; never a false booking |
| GET `/api/appointments/{id}` | Appointment UUID | Authorized appointment draft |
| GET `/api/graph/health` | Bearer token | Admin/auditor: configured graph facade readiness |
| POST `/api/workflows/{id}/graph-queries` | expected_version, step_id, confirmed patient_profile_id, fixed operation | Doctor/admin: validated result and persisted reference metadata |
| GET `/api/workflows/{id}/graph-queries` | limit, offset | Authorized workflow readers: references/counts only |
| GET `/api/risk-policy` | Bearer token | Authoritative policy/version and enabled write categories |
| GET `/api/approvals` | limit, offset | Doctor/admin pending review queue |
| GET/POST `/api/workflows/{id}/approvals` | GET history; POST expected_version, reason | Scoped metadata / request or refresh canonical action review |
| GET `/api/workflows/{id}/approvals/{approval_id}/review` | Approval UUID | Authorized reviewer: canonical arguments, scope match and events |
| POST `/api/workflows/{id}/approve` or `/reject` | expected_version, approval_id, reason | Independent authorized reviewer; persisted decision |
| GET `/api/workflows/{id}/trace` | limit, offset | Scoped current workflow/steps and paginated audit/system history with next_offset |
| GET `/api/audit` | limit, offset, workflow_id, actor_user_id, outcome, after/before | Admin/auditor: typed AuditRecord page with next_offset |
| GET `/api/system-events` | limit, offset, workflow_id, code, after/before | Admin/auditor: typed SystemEvent page with next_offset |
| POST `/api/workflows/{id}/tool-calls/{call_id}/reconcile` | expected_version, reason | Admin: authoritative receipt lookup; never retries the write |
| GET `/api/workflows/{id}/reconciliations` | limit, offset | Scoped attempt metadata, actor/reason and authoritative outcome references |

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
`high`; authoritative risk classification and approval enforcement are implemented in Part 7.

`DocumentMetadata` contains UUID `id`, `filename`, `media_type`, `size_bytes`,
`extraction_status` (`pending`, `processing`, `completed`, `failed`), `created_at`,
`updated_at`, `owner_user_id`, `extraction_version` and optional safe
`extraction_error_code`. Raw files remain in private storage. Extraction results
are persisted separately from metadata and are subject to content-access checks.
`AuditEvent` contains UUID `id`, optional `workflow_id`, `actor_user_id`, `action`,
`resource_type`, `resource_id`, `outcome` (`success`, `denied`, `failure`), and
`created_at`. Part 8's `AuditRecord` adds `sequence`, `request_id` and allowlisted
`metadata`. Document persistence is implemented in Part 3 and atomic audit
persistence in Part 8. Date filters require timezones; trace/audit/event pages
return `next_offset` until exhausted. See the Part 8 receipt contract before
connecting a recovery facade.

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

Part 5 managed workflows add `managed_kind` and `subject_user_id` to workflow
responses. Generic plan/status mutations cannot bypass the managed domain service.
Actual creation/submission remains blocked pending Part 7; prepared claims are
not submitted claims. See the Part 5 contract table for required business facts
in EHR, billing and insurance responses.

Part 6 adds `use_graph: true` to new managed claims. Such claims require current
successful graph relationship/dependency/history receipts before reaching approval.
The requirement cannot be removed by intake updates; old query metadata remains
traceable after explicit read refreshes. Invalid/incomplete/unavailable graph data
never becomes an empty success or claim approval. Native NebulaGraph schemas and
ingestion remain with the graph owner. See the Part 6 facade contract for IDs,
result references, item shapes and safe `GRAPH_*` error codes.
