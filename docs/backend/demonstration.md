# Backend demonstration and team rehearsal

Use synthetic data only. This guide separates a runnable backend API demonstration
from the full browser/agent/simulator/graph demonstration the team will integrate.

## Fresh backend API demonstration

Start the stack using [the delivery quickstart](README.md). Open `/docs`:

1. Register a patient through `POST /api/auth/register` with a synthetic name,
   an example.com email and a password of at least 12 characters.
2. Log in through `POST /api/auth/login`. Copy the returned access token into
   Swagger's **Authorize** bearer field. Keep it local; do not paste it into
   shared screenshots or commit it.
3. Call `/api/me`; create a workflow with `/api/workflows` and fetch it by ID.
   The server assigns ownership, version and initial state.
4. Upload a small synthetic TXT/PDF/image through `/api/documents`. Fetch its
   metadata and authorized content. Extraction starts pending until a worker
   claims and finishes it through the internal extraction contract.
5. Create `/api/onboarding` with incomplete intake; inspect the managed case and
   missing fields. Supply valid intake through its correction endpoint. Execution
   still requires clinical staff and configured hospital capabilities.
6. Fetch the patient's workflow trace. Another patient cannot read this workflow
   or private source content.

This minimal stack does not auto-provision a staff user, bypass staff stages or
invent an external EHR success. Staff face runtime/enrollment and the selected
integration services must be supplied before the corresponding live steps can run.

## Deterministic backend rehearsal

Install `backend/requirements-test.txt`. Supply an empty disposable PostgreSQL DB,
then run from the repository root:

```bash
export MEDILINK_TEST_DB_DSN='dbname=medilink_test host=localhost user=postgres password=your-test-password'
python -m backend.ops.rehearse --output artifacts/backend-rehearsal.json
```

PowerShell uses `$env:MEDILINK_TEST_DB_DSN = '...'`. The command requires explicit
DB opt-in and rejects incomplete/skipped scenario coverage. It executes selected
existing backend tests for AT-01–AT-12 and records counts and test-node references,
without copying credentials or result payloads into its report. Tests use their
existing synthetic fixtures and refuse an application database with public tables.

The rehearsal covers:

| Stage | Evidence demonstrated |
| --- | --- |
| Onboarding | Persisted steps and authoritative matched-patient completion |
| Documents | Extraction lifecycle, stored fields and validation states |
| Two MCP capabilities | Real SDK stdio handshake/discovery and synthetic EHR/lab calls |
| Graph-dependent claim | All required typed relationship/dependency/history reads |
| Risk | Authorized automatic low-risk retrieval and mandatory high-risk pause |
| Approval | Independent approval resumes; rejection never calls a write |
| Failure/retry | Timeout remains unconfirmed; reconciliation does not replay writes |
| Auditor/security | Complete scoped trace and denied unauthorized approvals |

Staff identities and signed context in this run are test fixtures. They are not
created by a public API bypass. Graph, hospital writes and receipt lookup use
synthetic peers; real project MCP/agent/graph implementations remain unchanged.
The actual receiving MCP owner must verify action context and persist durable
idempotency/receipt evidence before the live write demonstration.

## Cross-team integrated demonstration checklist

Use [the acceptance matrix](part-9-acceptance.md) with each owner:

- Frontend: patient/staff/admin/auditor flows, current API shapes, bearer handling,
  optimistic-version conflicts, approval controls, failures and trace pagination.
- AI: intent/planning/tool choice, extraction worker results, bounded execution,
  safe alternative paths and backend-enforced risk; no autonomous self-approval.
- MCP: stable schemas, receiving-side context verification, durable deduplication,
  failure injection and the action-receipt lookup facade.
- Graph: reachable facade, patient/operation/revision binding and required graph
  reasoning evidence from the selected implementation.
- Backend: current DB/schema/configuration, real staff enrollment/login,
  configured boundaries, independent review, failure/recovery and audit trace.

Then demonstrate one multimodal flow, two real selected MCP capabilities, a
required graph scenario, approval/rejection, uncertain-write recovery and the
browser's auditor trace. Record owner results and limitations. Only after that
whole-team checkpoint should an integrated release tag be created.
