# Backend Part 6: graph service boundary and workflow references

Based on `backend-part-5`. This implements Person 1's graph-query boundary, health
endpoint, persisted query/result references and controlled failure states. Graph
schema, ingestion, NebulaGraph deployment/query implementation, MCP servers, AI
agents and frontend code are unchanged.

The concrete backend transport is a read-only HTTP facade owned by the graph
implementation. It is **not the native NebulaGraph protocol**, and its URL must
not point at the native graphd port. The graph owner can implement this facade over
NebulaGraph or supply an adapter honoring the same typed health/query interface.
This change does not claim that a live NebulaGraph service is deployed or tested.

## Setup and migration

Activate the environment and run from the repository root:

```bash
python -m pip install -r backend/requirements-foundation.txt
python -m backend.db.migrate --status
python -m backend.db.migrate
python -m uvicorn backend.application:create_app --factory --reload
```

Configure a graph-owner HTTP facade in your local `.env`:

```env
MEDILINK_GRAPH_SERVICE_URL=http://127.0.0.1:8200
MEDILINK_GRAPH_SERVICE_TOKEN=your-server-side-read-only-service-token
MEDILINK_GRAPH_TIMEOUT_SECONDS=10
MEDILINK_GRAPH_MAX_RESULTS=100
```

The timeout accepts 0.1–60 seconds and the result limit 1–200. A blank URL leaves
graph access unconfigured. Configuration/credentials never come from request
bodies. URL userinfo, queries, fragments and invalid schemes are rejected. Remote
hosts require HTTPS and a configured service token; plain HTTP is limited to
loopback. Redirects and ambient proxy configuration are disabled. Tokens are sent
as service-side Authorization headers and are excluded from settings repr, API
responses and persisted query metadata.

App startup and the existing process/PostgreSQL health checks do not call the graph
service. Missing graph configuration does not disable unrelated workflows.

Migration `0006` adds `workflow_graph_queries`. Earlier schema/migrations and
existing cases are preserved. Query records reference both their workflow and its
actual step, plus the confirmed local patient profile. The database rejects
cross-workflow step references and multiple pending graph reads for one step.

## Backend API

| Method/path | Permission and behavior |
| --- | --- |
| GET `/api/graph/health` | Admin/auditor; check the configured facade's readiness |
| POST `/api/workflows/{id}/graph-queries` | Doctor/admin; execute one matching ordered graph step |
| GET `/api/workflows/{id}/graph-queries` | Authorized workflow readers; paginated reference metadata |

The existing JSON envelope and request IDs apply. Graph responses carry
`Cache-Control: private, no-store`. Patients/receptionists/auditors cannot execute
arbitrary clinical graph reads. Trace readers receive query/result references and
counts, not relationship items, clinical payloads or credentials.

Explicit query example:

```json
{
  "expected_version": 3,
  "step_id": "replace-with-persisted-step-uuid",
  "patient_profile_id": "replace-with-confirmed-local-profile-uuid",
  "operation": "claim_history"
}
```

The workflow must be executing, its step action must be `graph.claim_history`,
earlier steps must have completed and referenced documents must have completed
extraction. The backend checks the current version and the confirmed profile's
ownership/subject binding. Another patient's profile cannot be substituted into
a patient-owned/subject-bound workflow. Managed claims additionally bind the
query to their stored profile and intake. Staff-owned generic workflows retain
the prototype's existing clinical doctor/admin scope.

There is no public nGQL/Cypher/SQL/query-string endpoint. Only these read names are
accepted: `query_relationships`, `find_dependencies`, `claim_history`.

## Graph-dependent claims

Existing claims keep their Part 5 behavior. To require graph evidence for a new
claim, add the strict boolean `use_graph: true` to `POST /api/claims`. The managed
recipe becomes:

1. The five existing EHR/billing/insurance reads.
2. `graph.query_relationships`.
3. `graph.find_dependencies`.
4. `graph.claim_history`.
5. `insurance.submit_claim`, still blocked pending Part 7 approval controls.

The existing `/api/workflows/{id}/advance` endpoint dispatches exactly one next
step through the appropriate backend boundary. The first five successful hospital
receipts no longer suffice to prepare a graph-dependent claim. All three valid
graph receipts must also exist for the current step versions.

Unresolved dependency references pause the case in `WAITING_FOR_DATA` with
`graph_dependencies`. Graph errors fail the case/workflow without preparing or
submitting the claim. Empty **complete** results are valid evidence of no returned
relationships/history; a timeout, malformed reply or incomplete result is not
converted into an empty success.

The graph requirement cannot be removed through intake correction. An explicit
allowed correction refreshes the read steps while preserving query history; old
result references no longer satisfy the reset steps. Claims started without graph
requirements retain compatible idempotency fingerprints from Part 5. Mandatory
graph use for particular business policies is future policy work, not inferred
from an agent or historical graph outcome here.

## Facade contract for the graph owner

`GET {base}/health` must return HTTP 200 with exactly:

```json
{"status":"ready"}
```

The facade should perform its own NebulaGraph readiness checks before asserting
readiness. The backend cannot certify ingestion correctness or live database
health solely from a successful HTTP response.

`POST {base}/queries` receives a backend-generated typed request:

```json
{
  "query_id": "backend-generated-query-uuid",
  "workflow_id": "persisted-workflow-uuid",
  "operation": "claim_history",
  "patient_id": "EHR-SYNTHETIC",
  "policy_id": "POL-SYNTHETIC",
  "procedure_code": "PROC-SYNTHETIC",
  "max_results": 100
}
```

Policy/procedure references are derived from managed intake; generic graph queries
send null for those optional fields. Requests contain IDs/context only: no names,
birth dates, uploaded documents, extracted text, passwords, LLM keys or arbitrary
native graph commands.

HTTP 200 query replies must be application/json and match:

```json
{
  "query_id": "same-query-uuid",
  "operation": "claim_history",
  "patient_id": "EHR-SYNTHETIC",
  "result_reference": "graph-result:opaque-source-result-id",
  "graph_revision": "revision:synthetic-1",
  "complete": true,
  "items": [
    {"kind":"claim","reference":"claim:PRIOR","outcome":"approved"}
  ]
}
```

The query ID, operation and patient ID must match. Result references are opaque
`graph-result:` identifiers, not public file URLs. Revisions are source-owned
version references for traceability, not a guarantee of freshness relative to the
EHR. The graph owner must retain/reconcile source results as appropriate for those
references.

Supported item shapes:

- Relationships: `kind: relationship`, `source_reference`, uppercase `relation`
  label, and `target_reference`.
- Dependencies: `kind: dependency`, `reference`, strict boolean `resolved`.
- History: `kind: claim`, `reference`, and outcome `approved`, `rejected`, `pending`
  or `unknown`.

Each operation accepts only its corresponding item shape. References are bounded
identifier strings, not raw document/clinical text. Unknown fields, duplicate
items, nonfinite JSON, invalid IDs, a coerced/incomplete completeness flag or
excessive result counts are rejected. Bodies are streamed with a 1 MiB decoded
limit and results are capped at the request's configured maximum. Partial or
paginated results must not claim `complete: true`.

Graph outcomes are context, not a diagnosis, coverage decision, approval, or
permission to execute a write. Existing authoritative billing/coverage checks and
the future human approval gate remain separate requirements.

## Persistence and failure behavior

Before external I/O, the backend commits a pending query record and executing
step. Metadata includes query/workflow/step/profile/actor IDs, operation, input
fingerprint and timestamps. Finalization records status, duration, safe error code,
source result/revision reference, item count and unresolved-dependency count.
Raw graph items and documents are not copied into PostgreSQL query records.

Successful steps receive `graph-query:<query UUID>` as their workflow result
reference. The authorized POST caller receives the validated result; GET trace
responses remain metadata-only. A process restart retains these references, not
the raw result body. Source result retention belongs to the graph service.

Failures produce persisted failed queries/steps and controlled failed workflow/
case states, never a synthetic successful result. Duplicate/concurrent requests
cannot dispatch the same step twice. A late result cannot reopen a cancelled
workflow. Process crashes or persistence failures can leave pending metadata for
Part 8 reconciliation; it is not treated as completed evidence. No automatic
retry or fallback is introduced.

Error codes: `GRAPH_NOT_CONFIGURED`, `GRAPH_CONFIG_INVALID`, `GRAPH_UNAVAILABLE`
and `GRAPH_CANCELLED` use 503; `GRAPH_TIMEOUT` uses 504; `GRAPH_RESULT_INVALID` and
`GRAPH_INCOMPLETE` use 502. Responses/persisted errors contain no raw remote
exception details. Normal auth/ownership/workflow errors retain their existing
401/403/404/409 behavior.

## Validation

```bash
python -m pip install -r backend/requirements-test.txt
MEDILINK_TEST_DB_DSN='dbname=medilink_part6_test host=localhost user=postgres' \
  python -m pytest tests/backend -q
```

Use an empty disposable database. Coverage includes typed facade responses,
references-only persistence, graph-dependent claims, unresolved dependencies,
malformed/incomplete/mismatched results, configuration/health permissions,
concurrency, cancellation, migration preservation and prior idempotency keys.
Graph transport tests use a synthetic HTTP facade through httpx's mock transport;
they do not claim to test a live NebulaGraph schema, ingestion or graph reasoning.
Previous MCP protocol tests still exercise their stdio/local HTTP test peers.

Validation result: **201 backend tests passed** against an isolated PostgreSQL 18
database. Static checks passed for every changed Python file.
