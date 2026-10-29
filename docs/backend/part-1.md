# Backend Part 1: foundation and contracts

This change implements Person 1's Part 1 responsibilities. It adds configuration,
an application factory, health endpoints, shared API schemas, and PostgreSQL
migrations for the initial workflow tables. It does not implement the workflow
engine, approvals, document persistence, audit persistence, or new authentication.
MCP servers, agents, and frontend files are unchanged.

## Setup and run

From the repository root, using Python 3.11 or newer:

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell instead: .\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-test.txt
```

Copy `.env.example` to `.env` and configure your local PostgreSQL database. Existing
environment variables override `.env`. Database connection and statement timeouts
are configurable; passwords are excluded from the settings representation.

Run only the new foundation, without AI/MCP/biometric dependencies:

```bash
python -m uvicorn backend.application:create_app --factory --reload
```

The existing full application remains `backend.main:app`. It includes the same
foundation endpoints and uses the shared connection configuration for its direct
database operations. Its existing authentication, AI, and biometric dependencies
are still required; the foundation requirements file is deliberately not a full
legacy application dependency manifest.

- `GET /api/health`: process liveness, no external connections.
- `GET /api/health/ready`: executes `SELECT 1`; returns 503 if PostgreSQL is unavailable.
- `/docs` and `/openapi.json`: implemented endpoint documentation.

Readiness tests connectivity only, not migration version, EHR data availability,
graph availability, or full workflow readiness. No migrations run on app startup.

## Migrations

Create the PostgreSQL database first (for example, `createdb medilink` using your
local PostgreSQL credentials), then run:

```bash
python -m backend.db.migrate --status
python -m backend.db.migrate
python -m backend.db.migrate --status
```

The versioned SQL runner records checksums in `public.schema_migrations`, obtains
a transaction advisory lock, and applies pending migrations as one transaction.
Repeating the command does not reapply migrations. Missing/edited applied files,
duplicate versions, and out-of-order additions are rejected. SQL failure rolls
back the entire batch. Add a new numbered SQL file for each future change; do not
edit an applied migration. Downgrades are intentionally unsupported: use a reviewed
forward migration or restore a backup. No credentials or patient data are printed.

Migration `0001` creates `workflow_runs` and `workflow_steps`, including status/type/
risk constraints, a step-to-workflow foreign key, unique step positions, and query
indexes. IDs are application-generated UUIDs. Owner IDs are text to accommodate
the existing identity store; authenticated owner assignment and validation arrive
in Part 2. Updating `updated_at` and enforcing transitions also belong to Part 2.

Existing `users`, `patients`, and `receptionist_patient_view` are **not altered or
bootstrapped**. A fresh database supports the Part 1 health/migration foundation;
the legacy dashboards still need their existing schema and seeded EHR dataset.
This avoids guessing or overwriting an undocumented existing hospital schema.
New workflow tables must not already exist under unmanaged definitions; the
migration fails rather than silently adopting incompatible tables.

## API contracts

See [api-contracts.md](api-contracts.md) and `backend/api/schemas.py`. New `/api`
routes use one success/error envelope and a generated `X-Request-ID`. Existing
routes retain their response shapes, including `detail` errors, so their clients
continue to work. The contract schemas are not new executable business endpoints.

## Verification

```bash
python -m pytest tests/backend -q
```

Database integration tests skip unless `MEDILINK_TEST_DB_DSN` points to a
**disposable, empty** PostgreSQL database. They refuse to run if public tables
already exist and roll back all test DDL/data. Never point them at an application
or production database.

```bash
MEDILINK_TEST_DB_DSN='dbname=medilink_part1_test host=localhost user=postgres' \
  python -m pytest tests/backend -q
```

Coverage includes health availability/failure, error sanitization, legacy response
compatibility, configuration, input contracts, transaction cleanup, migration
repeatability/checksums/rollback, workflow constraints, and preserving existing
user/patient tables. The older `tests/test_mcp_patient_tools.py` suite is outside
this change and still imports removed agent functions.

Part 1 validation: 26 tests passed against an isolated PostgreSQL 18 database,
including the real readiness endpoint. The existing complete staff/AI application
was not exercised end-to-end; it requires its separate dependencies and EHR setup.

## Handoff

Part 2 should implement auth/RBAC and persisted workflow transitions using these
contracts. Other team members must review the shared API and workflow contracts
before integrating; this backend change cannot itself certify their agreement or
the roadmap's cross-team MCP/NebulaGraph/frontend checkpoint.
