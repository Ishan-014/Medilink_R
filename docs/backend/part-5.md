# Backend Part 5: onboarding, claim preparation and domain persistence

Based on `backend-part-4`. This implements Person 1's business workflow services,
start/status APIs, domain records, missing-evidence stops and confirmation handling.
MCP servers, AI agents and frontend code are unchanged. Deterministic backend
recipes coordinate the authorized Part 4 adapter; they are not LangGraph agents.

New patient creation and insurance submission remain blocked by the Part 4 write
gate until Part 7 supplies authoritative approvals. This part can fully confirm
onboarding by linking an existing matching EHR record and can prepare a claim for
approval. It does not claim new EHR creation, real insurance settlement or confirmed
appointment booking before those external operations succeed.

## Upgrade

Activate the environment and run from the repository root:

```bash
python -m pip install -r backend/requirements-foundation.txt
python -m backend.db.migrate --status
python -m backend.db.migrate
python -m uvicorn backend.application:create_app --factory --reload
```

Configure the EHR, billing and insurance MCP connections as described in
[Part 4](part-4.md). If a required simulator is absent, calls return a controlled
error rather than substituting fabricated records. App startup does not contact
these systems. `backend.main:app` also includes the new APIs.

Migration `0005` adds:

- `patient_profiles`: the local application registry with draft/active state,
  external EHR ID and authoritative confirmation-call reference.
- `claims`: requested amount/currency/policy/procedure, preparation state and
  confirmation references for eventual submission.
- `appointments`: persistent requested appointment drafts, explicitly not bookings.
- `workflow_cases`: structured intake, subject identity, phase and missing fields.
- `workflow_case_evidence`: selected typed facts and their successful tool-call IDs.
- Managed-kind and subject-user references on `workflow_runs`.

The existing/externally owned `patients` EHR table is neither changed nor guessed.
The local patient profile UUID is distinct from `external_patient_id`. Existing
dashboards continue using their current EHR APIs; they are not rewritten to use
this registry. Earlier migrations and existing workflows remain intact.

## API catalog

JSON uses the existing `{data, request_id}` envelope. Sensitive resource responses
carry `Cache-Control: private, no-store`. Start endpoints accept an optional
`Idempotency-Key`: the same caller/key/body returns the original case with 200;
a new case returns 201; conflicting reuse returns 409. Intake, workflow creation,
document links and domain records commit in one transaction.

| Method/path | Purpose |
| --- | --- |
| POST `/api/onboarding` | Start a managed onboarding workflow and draft profile |
| PATCH `/api/onboarding/{workflow_id}` | Supply/correct intake while waiting for data |
| POST `/api/claims` | Start claim preparation for a confirmed patient profile |
| PATCH `/api/claims/{workflow_id}/intake` | Supply/correct claim intake while waiting for data |
| GET `/api/workflows/{workflow_id}/case` | Workflow, phase, missing fields, entity UUID and next action |
| POST `/api/workflows/{workflow_id}/advance` | Doctor/admin executes exactly one ready recipe step |
| GET `/api/workflows/{workflow_id}/evidence` | Authorized subject/staff reads selected facts and source-call IDs |
| GET `/api/patients/{patient_profile_id}` | Authorized local patient profile |
| GET `/api/claims/{claim_id}` | Authorized claim record; claim UUID differs from workflow UUID |
| POST `/api/appointments` | Create an appointment draft for a confirmed profile |
| GET `/api/appointments/{appointment_id}` | Read an authorized appointment draft |

All mutation/resume requests use `expected_version` from the latest workflow.
Starting a case atomically plans its recipe and publishes `EXECUTING` or
`WAITING_FOR_DATA`. Advancing performs one operation, not an unbounded loop.
Call the status endpoint and use the new version before the next step.

Generic plan/status endpoints cannot replace or complete managed recipes. The
generic tool-call endpoint remains usable by authorized staff, but its operation
and arguments must exactly match the persisted case and the ordered plan step.
Client-supplied IDs, amounts or policies cannot redirect a step to another subject.

## Onboarding

Patient example:

```json
{
  "full_name": "Synthetic Patient",
  "date_of_birth": "2000-01-01",
  "phone": "+919876543210",
  "document_ids": []
}
```

The patient account's email is derived server-side. Staff may specify
`patient_user_id` for an existing active patient account; patients cannot start
another patient's onboarding. Name, birth date and phone are required before
external reads. Incomplete intake is persisted with explicit missing-field names.
Referenced documents must belong to an authorized resource and finish extraction.
Missing fields/documents cause a pause, not guessed values.

The first step is `ehr.search_patients` with the derived email:

- Exactly one matching email/name/birth-date record confirms the link. The draft
  profile becomes active with its EHR ID and successful source-call reference.
- No matching record appends `ehr.create_patient` and pauses for approval. The
  profile remains draft; the new patient is not falsely marked created.
- Ambiguous, malformed, conflicting, or potentially truncated (100-result) search
  evidence pauses for review. An existing EHR ID cannot be linked to two local
  profiles. One subject cannot start multiple concurrent onboarding cases.

Correcting intake uses the version and only supplied fields:

```json
{"expected_version":0,"full_name":"Synthetic Patient","date_of_birth":"2000-01-01","phone":"+919876543210"}
```

The application performs deterministic matching on synthetic prototype identities;
this is not production identity verification or KYC. It never infers a diagnosis,
changes treatment, or merges ambiguous patient records automatically.

## Claim preparation

Start with an active local patient profile:

```json
{
  "patient_profile_id": "replace-with-confirmed-local-profile-uuid",
  "policy_id": "POL-SYNTHETIC",
  "procedure_code": "PROC-SYNTHETIC",
  "amount": "125.50",
  "currency": "INR"
}
```

Amounts are positive Decimal values with at most two fractional digits and 12 total
digits; no floating-point settlement arithmetic is introduced. Missing policy,
procedure or amount pauses the workflow. The fixed read sequence is:

1. EHR patient retrieval.
2. Procedure charge retrieval from billing.
3. Authoritative claim amount validation from billing.
4. Policy retrieval from insurance.
5. Authoritative coverage validation from insurance.

Each response is schema-checked by the MCP adapter, then checked against the case's
patient/policy/procedure/currency/amount bindings. A different patient's result or
a conflicting amount fails the operation and cannot prepare the claim. Missing
facts, invalid amount, inactive policy or lack of coverage pause for evidence.

Only all five valid source receipts produce `claims.status = prepared` and
`WAITING_FOR_APPROVAL`. The final planned step is `insurance.submit_claim`.
`prepared` means evidence collected, not insurer acceptance or money transferred.
No public API accepts a submitted status, external claim ID or confirmation ID.

### Backend wire contracts for the integration owner

MCP schema wrappers consisting solely of `{ "result": ... }` are supported.
Other output shapes must be aligned explicitly, not inferred from free-form text.

| Operation | Canonical input | Selected required output facts |
| --- | --- | --- |
| ehr.search_patients | query = subject account email | Array of id/patient_id, name/full_name, birth date, email |
| ehr.get_patient | patient_id | Same identity facts; must match the confirmed profile |
| billing.get_procedure_charge | procedure_code | procedure_code, amount, currency |
| billing.validate_claim_amount | patient_id, procedure_code, amount, currency | Same bindings and strict boolean valid |
| insurance.get_policy | patient_id, policy_id | Same IDs, currency, strict boolean active |
| insurance.validate_coverage | patient_id, policy_id, procedure_code, amount, currency | Same bindings and strict boolean covered |

Currency/amount/identity facts must match intake. The billing validation tool remains
authoritative about amount validity; the backend does not invent payer rules.
Only selected typed facts are retained. Full clinical notes and arbitrary tool
payloads are not copied into the domain evidence table.

## Confirmed writes and workflow integrity

Trusted finalization hooks are ready for later approved execution:

- A successful `ehr.create_patient` receipt with matching identity can activate the
  profile and complete onboarding.
- A successful `insurance.submit_claim` receipt must include matching bindings,
  `covered: true`, `status: submitted` and a nonempty `claim_id`. Only then are the
  submitted state and external ID committed together with its call reference.

These hooks run inside the Part 4 tool-call finalization transaction. They are not
HTTP approval/confirmation endpoints or a way around the write gate. Part 7 must
authorize actual writes; the simulator owner must implement those capabilities.
Until then, new patient creation and actual claim submission remain paused.

Malformed or conflicting business evidence rolls back the attempted success and
records a controlled failure. Timeouts/uncertain operations never activate a
patient or submit a claim. A late response cannot reopen a cancelled case.

Intake correction is allowed only in `WAITING_FOR_DATA`, with a matching version
and no failed/pending/uncertain or mutating attempts requiring reconciliation.
It clears current selected evidence and explicitly restarts the safe read steps.
Historical tool-call records remain intact. This is a controlled read-only retry
after correction, not an automatic retry of an uncertain write.

## Roles and appointment persistence

Patients can start/read/correct their own cases. Doctor/admin roles can operate
onboarding and claims across this single-facility prototype. Receptionists can
start/correct onboarding, inspect demographic profiles and create/read appointment
drafts, but cannot execute arbitrary clinical MCP calls or read claim financial
records. Auditors may inspect workflow status and trace metadata, not private
profiles, claim facts or domain mutations. A subject patient may read the workflow
even when authorized staff started it; another patient's resources return 404.

Appointment creation requires an active profile and a future timezone-aware
`starts_at`, `doctor_reference`, and duration of 5–240 minutes. Timestamps normalize
to UTC. Records remain `draft`; doctor availability and slot booking are not
fabricated. No client can set `booked`. Scheduling coordination/confirmation is a
separate workflow and integration task.

## Validation

```bash
python -m pip install -r backend/requirements-test.txt
MEDILINK_TEST_DB_DSN='dbname=medilink_part5_test host=localhost user=postgres' \
  python -m pytest tests/backend -q
```

Use an empty disposable database. Coverage includes onboarding matches/duplicates,
missing intake/documents, claim evidence and negative coverage, mismatched receipts,
idempotent/concurrent starts and advancement, corrections with retained trace,
cancellation races, role isolation, appointment draft persistence and migration
preservation of legacy EHR data. Trusted future-write receipt tests simulate the
executor callback directly while independently verifying that the HTTP write gate
remains closed; they do not claim to exercise live approved writes. Existing MCP
protocol tests continue using separate synthetic test peers.

Validation result: **168 backend tests passed** against an isolated PostgreSQL 18
database. Static checks passed for all changed Python files.
