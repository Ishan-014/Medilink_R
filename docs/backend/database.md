# Database setup and backend operations

## New Compose database

From a fresh clone:

```bash
python -m backend.ops.init_env
docker compose -f compose.backend.yaml up --build --detach --wait
docker compose -f compose.backend.yaml ps --all
```

`db` must be healthy, `migrate` must have exited successfully, and `backend` must
be healthy. The migration service applies the existing eight forward migrations.
No schema is guessed from a hospital dataset and no demo accounts are seeded.
PostgreSQL and private documents use separate persistent named volumes.

For an existing `.env`, keep it and configure missing fields using
[the configuration reference](configuration.md); the initializer will not replace
it. Existing PostgreSQL-volume passwords are not changed by editing `.env`.

## Native PostgreSQL

Install Python 3.12+ and PostgreSQL, then from the repository root:

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# PowerShell alternative: .\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-test.txt
```

Create a local database using a role you control, for example:

```bash
createdb -h localhost -p 5432 -U postgres medilink
```

Configure `.env` with that database and its actual credentials. The secret helper
is convenient for new Compose stacks; its generated DB password does not alter
an already provisioned native PostgreSQL role. Keep the three signing secrets
independent from each other and from the DB password.

```bash
python -m backend.db.migrate --status
python -m backend.db.migrate
python -m uvicorn backend.application:create_app --factory --reload --log-config backend/config/logging.json --no-access-log
```

Migrations take a transaction advisory lock, apply pending files atomically and
record checksums in `schema_migrations`. Re-running is a no-op. An edited/missing
applied migration, incompatible legacy schema or failing SQL is rejected. Add a
new numbered forward migration for future changes; do not edit `0001`–`0008`.
Startup does not silently migrate native databases. Readiness checks connectivity,
while migration status is a separate operation.

## Persistence ownership

| Tables/storage | Responsibility |
| --- | --- |
| `users`, `consumed_auth_stages` | Canonical roles/password hashes and one-use staff stages |
| `workflow_runs`, `workflow_steps`, creation/action keys | Versioned plans/state and durable idempotency reservations |
| `documents`, extraction results, document links | Metadata/extraction lifecycle and source references |
| Private document volume | Raw PDF/TXT/image bytes, separate from database metadata |
| `patient_profiles`, `claims`, `appointments`, workflow cases/evidence | Local application mirrors and selected authoritative facts |
| `tool_calls`, graph query records | Execution status and result references |
| `approvals`, append-only approval events | Scope-bound decisions and single-use authorization |
| `audit_logs`, `system_events`, external reconciliations | Atomic mutation history, errors and receipt-lookup attempts |
| Legacy `patients`/views | Existing hospital/dashboard schema; not provisioned by the backend migrations |

A local patient profile is distinct from a legacy/external EHR patient. Do not
populate external IDs or submitted/active flags directly through SQL to simulate
an integration success. Completion requires a validated authoritative receipt.
Appointment records currently remain drafts; booking mutations are not enabled.

## First accounts

Patients use public registration/login. Public callers cannot select staff roles.
For the first trusted staff account, an operator with DB access runs:

```bash
python -m backend.auth.provision admin@example.com --role admin
# Compose equivalent; prompts require the normal interactive terminal:
docker compose -f compose.backend.yaml exec backend python -m backend.auth.provision admin@example.com --role admin
```

The command prompts for name/password/confirmation, hashes the password and
creates one account. It neither changes an existing account nor prints a JWT.
Staff must still complete credentials, signed ID card and face verification.
QR provisioning/enrollment use the existing auth tooling and optional biometric
runtime. The minimal Compose image does not include the heavy biometric/legacy
AI dependencies or enrollment data, so provisioning alone does not make a staff
login demonstration complete. The synthetic rehearsal uses test-only fixtures,
not an authentication shortcut in the service.

## Restart, upgrades and backups

`docker compose -f compose.backend.yaml down` preserves both application volumes.
To update code, back up the database and private source files together, review new
forward SQL, then rebuild/start so the migration gate can run. Do not use
`down --volumes` to restart an application stack. That destructive option is used
only by the isolated smoke command for volumes it generated itself.

For a native database, a DB operator can save a custom-format dump outside the
repository (replace role/database appropriately):

```bash
mkdir -p ../medilink-backups
pg_dump -h localhost -U postgres -Fc -f ../medilink-backups/database.dump medilink
```

Arrange a matching private-document backup during a controlled write pause.
Restore into a new database and private storage location first, verify migration
status/record references/content authorization, then rehearse the intended switch.
Do not restore test data over an application database. Database-only backup is
insufficient for uploaded sources. Backup/restore of real deployment data remains
an operator procedure, not an automatic feature or a compliance claim.

During an MCP timeout/crash, pending/uncertain writes remain reserved. An admin
uses the receipt lookup contract from [Part 8](part-8.md) to reconcile the original
operation. A missing receipt is not proof it was never applied. Never clear a key,
reset a consumed approval or blindly resubmit a claim to recover it.

Liveness is independent of PostgreSQL; readiness returns 503 during DB outage.
External MCP/graph/AI health is separate. Atomic audit history and metadata-only
operational logs help correlate failures without exposing tokens or raw documents.
