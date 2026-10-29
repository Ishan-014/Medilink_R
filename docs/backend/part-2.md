# Backend Part 2: authentication, RBAC and workflow persistence

Based on `backend-part-1`. This completes Person 1's Part 2 backend work without
editing MCP servers, agents or frontend code. It does not implement the other
owners' Part 2 tasks or the roadmap's cross-team UI/agent checkpoint.

## Upgrade

From the repository root, activate your Python environment, then:

```bash
python -m pip install -r backend/requirements-foundation.txt
python -m backend.db.migrate --status
python -m backend.db.migrate
python -m uvicorn backend.application:create_app --factory --reload
```

Set `MEDILINK_JWT_SECRET` and `MEDILINK_CARD_SECRET` to separate random secrets of
at least 32 characters. Placeholder/development signing keys are rejected. Generate
each independently with `python -c "import secrets; print(secrets.token_urlsafe(48))"`
and keep them in your local `.env`. `MEDILINK_ACCESS_TOKEN_MINUTES` defaults to 60
and accepts 1–480. No secret is included in an API response or settings repr.

`backend.main:app` remains the full legacy staff/dashboard/chat application and
includes the new APIs. Its existing AI and optional camera/face dependencies still
need installation. The factory starts the auth/workflow APIs without loading an
LLM, OpenCV, or DeepFace. Camera decoding and real face verification load those
optional dependencies only when those endpoints are called.

Existing access tokens must be replaced by logging in again: access tokens now
require issuer/audience, expiry, issuance time, identity, token type and auth level.
If replacing an old development card secret, reissue your staff QR cards too.

## Database compatibility

Migration `0001` is unchanged. New migration `0002`:

- Creates `public.users` on fresh databases using generated numeric user IDs.
- Retains existing users, user IDs, password hashes and other columns.
- Requires the six canonical existing user columns and a text/varchar role.
- Expands role-only CHECK constraints to the five supported roles. Combined checks
  involving other fields remain in place. Unsupported legacy roles fail migration.
- Adds case-insensitive email uniqueness. Existing case-only duplicates fail rather
  than being merged or removed.
- Adds workflow versions, creation/action idempotency records and consumed login
  stage digests. Workflow owners remain text IDs to support existing numeric/UUID
  identity stores; authorization resolves them through the canonical users table.

An existing users table must have an automatically generated/defaulted `user_id`
for registration, and must allow inserts with `full_name`, `email`, `password_hash`,
`role`, `is_active`. Custom additional NOT NULL columns without defaults or combined
role restrictions need an explicit compatibility migration by the schema owner.
No clinical tables or patient data are changed or seeded by this migration.

## Accounts and login

Public `POST /api/auth/register` creates **patients only**. It rejects submitted
roles and other unknown fields. Passwords are Argon2 hashes; new passwords require
12–1024 characters and their whitespace is preserved. Existing shorter passwords
can still be verified. Emails are normalized for registration and login.

Patient login: `POST /api/auth/login` with `email` and `password` returns the usual
API envelope containing `access_token`, `token_type`, `expires_in` and `user`.

All four staff roles (`doctor`, `receptionist`, `admin`, `auditor`) must complete:

1. `POST /auth/login/credentials`: form `username`, `password` → stage 2 token.
2. `POST /auth/login/id-card`: form `stage2_token`, `qr_string` → stage 3 token.
3. `POST /auth/login/face`: multipart `stage3_token`, `frame` → access token.

These existing staff routes retain their existing response shapes for the current
frontend. Stage tokens cannot access protected APIs. Successful stage 2/3 tokens
are one-use: their hashes are persisted to reject replay. Camera frames are bounded
to 5 MB. Failed verification does not issue an access token. Existing QR and face
verification remain the source of those authentication checks.

The old `POST /login` now uses the patient password login service and rejects staff
password-only login. Every protected legacy/new route resolves active users from
the database. Deactivating a user takes effect on the next request; stale JWT role
claims cannot retain old permissions. Patients promoted to a staff role must
complete the staff authentication stages.

`GET /api/me` returns the current identity in the shared envelope. Existing `/me`
retains its legacy shape, including the role/auth level used by the existing UI.

### Controlled provisioning

Bootstrap the first administrator from a trusted terminal with database access:

```bash
python -m backend.auth.provision admin@example.com --role admin
```

The command prompts for a name and password/confirmation; passwords are not CLI
arguments. The same CLI supports all five roles. Existing signed staff-card and
face enrollment must then be completed for staff accounts. To create their card:

```bash
python scripts/provision_staff.py admin@example.com
```

Face enrollment uses the existing `backend.auth.facial_recognition.enroll_face`
function with the created user ID and an authorized reference photo. The API does
not supply an authentication bypass for bootstrap administrators.

An authenticated administrator may also `POST /api/admin/users` with `full_name`,
`email`, `password`, and `role`. Other roles receive 403. There is no public staff
registration, role elevation, or admin-specific frontend in this change.

## Workflow APIs

All routes require an access token and return the shared success/error envelope.
UUIDs, workflow types, statuses, bounds and unknown fields are validated.

| API | Behavior |
| --- | --- |
| POST `/api/workflows` | Create a `CREATED` workflow for the authenticated owner |
| GET `/api/workflows` | Filter by `status`/`workflow_type`; `limit` 1–100, `offset` ≥ 0 |
| GET `/api/workflows/{id}` | Read state, version, timestamps and ordered plan steps |
| PUT `/api/workflows/{id}/plan` | Authorized staff supplies an ordered plan during `PLANNING` |
| POST `/api/workflows/{id}/transitions` | Authorized staff requests a valid state change |
| POST `/api/workflows/{id}/cancel` | Owner or authorized staff cancels a nonterminal workflow |

Patients can create/read/list/cancel their own workflows. Another patient's
workflow returns 404. Doctor, receptionist and admin roles can manage workflows
across this single-facility prototype; auditors can read/list all workflows but
cannot create or mutate them. Staff assignment/consent scoping remains future work.

Transition example:

```json
{"expected_version":0,"status":"PLANNING"}
```

Plan example (after that transition):

```json
{"expected_version":1,"steps":[{"action":"validate_patient"},{"action":"create_record"}]}
```

Every plan/state update locks its workflow row, verifies the supplied version and
increments it. Concurrent/stale writes return 409 without modifying the workflow.
The server derives step positions; a plan with previously executed steps cannot
be replaced. The response's new `version` must be used for the next mutation.

Allowed transitions:

| From | To |
| --- | --- |
| CREATED | PLANNING, CANCELLED |
| PLANNING | WAITING_FOR_DATA, EXECUTING, FAILED, CANCELLED |
| WAITING_FOR_DATA | PLANNING, FAILED, CANCELLED |
| EXECUTING | WAITING_FOR_DATA, WAITING_FOR_APPROVAL, COMPLETED, FAILED, CANCELLED |
| WAITING_FOR_APPROVAL | CANCELLED only until the Part 7 approval service exists |
| COMPLETED / FAILED / REJECTED / CANCELLED | No outgoing transitions |

Execution requires a nonempty plan. Completion requires every stored step to have
a completed status and confirmed result reference. No public endpoint lets callers
set step results, risk or approval fields. This part stores and validates workflow
state; it does **not** execute tools or pretend that actions have completed.
Document references are rejected until Part 3 can verify their ownership.

## Idempotency

An optional `Idempotency-Key` header on workflow creation accepts 1–128 characters
from letters, digits, `.`, `_`, `:`, `-`. The same owner/key/body returns the existing
workflow (200 instead of 201). Reusing the key for a different body returns 409.
Other owners have separate key scopes. Reservations and workflow creation commit
atomically, including during concurrent requests.

`backend.services.idempotency` provides the backend boundary for selected future
external writes. An authorized executor must:

1. Check resource permissions and any required approval.
2. Call `reserve_action` with a stable workflow/action/key/payload.
3. Execute externally **only** if `should_execute` is true; the reservation has
   already committed before the function returns.
4. Record confirmed success with a result reference, definite failure, or uncertainty.

Duplicate calls never grant another execution, including pending/failed/uncertain
reservations. Conflicting payloads return 409. Pending or uncertain operations need
reconciliation; a crash after reservation is intentionally not retried automatically.
This is a durable duplicate-prevention foundation, not a claim of distributed
exactly-once execution. Existing agent writes are not connected to this service;
integration belongs to later MCP/backend adapter work.

## Legacy role corrections

Receptionist patient details now use the same administrative database view as the
receptionist list, rather than returning full clinical records. The existing chat
agent exposes clinical read/write tools without receiving caller permissions, so
the backend `/chat` route is restricted to doctors until that integration becomes
role-aware. Receptionist clinical chat requests receive 403; no frontend/agent/MCP
code is changed. Upload persistence and document authorization are Part 3 work.

## Validation

```bash
python -m pip install -r backend/requirements-test.txt
MEDILINK_TEST_DB_DSN='dbname=medilink_part2_test host=localhost user=postgres' \
  python -m pytest tests/backend -q
```

Use an empty disposable test database only. API fixtures refuse existing public
tables, create the migrations, then remove only their known test tables. Migration
fixtures roll back their DDL. Do not run these database tests concurrently with
another suite on the same database.

The suite covers patient registration/password hashing, all five roles, privilege
escalation, token expiry/type/audience, disabled users, live role changes, staff
stage replay, legacy HTTP compatibility, workflow ownership and transitions,
optimistic concurrency, idempotent creation and durable action reservations.
Biometric matching is replaced by a synthetic verifier in HTTP tests; real camera/
face accuracy and the unchanged AI pipeline are not exercised.

Validation result: **65 backend tests passed** against an isolated PostgreSQL 18
database. Ruff's undefined-name/unused-import checks passed for all changed Python
files. The pre-existing AI/MCP test file is outside this backend-only suite.
