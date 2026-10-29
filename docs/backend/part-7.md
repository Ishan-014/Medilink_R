# Backend Part 7: risk policy and human approvals

Based on `backend-part-6`. This implements the backend/security responsibilities
of roadmap Part 7: authoritative risk classification, persisted approval records,
approve/reject endpoints and enforced execution gates. Frontend, AI agents,
MCP implementations and graph implementation are unchanged.

## Setup

Use the existing foundation requirements, then apply the forward migration:

```bash
python -m pip install -r backend/requirements-foundation.txt
python -m backend.db.migrate --status
python -m backend.db.migrate
python -m uvicorn backend.application:create_app --factory --reload
```

Set these in your local `.env`:

```env
MEDILINK_MEDIUM_RISK_REVIEW=required
MEDILINK_APPROVAL_TTL_MINUTES=60
MEDILINK_ACTION_SIGNING_SECRET=your-separate-random-secret-of-at-least-32-characters
```

Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
Use a separate secret from `MEDILINK_JWT_SECRET`; never commit either secret.
The default requires human review for medium-risk writes. The `automatic` option
permits medium-risk managed actions with an explicit, audited system decision.
High-risk actions always require human review. TTL must be 1–1440 minutes.
Missing signing configuration prevents write reservation/dispatch without
consuming an approval; unrelated reads remain available.

Migration `0007` adds approvals, append-only approval events, write-reservation
links and risk fields. Existing data and migrations are retained. Historical
traces are classified without inventing retrospective approvals. Existing Part 5/6
cases already waiting for review can create their first approval through the
request endpoint below; newly prepared cases enqueue approvals automatically.

## Policy and authorization

| Risk | MVP action categories | Execution |
| --- | --- | --- |
| Low | Allowlisted EHR/lab/scheduling/insurance/billing reads; graph queries | Automatic after existing role, scope, step and document checks |
| Medium | Patient creation; appointment booking/cancellation | Required review by default, configurable automatic review |
| High | Claim submission; clinical record/condition/medication/allergy updates | Independent human review required |

Only the existing managed `ehr.create_patient` and `insurance.submit_claim`
recipes are enabled for writes. Scheduling mutations and clinical updates stay
blocked until they have a managed recipe with authoritative arguments/evidence.
An approval never grants generic arbitrary tool execution. Legacy direct clinical
write service functions also refuse writes, closing the chat/service bypass.
Existing patient reads remain available.

Server policy determines risk at dispatch; caller-proposed step risk and tool
annotations cannot lower it. Patient creation can be reviewed by a doctor or
administrator; claim submission requires an administrator. The human reviewer
must differ from the requester, workflow owner and patient subject. Execution
continues through an authenticated doctor/admin request after approval. Every
request, decision, invalidation, automatic decision and consumption records an
actor, timestamp and reason. Auditors can inspect workflow approval metadata.

Approvals bind the workflow, patient subject, entity, exact step/action/arguments,
policy version/configuration, successful current MCP evidence, attached document
hash/extraction versions and required graph query references/revisions. Changed
scope, expiry or reviewer inactivity/role changes deny execution. Re-requesting
review invalidates a stale open approval and creates a new record; it never edits
the old decision. No caller can set approval status, risk or receipt IDs.

## API

Existing success/error envelopes and authentication apply. Approval and decision
responses carry `Cache-Control: private, no-store`.

| Method/path | Behavior |
| --- | --- |
| GET `/api/risk-policy` | Authenticated policy/version and enabled write categories |
| GET `/api/approvals?limit=50&offset=0` | Doctor/admin pending queue filtered by reviewer role |
| GET `/api/workflows/{id}/approvals` | Authorized workflow readers; approval decision metadata/history |
| POST `/api/workflows/{id}/approvals` | Request/refresh approval for the canonical next managed action |
| GET `/api/workflows/{id}/approvals/{approval_id}/review` | Authorized reviewer; canonical action arguments, scope-match flag and event history while action remains reviewable |
| POST `/api/workflows/{id}/approve` | Independent authorized human decision; resumes workflow |
| POST `/api/workflows/{id}/reject` | Independent authorized human decision; terminates workflow/domain case as rejected |

Request/refresh body:

```json
{"expected_version": 4, "reason": "Request review of prepared action"}
```

Approve/reject body (use the actual current version and approval UUID):

```json
{
  "expected_version": 4,
  "approval_id": "00000000-0000-0000-0000-000000000001",
  "reason": "Verified patient, policy, procedure and amount evidence"
}
```

After approval, fetch the current case/version and call the existing
`POST /api/workflows/{id}/advance`. Approval itself does not make a hospital call.
An approved claim remains `prepared` until a validated submission receipt arrives.
Rejected cases have no next action; rejection cannot create a patient or submit a
claim. Unauthorized or conflicting decisions roll back without state changes.

## Single-use dispatch and MCP handoff

Before external I/O, one transaction locks the workflow, validates approval and
canonical evidence, creates a stable action reservation and pending tool-call,
consumes approval, records the consumption event and starts the step. Concurrent
or repeated decisions/executions cannot dispatch the same action twice. Execution
metadata records risk, approval ID and reservation ID without raw arguments,
results or signed tokens. Valid external receipts alone activate a patient or
mark a claim submitted, atomically with the successful trace.

The backend sends SDK tool-call `_meta.medilink` containing `approval_token` and
`idempotency_key`. The HS256 action token uses issuer `medilink-actions`, audience
`medilink-mcp`, token type `approved_action`, actor `sub`, call-ID `jti`, workflow,
step and approval IDs, operation, argument digest, stable idempotency key and
short expiry (tool timeout + 60 seconds). The digest is SHA-256 of the backend's
canonical `checked_json` argument bytes. No access JWT is reused for tool writes.

**MCP-owner integration remains required:** the server must verify the signature,
issuer/audience/type/expiry, exact operation and argument digest, then durably
deduplicate the idempotency key before mutating its system. This backend change
sends that context but does not implement verification in MCP servers. Existing
servers may not expose these write capabilities yet. Tests use synthetic MCP
peers; live hospital simulator integration is not claimed.

Once a write has been dispatched, timeout, malformed/mismatched receipt, remote
failure or disconnection records `uncertain`, retains the consumed approval and
blocks automatic retry or another unresolved write of the same category for the
patient. Pre-dispatch adapter/schema failures record `failed`, also without reuse
of the consumed approval/reservation. A process crash can leave `pending`; pending
writes also block another attempt. Recovery/reconciliation belongs to Part 8.
Cancellation invalidates unconsumed approvals; cancellation during a pending
external write is denied so its eventual receipt can still be recorded.

## Verification

Run only against an empty disposable PostgreSQL database:

```bash
MEDILINK_TEST_DB_DSN='dbname=medilink_test host=localhost user=postgres password=your-test-password' python -m pytest tests/backend -q
```

Tests cover approved creation/submission, negative decisions, role/self-approval
checks, atomic concurrency, canonical arguments, stale scopes/documents/policy,
expiry, reviewer deactivation, automatic medium review, mandatory high review,
signed SDK metadata, append-only events, uncertain outcomes, cancellation and
legacy clinical-write bypass prevention, alongside previous backend regressions.
