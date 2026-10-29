# Backend Part 8: audit, observability and reconciliation

Based on `backend-part-7`. This implements Person 1's backend responsibilities
for roadmap Part 8. MCP implementations, AI agents, frontend and graph code
owned by the other members are unchanged. The recovery boundary is ready for an
integration-owned receipt service; no live simulator integration is claimed.

## Setup

Use the existing foundation requirements and apply the new forward migration:

```bash
python -m pip install -r backend/requirements-foundation.txt
python -m backend.db.migrate --status
python -m backend.db.migrate
python -m uvicorn backend.application:create_app --factory --reload --log-config backend/config/logging.json --no-access-log
```

The logging configuration enables the `medilink.events` JSON request logger and
suppresses library access logging that could otherwise include raw URLs. It also
works with `backend.main:app` when the optional legacy dependencies are installed.
The existing database, signing and MCP settings from previous parts still apply.
App startup and liveness do not contact external recovery services.

Configure recovery only after the integration owner supplies the receipt facade:

```env
MEDILINK_RECOVERY_SERVICE_URL=http://127.0.0.1:8300
MEDILINK_RECOVERY_SERVICE_TOKEN=your-server-side-receipt-service-token
MEDILINK_RECOVERY_TIMEOUT_SECONDS=10
MEDILINK_RECONCILIATION_STALE_SECONDS=300
```

A blank URL disables reconciliation while leaving other features available.
Remote hosts require HTTPS and a service token. Plain HTTP is limited to loopback.
URL credentials, queries, fragments, malformed ports and redirects are rejected;
ambient HTTP proxy configuration is disabled. Secrets/URLs are excluded from
settings repr. Timeout accepts 0.1–60 seconds. A pending write must be at least
300–86400 seconds old (default 300) before receipt lookup is allowed; this exceeds
the existing maximum MCP request deadline.

## Persistent audit and traces

Migration `0008` adds `audit_logs`, `system_events`, `external_reconciliations`
and indexes for workflow/time, actor/time and operational code filters. Earlier
migrations are unchanged. Existing records receive explicitly labelled
`*.snapshot` entries with `metadata.snapshot=true` at upgrade. Snapshots preserve
current metadata; they do not invent past transitions or decision timestamps.

Database triggers record successful mutations to workflow runs/steps/document
links, document metadata/status, managed cases/evidence references, patient and
claim mirrors, appointment drafts, tool calls, graph query references, approvals,
approval events, action reservations, reconciliation attempts and user accounts.
The audit entry commits or rolls back with its mutation. This prevents a crash
between a domain update and a separate audit write from losing that history.

Entries contain actor, request ID, action/resource references, time, and an explicit
metadata allowlist: status/version changes, known operations, risk, result IDs,
document extraction versions, graph revisions/counts and error codes. They exclude
raw workflow requests, intake, EHR results, document text/names, passwords, JWTs,
approval tokens and integration credentials. Approval reasons remain in their
existing append-only table and appear in authorized workflow traces.

`audit_logs.outcome=success` means the database mutation succeeded; a mutation can
record a failed business state. Tool/graph failures also generate operational
system events in the same transaction. HTTP validation, authentication,
authorization, database and integration failures produce normalized system events;
401/403 API failures also produce `access.denied` audit entries. Failed HTTP
telemetry is best effort when PostgreSQL is unavailable and emits only a sanitized
`telemetry_unavailable` log. Successful HTTP requests emit timing logs; this part
does not persist a separate access-audit row for every successful read.

Request context carries the authenticated actor and server-generated request ID
through sync service threads into transaction-local PostgreSQL settings. Non-HTTP
workers default to `system:backend`; migration snapshots use `system:migration`.
Context does not leak between requests. UPDATE/DELETE of audit/system records is
blocked by database triggers. Deployment must additionally restrict the application
DB role from DDL/TRUNCATE and trigger management; an owner/superuser is not an
audit integrity boundary. Retention/export policy is an operator responsibility.

## API

All endpoints use existing authentication, success/error envelopes and request IDs.
Trace/audit/recovery responses are private and not cached. OpenAPI exposes typed
record, pagination, workflow trace and reconciliation models.

| Method/path | Permission and behavior |
| --- | --- |
| GET `/api/audit` | Admin/auditor; filter by workflow, actor, outcome and date |
| GET `/api/system-events` | Admin/auditor; filter by workflow, error/event code and date |
| GET `/api/workflows/{id}/trace` | Existing workflow readers; current workflow/steps plus chronological audit/system timeline |
| POST `/api/workflows/{id}/tool-calls/{call_id}/reconcile` | Admin; query authoritative outcome of an unresolved approved managed write |
| GET `/api/workflows/{id}/reconciliations` | Existing workflow readers; reference-only lookup attempts, actor/reason and outcomes |

List and trace routes accept `limit` (1–100, default 50) and `offset` (0–100000).
Global audit/events and trace return `next_offset`, or null when exhausted. Follow
all pages to inspect a complete history. Date filters `after` (inclusive) and
`before` (exclusive) require explicit timezones and an increasing range. Workflow
scope still hides another patient's records; global audit/event feeds require
admin or auditor access. Trace data excludes clinical result payloads and raw
requests. Linked documents contribute metadata/extraction history only. Each trace
page uses one repeatable-read snapshot across summary and timeline. Pages are
separate snapshots, so refresh a live workflow when its history changes.

Reconciliation body:

```json
{"expected_version": 8, "reason": "Verify the original external write receipt"}
```

The caller cannot supply an outcome, receipt body, risk, approval state or result
ID. Fetch the current workflow version before each attempt and after recovery.

## Reconciliation and integration-owner contract

Only the existing approved managed patient-creation and claim-submission writes
are eligible. An uncertain call is eligible immediately; a still-pending call must
exceed the configured stale age. The backend validates its consumed approval,
reservation and persisted argument digest before reserving a lookup. Read calls,
unapproved calls, completed calls, mismatched workflows and fresh pending writes
are rejected. Receipt lookup does not issue a second MCP write or create another
approval. Concurrent requests reserve one lookup; abandoned lookup workers can be
superseded after the lookup timeout plus 60 seconds. A late response from a
superseded lookup cannot change state.

The backend's `ReconciliationClient.lookup(settings, request)` makes a bounded,
read-only HTTP request:

```http
POST /action-receipts/lookup
Authorization: Bearer <server-side receipt-service-token>
Content-Type: application/json
```

```json
{
  "reconciliation_id": "00000000-0000-0000-0000-000000000001",
  "workflow_id": "00000000-0000-0000-0000-000000000002",
  "tool_call_id": "00000000-0000-0000-0000-000000000003",
  "approval_id": "00000000-0000-0000-0000-000000000004",
  "operation": "ehr.create_patient",
  "idempotency_key": "approved-step:00000000-0000-0000-0000-000000000005",
  "arguments_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

A 200 response must echo **every field exactly**, then add:

```json
{
  "outcome": "succeeded",
  "external_reference": "external-receipt:durable-receipt-id",
  "result": {
    "id": "EHR-SYNTHETIC",
    "name": "Synthetic Patient",
    "date_of_birth": "2000-01-01",
    "email": "patient@example.com"
  }
}
```

The example shows only the additional response fields; the actual response must
also include all echoed request fields. Responses are limited to 64 KiB, reject
extra fields/non-finite JSON, and validate domain evidence using the existing
Part 5 models. Claim success additionally requires the original patient, policy,
procedure, amount/currency, `covered: true`, `claim_id`, and `status: submitted`.
The facade resolves the original durable write by its key/digest; it must not
perform a mutation to answer the lookup.

| External outcome | Required evidence | Backend result |
| --- | --- | --- |
| `succeeded` | Receipt reference and valid, matching domain result | Atomically confirm patient/claim, call, reservation and workflow; record attempt success |
| `not_applied` | Receipt reference, `result: null`; durable guarantee the original write cannot later apply | Close original call/reservation as failed; keep workflow failed; a new case may be created |
| `pending` | `external_reference: null`, `result: null` | Keep original write unresolved; allow another later read-only lookup |

A missing receipt is **not proof of non-application**. The facade must return
`pending` unless it can guarantee a terminal outcome. Timeouts, outages, malformed
or mismatched evidence record a failed lookup while leaving the original write
unresolved. Original status/error are retained in the attempt and audit history.
The original approval stays consumed for every outcome; no recovery path resets
it or retries its reservation. A new case after confirmed non-application must
collect evidence and pass the normal approval policy again.

The MCP owner must implement durable idempotency/receipt storage and this lookup
facade (or provide a compatible backend adapter). The AI owner retains bounded
execution and approved alternative-path recovery. The frontend owner can consume
these APIs for auditor timelines and admin recovery controls. This part supplies
and tests the backend boundary without changing those members' code.

## Stable errors and logging

Existing `VALIDATION_ERROR`, `UNAUTHENTICATED`, `FORBIDDEN`, conflict, MCP and graph
codes remain. Additional backend handoff codes are:

- `DATABASE_UNAVAILABLE` (503), `DATABASE_TIMEOUT` (504), `DATABASE_CONFLICT` (409).
- `AI_UNAVAILABLE` (503), `AI_TIMEOUT` (504), `AI_OUTPUT_INVALID` (502), available
  through `BackendError` for the future AI adapter; legacy agent behavior is unchanged.
- `RECOVERY_NOT_CONFIGURED` (503), `RECOVERY_UNAVAILABLE` (503),
  `RECOVERY_TIMEOUT` (504), `RECOVERY_OUTPUT_INVALID` (502), `RECOVERY_NOT_READY` (409).

Readiness preserves its existing `SERVICE_UNAVAILABLE` contract. Unknown errors
return `INTERNAL_ERROR`, with no SQL/provider exception text or stack trace.
Validation details omit submitted values, arbitrary extra keys and nested input
keys. Operational JSON logs contain normalized route templates, actor/request IDs,
HTTP status, code and duration; they omit actual URLs, query strings, headers,
request bodies, reasons and exception messages. Restrict log-reader access because
actor and workflow IDs still link activity.

## Verification

Use an empty disposable PostgreSQL database only:

```bash
MEDILINK_TEST_DB_DSN='dbname=medilink_test host=localhost user=postgres password=your-test-password' python -m pytest tests/backend -q
```

Coverage includes atomic/append-only auditing, upgrade snapshots, role and patient
scope, paginated traces, constant service-query count as history grows, request
context isolation, sanitized structured logs, typed database/AI errors, telemetry
outages, authoritative success/non-application/pending receipts, stale writes,
abandoned workers, concurrent lookups and malformed/outage/timeout responses.
Tests use synthetic MCP and HTTP receipt peers; live hospital integration, deployed
load capacity and cross-team end-to-end acceptance remain integration work.
