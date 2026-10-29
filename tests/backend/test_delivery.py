"""Delivery helpers must preserve local secrets and publish accurate contracts."""

import ast
import os
from pathlib import Path

from dotenv import dotenv_values
import pytest

from backend.db import connection as db
from backend.ops import export_contract, init_env, rehearse

ROOT = Path(__file__).resolve().parents[2]


def test_generated_environment_has_independent_keys_and_private_permissions(
    tmp_path, capsys
):
    destination = tmp_path / "local.env"
    assert init_env.main(["--output", str(destination), "--api-port", "18310"]) == 0
    values = dotenv_values(destination)
    secrets = [values[key] for key in init_env.SECRET_KEYS]
    assert all(secret and len(secret) >= 32 for secret in secrets)
    assert len(set(secrets)) == 4
    assert values["MEDILINK_API_PORT"] == "18310"
    assert values["MEDILINK_MEDIUM_RISK_REVIEW"] == "required"
    output = capsys.readouterr().out
    assert all(secret not in output for secret in secrets)
    if os.name != "nt":
        assert destination.stat().st_mode & 0o777 == 0o600


def test_environment_setup_never_overwrites_existing_file_or_symlink(tmp_path):
    existing = tmp_path / "existing.env"
    existing.write_text("OPERATOR_SETTING=keep-this-exactly\n")
    with pytest.raises(FileExistsError):
        init_env.create_env(existing)
    assert existing.read_text() == "OPERATOR_SETTING=keep-this-exactly\n"
    link = tmp_path / "link.env"
    link.symlink_to(existing)
    with pytest.raises(FileExistsError):
        init_env.create_env(link)
    assert existing.read_text() == "OPERATOR_SETTING=keep-this-exactly\n"


@pytest.mark.parametrize("port", [0, 65536])
def test_invalid_port_cannot_create_environment_file(tmp_path, port):
    destination = tmp_path / "local.env"
    with pytest.raises(ValueError):
        init_env.create_env(destination, api_port=port)
    assert not destination.exists()


def test_environment_setup_removes_partial_file_after_write_failure(
    tmp_path, monkeypatch
):
    destination = tmp_path / "local.env"
    original = os.fdopen

    class BrokenStream:
        def __init__(self, *args, **kwargs):
            self.stream = original(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, value):
            self.stream.write(value[:20])
            self.stream.flush()
            raise OSError("synthetic disk failure")

    monkeypatch.setattr(init_env.os, "fdopen", BrokenStream)
    with pytest.raises(OSError):
        init_env.create_env(destination)
    assert not destination.exists()


def test_example_environment_covers_every_settings_input():
    tree = ast.parse((ROOT / "backend/core/config.py").read_text())
    required = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
        ):
            if node.args and isinstance(node.args[0], ast.Constant):
                required.add(node.args[0].value)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
        ):
            if isinstance(node.slice, ast.Constant):
                required.add(node.slice.value)
    assert required <= set(dotenv_values(ROOT / ".env.example"))


def test_openapi_export_matches_snapshot_without_connecting_services(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Contract export attempted database I/O")

    monkeypatch.setattr(db, "connect", forbidden)
    sentinel = "synthetic-secret-must-not-appear-in-openapi"
    monkeypatch.setenv("MEDILINK_JWT_SECRET", sentinel)
    document = export_contract.content()
    assert sentinel not in document
    assert document == (ROOT / "docs/backend/openapi.json").read_text(encoding="utf-8")
    assert export_contract.main(["--check"]) == 0


def test_contract_checker_rejects_stale_artifact(tmp_path):
    target = tmp_path / "openapi.json"
    target.write_text('{"outdated":true}')
    with pytest.raises(SystemExit) as exc:
        export_contract.main(["--check", "--output", str(target)])
    assert exc.value.code == 1 and target.read_text() == '{"outdated":true}'


def test_rehearsal_requires_explicit_disposable_database(monkeypatch):
    monkeypatch.delenv("MEDILINK_TEST_DB_DSN", raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Rehearsal launched without a test DB opt-in")

    monkeypatch.setattr(rehearse.subprocess, "run", forbidden)
    with pytest.raises(SystemExit) as exc:
        rehearse.main([])
    assert exc.value.code == 2


def test_backend_delivery_document_links_resolve():
    import re
    from urllib.parse import unquote

    documents = [ROOT / "README.md", *(ROOT / "docs/backend").glob("*.md")]
    broken = []
    for document in documents:
        for target in re.findall(
            r"\[[^\]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")
        ):
            target = unquote(target.strip("<>").split("#", 1)[0])
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (document.parent / target).exists():
                broken.append((str(document.relative_to(ROOT)), target))
    assert not broken
