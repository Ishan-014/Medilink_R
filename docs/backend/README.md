# MediLink backend delivery

This is the entry point for the backend delivered in parts 1–10. It provides the
authoritative identity, workflow, document, approval, audit and recovery layer.
Frontend, AI-agent, hospital MCP and graph implementations connect through the
published contracts and remain with their owners.

## Start a fresh backend with Docker

Requirements: Git, Python 3.12+ for the setup utility, and running Docker Compose.
From a new clone of `backend-part-10`:

```bash
git clone --branch backend-part-10 https://github.com/Ishan-014/Medilink_R.git medilink-backend
cd medilink-backend
python -m backend.ops.init_env
docker compose -f compose.backend.yaml config --quiet
docker compose -f compose.backend.yaml up --build --detach --wait
```

The initializer creates a private `.env` with independent random database,
access-token, QR-card and action-signing secrets. It refuses an existing file.
It is for a NEW Compose database; native PostgreSQL credentials must be configured
separately. If port 8000 is occupied, use `--api-port 18310` during initialization.
Existing environment variables override `.env`; review them when switching setups.

- Liveness: `http://127.0.0.1:8000/api/health`.
- Database readiness: `http://127.0.0.1:8000/api/health/ready`.
- Interactive API: `http://127.0.0.1:8000/docs`.
- Live contract: `http://127.0.0.1:8000/openapi.json`.

Use the selected port if changed. Compose waits for PostgreSQL and successful
migrations before starting the backend. The image runs the backend-only
`backend.application:create_app` factory, not the legacy chat/dashboard entry
point. It starts without an LLM, graph or MCP service. Unconfigured integrations
return controlled errors when requested. Staff face authentication still needs the
optional biometric runtime and enrollment; no shortcut staff token is provided.

No demo accounts are auto-created. Patients register with `/api/auth/register`
and log in with `/api/auth/login`. Bootstrap staff accounts using the trusted
provisioning command in [database setup](database.md), then complete their normal
three-stage authentication with the optional biometric runtime.

Stop with `docker compose -f compose.backend.yaml down`; database and document
volumes persist. Do not delete those volumes as a routine restart or credential
rotation step.

## Delivery documents

| Document | Purpose |
| --- | --- |
| [API reference](api-contracts.md) / [OpenAPI snapshot](openapi.json) | Public shapes, roles, errors and integration contracts |
| [Configuration](configuration.md) | Every backend setting, secret ownership and native/container differences |
| [Database and operations](database.md) | Native setup, migrations, account provisioning, persistence and recovery |
| [Architecture](architecture.md) | Trust boundaries, state ownership and approved-write sequence |
| [Demonstration](demonstration.md) | Reproducible synthetic backend rehearsal and team demo checklist |
| [Part 9 acceptance matrix](part-9-acceptance.md) | AT-01–AT-12 backend evidence and remaining owner handoffs |
| [Part 10 delivery report](part-10.md) | Fresh-clone/test evidence and final backend scope |

For transport details, use [MCP Part 4](part-4.md), [graph Part 6](part-6.md),
[approval Part 7](part-7.md), and [receipt recovery Part 8](part-8.md).
[Part 9](part-9.md) explains CI, Docker smoke tests and the provisional read target.
Earlier part documents describe their historical implementation stages; this
portal and current API reference describe the cumulative delivered backend.

## Verify and regenerate

```bash
python -m pip install -r backend/requirements-test.txt
# Supply an EMPTY DISPOSABLE database, never the application database.
# Linux/macOS:
export MEDILINK_TEST_DB_DSN='dbname=medilink_test host=localhost user=postgres password=your-test-password'
# PowerShell equivalent:
# $env:MEDILINK_TEST_DB_DSN = 'dbname=medilink_test host=localhost user=postgres password=your-test-password'
python -m pytest -q
python -m backend.ops.export_contract --check
python -m backend.ops.rehearse --output artifacts/backend-rehearsal.json
```

After changing public API shapes, regenerate `docs/backend/openapi.json` with
`python -m backend.ops.export_contract`; commit it with the implementation. CI
checks the snapshot, backend lint/tests, zero skipped DB tests, simple-read
performance and isolated Compose startup/persistence/outage recovery.

The remaining browser/agent/simulator/graph acceptance work is explicitly tracked.
The backend delivery branch is not a tagged, integrated whole-project release.
