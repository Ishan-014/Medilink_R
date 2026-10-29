"""Build and verify an isolated backend Compose project, then delete its volumes.

Only the generated medilink-smoke-<uuid> project is changed. Docker Desktop on
Windows can be invoked from WSL with --docker /path/to/docker.exe.
"""

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]


def host_path(path, docker):
    if sys.platform == "linux" and str(docker).endswith(".exe"):
        return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()
    return str(path)


def run(docker="docker"):
    project = "medilink-smoke-" + uuid4().hex[:12]
    # A developer's loaded .env must never override the synthetic test settings.
    child_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MEDILINK_", "COMPOSE_"))
    }
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    report = {
        "scope": "isolated backend Compose project with synthetic data",
        "checks": {},
    }
    with TemporaryDirectory(prefix="medilink-compose-smoke-") as storage:
        env = Path(storage) / "smoke.env"
        values = {
            "MEDILINK_DB_NAME": "medilink_smoke",
            "MEDILINK_DB_USER": "smoke",
            "MEDILINK_DB_PASSWORD": secrets.token_urlsafe(32),
            "MEDILINK_JWT_SECRET": secrets.token_urlsafe(48),
            "MEDILINK_CARD_SECRET": secrets.token_urlsafe(48),
            "MEDILINK_ACTION_SIGNING_SECRET": secrets.token_urlsafe(48),
            "MEDILINK_API_PORT": str(port),
        }
        env.write_text(
            "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
            encoding="utf-8",
        )
        env.chmod(0o600)
        prefix = [
            docker,
            "compose",
            "--project-name",
            project,
            "--env-file",
            host_path(env, docker),
            "-f",
            host_path(ROOT / "compose.backend.yaml", docker),
        ]

        def call(*args, timeout=600):
            return subprocess.run(
                [*prefix, *args],
                check=True,
                capture_output=True,
                env=child_env,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            ).stdout

        def wait_status(client, path, status, seconds=60):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    if client.get(path).status_code == status:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.3)
            raise RuntimeError("Expected health state was not reached")

        try:
            print(
                "Validating Compose configuration and building the backend image...",
                flush=True,
            )
            call("config", "--quiet")
            call("up", "--build", "--detach", "--wait", "--wait-timeout", "240")
            report["checks"]["compose_startup_and_health"] = True
            report["compose_version"] = subprocess.check_output(
                [docker, "compose", "version", "--short"], text=True
            ).strip()
            print(
                "Checking container permissions, image scope and authenticated API...",
                flush=True,
            )
            uid = call("exec", "-T", "backend", "id", "-u").strip()
            assert uid == "10001", "Backend image must run as its unprivileged UID"
            report["runtime_versions"] = json.loads(
                call(
                    "exec",
                    "-T",
                    "backend",
                    "python",
                    "-c",
                    "import json,platform; from importlib.metadata import version; print(json.dumps({'python':platform.python_version(),'fastapi':version('fastapi'),'uvicorn':version('uvicorn')}))",
                )
            )
            report["runtime_versions"]["postgresql"] = call(
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "smoke",
                "-d",
                "medilink_smoke",
                "-Atc",
                "SHOW server_version",
            ).strip()
            call(
                "exec",
                "-T",
                "backend",
                "python",
                "-c",
                "from pathlib import Path; assert all(not Path(p).exists() for p in ['/app/.env','/app/frontend','/app/backend/agents','/app/backend/mcp'])",
            )
            report["checks"]["non_root_and_scoped_image"] = True
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=8, trust_env=False
            ) as client:
                assert client.get("/api/health").status_code == 200
                password = secrets.token_urlsafe(24)
                assert (
                    client.post(
                        "/api/auth/register",
                        json={
                            "full_name": "Synthetic Smoke Patient",
                            "email": "smoke@example.com",
                            "password": password,
                        },
                    ).status_code
                    == 201
                )
                login = client.post(
                    "/api/auth/login",
                    json={"email": "smoke@example.com", "password": password},
                )
                assert login.status_code == 200
                headers = {
                    "Authorization": "Bearer " + login.json()["data"]["access_token"]
                }
                created = client.post(
                    "/api/workflows",
                    headers=headers,
                    json={
                        "workflow_type": "onboarding",
                        "request": "Synthetic Compose persistence check",
                    },
                )
                assert created.status_code == 201
                workflow = created.json()["data"]["id"]
                uploaded = client.post(
                    "/api/documents",
                    headers=headers,
                    files={
                        "file": (
                            "synthetic.txt",
                            b"Synthetic Compose smoke data",
                            "text/plain",
                        )
                    },
                )
                assert uploaded.status_code == 201
                document = uploaded.json()["data"]["id"]
                print("Checking persistence across backend restart...", flush=True)
                call("restart", "backend")
                wait_status(client, "/api/health/ready", 200)
                assert (
                    client.get(
                        f"/api/workflows/{workflow}", headers=headers
                    ).status_code
                    == 200
                )
                assert (
                    client.get(
                        f"/api/documents/{document}/content", headers=headers
                    ).content
                    == b"Synthetic Compose smoke data"
                )
                report["checks"]["database_and_private_document_persistence"] = True
                print(
                    "Checking database outage and recovery health states...", flush=True
                )
                call("stop", "db")
                wait_status(client, "/api/health/ready", 503)
                assert client.get("/api/health").status_code == 200
                report["checks"]["outage_readiness_503_liveness_200"] = True
                call("start", "db")
                wait_status(client, "/api/health/ready", 200)
                assert (
                    client.get(
                        f"/api/workflows/{workflow}", headers=headers
                    ).status_code
                    == 200
                )
                report["checks"]["database_restart_recovery"] = True
            status = call(
                "run",
                "--rm",
                "--no-deps",
                "migrate",
                "python",
                "-m",
                "backend.db.migrate",
                "--status",
            )
            lines = [line for line in status.splitlines() if line.strip()]
            assert lines and all(line.endswith(": applied") for line in lines)
            report["applied_migrations"] = len(lines)
            report["checks"]["migration_replay_is_noop"] = True
            report["passes"] = True
            return report
        finally:
            print(
                "Removing the isolated smoke-test project and its test volumes...",
                flush=True,
            )
            call("down", "--volumes", "--remove-orphans", timeout=90)
            report["cleanup"] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run(args.docker)
    except Exception:
        parser.exit(
            1,
            "Compose verification failed. Check Docker availability and the isolated backend build/startup.\n",
        )
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
