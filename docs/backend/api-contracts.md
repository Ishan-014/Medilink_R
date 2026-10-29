# Backend integration contracts (Part 1 baseline)

Implemented now: `/api/health` and `/api/health/ready`. All other `/api` routes below
are **planned contracts**, not implemented endpoints. Existing unprefixed routes
remain available with their existing formats. Authentication always uses a bearer
access token on protected routes; workflow ownership/roles come from the verified
identity, never from a user-submitted role or owner ID.

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

## Planned API boundaries

| Method/path | Request | Response data / enforcement |
| --- | --- | --- |
| POST `/api/auth/register` | Patient registration fields, password; finalized in Part 2 | Current user; public callers cannot grant staff/admin/auditor roles |
| POST `/api/auth/login` | Credentials; staff auth stages retain their existing flow | Token, bearer type, expiry, current user; finalized in Part 2 |
| GET `/api/me` | Bearer token | `CurrentUser` (`user_id`, `email`, `role`) |
| POST `/api/workflows` | `WorkflowCreate` | `WorkflowRun`; owner assigned server-side; 201 |
| GET `/api/workflows` | Pagination/status/type filters; finalized in Part 2 | Authorized workflow collection |
| GET `/api/workflows/{id}` | Workflow UUID | `WorkflowRun` and ordered steps; ownership/role checks |
| POST `/api/documents` | Multipart file; types/limits finalized in Part 3 | `DocumentMetadata`; 201 |
| GET `/api/documents/{id}` | Document UUID | Authorized `DocumentMetadata`; no public storage URL |
| GET `/api/workflows/{id}/trace` | Workflow UUID | Authorized plan, steps, tool-call references, approvals, errors, and audit events; finalized in Part 8 |
| GET `/api/audit` | Pagination/date/status filters; finalized in Part 8 | Authorized `AuditEvent` collection |

Roles: `patient`, `doctor`, `receptionist`, `admin`, `auditor`. Doctor and receptionist
are distinct staff roles; there is no unrestricted shared `staff` role.

`WorkflowCreate` accepts only:

```json
{"workflow_type":"onboarding","request":"Register this synthetic patient","document_ids":[]}
```

Workflow types: `onboarding`, `insurance_claim`, `appointment`. Requests must contain
1–10,000 characters after trimming; up to 20 document UUID references are allowed.
Unknown fields (including owner, status, risk, and approval) are rejected. Raw
documents are stored separately from workflow records.

`WorkflowRun` contains UUID `id`, `owner_user_id`, `workflow_type`, `status`, UTC
`created_at`/`updated_at`, and ordered `steps`. Each step contains UUID `id`, a
zero-based `position`, `action`, `status`, optional `risk`, and optional
`result_reference`. References are not raw tool payloads or documents.

Workflow statuses: `CREATED`, `PLANNING`, `WAITING_FOR_DATA`, `EXECUTING`,
`WAITING_FOR_APPROVAL`, `COMPLETED`, `FAILED`, `REJECTED`, `CANCELLED`. Part 2 must
enforce transitions, authenticated ownership, and appropriate resume behavior;
the status enum alone does not implement an engine. Risk tiers are `low`, `medium`,
`high`; authoritative risk classification/approval enforcement arrives in Part 7.

`DocumentMetadata` contains UUID `id`, `filename`, `media_type`, `size_bytes`,
`extraction_status` (`pending`, `processing`, `completed`, `failed`), and `created_at`.
`AuditEvent` contains UUID `id`, optional `workflow_id`, `actor_user_id`, `action`,
`resource_type`, `resource_id`, `outcome` (`success`, `denied`, `failure`), and
`created_at`. Neither document nor audit persistence is implemented in Part 1.

The backend contract is separate from LangGraph's internal agent state. AI/MCP
owners should map their execution results to these boundaries without sharing
credentials or giving the agent authority to self-approve actions. API routes,
payloads, pagination, and error extensions must be reviewed across owners before
those later parts are implemented.
