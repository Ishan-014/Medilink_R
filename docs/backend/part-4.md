# Backend Part 4: hospital integration adapters and tool-call records

Based on `backend-part-3`. This implements Person 1's MCP connectivity configuration,
health checks, simulator-independent backend adapter and durable workflow-linked
tool-call records. Project MCP server, agent and frontend files are unchanged.
The other owners' simulators, tool authorization and agent integration remain their
work; the roadmap's whole-team checkpoint is not claimed by this backend change.

## Setup

Activate your environment and run from the repository root:

```bash
python -m pip install -r backend/requirements-foundation.txt
python -m backend.db.migrate --status
python -m backend.db.migrate
python -m uvicorn backend.application:create_app --factory --reload
```

Migration `0004` adds `tool_calls` and a composite constraint linking each call to
both its workflow and its step. Earlier migrations and existing data are retained.
App startup does not connect to or launch hospital servers. Ordinary process and
PostgreSQL health endpoints retain their existing behavior.

## Connection configuration

By default, all integrations are unconfigured. Copy
`backend/config/mcp.example.json` to the Git-ignored
`backend/config/mcp.local.json`, configure the servers actually available, and set:

```env
MEDILINK_MCP_CONFIG_FILE=backend/config/mcp.local.json
```

Relative environment paths resolve from the repository root. The example shows
an EHR stdio process and an optional laboratory HTTP endpoint; it does not create
or claim that a lab server already exists. Remove any example server you are not
running. Configuration is read on each integration request, so changes take effect
without app restart. Invalid configuration fails closed with a safe error code.

The five supported server identifiers are `ehr`, `lab`, `scheduling`, `insurance`,
and `billing`. Each configured server selects one transport:

```json
{
  "servers": {
    "ehr": {
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "backend.mcp.server"],
      "env_vars": ["MEDILINK_DB_NAME", "MEDILINK_DB_HOST", "MEDILINK_DB_PORT", "MEDILINK_DB_USER", "MEDILINK_DB_PASSWORD"],
      "timeout_seconds": 15
    }
  }
}
```

For stdio, select the correct Python executable for your environment. Commands run
without a shell, from the repository root unless `cwd` is explicitly configured.
Only SDK-safe environment defaults and the named `env_vars` are passed explicitly;
missing named variables fail configuration validation. Process stderr is discarded
to avoid forwarding subsystem logs containing sensitive data. These are trusted
operator-owned processes, not a sandbox: a subprocess may still read its working
directory or load its own `.env`. The existing EHR server retains its own original
dependencies, including its service imports; the foundation requirements do not
replace that server's dependency setup.

HTTP example:

```json
{
  "servers": {
    "lab": {
      "transport": "streamable_http",
      "url": "http://127.0.0.1:8102/mcp",
      "bearer_token_env": "MEDILINK_LAB_MCP_TOKEN",
      "timeout_seconds": 15
    }
  }
}
```

The bearer token value comes only from the server environment. URLs cannot contain
credentials, queries or fragments. Plain HTTP is limited to loopback hosts; remote
URLs require HTTPS. Redirects and ambient proxy configuration are disabled for this
client. Each operation has a configurable deadline (0.1–120 seconds), with SDK
transport cleanup afterward. Connections/processes are scoped to one request;
there is no persistent pool or automatic retry in this part.

No API accepts endpoint URLs, commands, credentials or transport settings from its
caller. Configuration paths, URLs, commands, arguments, environment-variable names
and token values are not returned by integration inspection endpoints.

## API boundaries and permissions

| Method/path | Permission and behavior |
| --- | --- |
| GET `/api/integrations` | Admin/auditor; five server names, configured flag, transport only |
| GET `/api/integrations/{name}/health` | Admin/auditor; MCP initialize, ping, discovery and schema checks |
| POST `/api/workflows/{id}/tool-calls` | Doctor/admin; execute one allowlisted read matching a workflow step |
| GET `/api/workflows/{id}/tool-calls` | Existing workflow read permissions; paginated metadata, no clinical payloads |

Health returns the names of the backend-allowlisted reads discovered on the server.
It does not invoke hospital tools or query patient data. Unconfigured/unavailable
servers return controlled errors. Connection health does not certify clinical data
quality, consent, the server's implementation, or workflow correctness.

Tool-call example:

```json
{
  "expected_version": 3,
  "step_id": "replace-with-the-persisted-step-uuid",
  "operation": "ehr.get_patient",
  "arguments": {"patient_id": "PAT-001"}
}
```

The workflow must be `EXECUTING`, and its plan step's `action` must exactly equal
the operation (`ehr.get_patient` here). The supplied version must match, the step
must not have started, all earlier steps must be completed and attached documents
must have completed extraction. Locks serialize competing dispatch requests.
Patients, receptionists and auditors cannot execute arbitrary hospital reads in
this boundary. Clinical scope follows the prototype's doctor/admin access model;
patient-to-EHR identity/consent mapping is not invented here.

The backend owns its allowlist in `backend/integrations/policy.py`. The available
read names follow the agreed EHR, lab, scheduling, insurance and billing catalog.
Server annotations and client arguments cannot grant new permissions. All known
write tools are blocked, even for administrators, until the authoritative risk/
approval gate is implemented. Unknown tools are also denied. These controls apply
to this new adapter, not to any separate code path that does not use it.

The existing agent still calls its existing functions directly; it is deliberately
not rewired in a backend-only part. Its calls are not retrospectively claimed as
adapter-traced or approval-gated. Later integration must route the agent through
the authorized backend boundary.

## Protocol and validation

`backend/integrations/transport.py` uses the official MCP client SDK for stdio and
Streamable HTTP. `HospitalClient` presents a consistent discovery/health/call
interface independent of simulator database internals. MCP code and credentials
stay server-side; no frontend protocol client is introduced.

Each call initializes a session, discovers its exact tool, validates both JSON
schemas, validates arguments, executes once and validates structured results.
External schema references are disabled. Tool lists are capped at 128 and reject
pagination, schemas/arguments at 64 KiB and structured results at 1 MiB. JSON must
be finite/serializable; excessive schema nesting and malformed schemas are denied.
The limits apply to parsed JSON values, not a guarantee about all remote wire-level
buffering. Servers must provide explicit input and output schemas with structured
JSON results. SDK wrappers such as `{ "result": ... }` are preserved and validated,
not guessed or stripped by the adapter. Text-only or malformed results cannot be
reported as success or passed onward as validated data.

## Persistence and workflow behavior

Before contacting MCP, the backend commits a `pending` call record and marks the
step `EXECUTING`. Metadata includes call UUID, workflow/step UUIDs, actor ID,
server, tool, input fingerprint and start time. Finalization records status,
finish time, duration, safe error code and an optional `tool-call:<UUID>` reference.
The database enforces matching workflow/step associations and one pending call per
step. Raw arguments, credentials, error strings and clinical results are not
persisted in this table.

Validated success marks the step completed and stores the reference. The HTTP
response includes call metadata plus the validated result for the authorized
doctor/admin. Trace endpoints contain metadata only. Sensitive responses carry
`Cache-Control: private, no-store`. A successful tool call does not automatically
complete the whole workflow; the existing transition service still checks every
step's confirmed result before accepting `COMPLETED`.

Failures finalize the call without a success reference and fail the step/workflow
when it is still executing. A timeout/disconnect before dispatch is a failed
attempt; after dispatch it is `uncertain`. Cancellation records actual results
when they arrive but never reopens or completes a cancelled workflow. Repeated or
concurrent step requests cannot execute the same step twice. No automatic retry is
attempted; uncertain/stale pending records require the later reconciliation work.
Process crashes or a DB failure during finalization can leave a pending record,
which is intentionally not treated as proof of success.

Safe integration codes:

| Code | HTTP status |
| --- | --- |
| MCP_NOT_CONFIGURED / MCP_CONFIG_INVALID / MCP_UNAVAILABLE | 503 |
| MCP_TIMEOUT | 504 |
| MCP_TOOL_UNAVAILABLE / MCP_SCHEMA_INVALID / MCP_OUTPUT_INVALID / MCP_REMOTE_ERROR | 502 |
| MCP_INPUT_INVALID | 422 |
| MCP_WRITE_BLOCKED | 403 |

Normal auth, ownership and workflow conflicts retain their existing 401/403/404/
409 responses. Error responses do not expose remote exception details.

## Verification and handoff

```bash
python -m pip install -r backend/requirements-test.txt
MEDILINK_TEST_DB_DSN='dbname=medilink_part4_test host=localhost user=postgres' \
  python -m pytest tests/backend -q
```

Use an empty disposable database. Tests cover ordered calls to two hospital
capabilities, metadata committed before dispatch, authorization, denied writes,
input/output schemas, timeouts, disconnections, cancellation, duplicate/concurrent
calls, completion evidence, migration upgrade integrity and safe trace retrieval.
Synthetic test peers exercise real official-SDK stdio and authenticated local HTTP
handshakes, discovery and tool execution. They do not alter or substitute for the
project's production simulator implementations or its existing AI tests.

Person 2 should provide the configured servers with stable schemas, structured
results, appropriate authorization and real simulator behavior. Person 3 should
consume the backend's validated execution boundary when connecting workflows.
Write approvals, advanced retry/recovery and complete audit views remain later
parts of the roadmap.

Validation result: **141 backend tests passed** against an isolated PostgreSQL 18
database, including real stdio/HTTP protocol checks. Static checks passed for all
changed Python files.
