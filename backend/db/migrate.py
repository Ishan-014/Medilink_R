"""Run with python -m backend.db.migrate [--status]. Forward-only, atomic SQL."""

import argparse
import hashlib
from pathlib import Path

import psycopg2

from backend.db.connection import connection

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
LOCK_ID = 7423101901


def migration_files(directory: Path = MIGRATIONS_DIR):
    files = sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    versions = [path.name.split("_", 1)[0] for path in files]
    if len(versions) != len(set(versions)):
        raise ValueError("Duplicate database migration version")
    return files


def migrate(conn, *, status_only=False, directory: Path = MIGRATIONS_DIR):
    """Caller owns transaction/connection; any error rolls back the entire batch."""
    files = migration_files(directory)
    contents = {path.name: path.read_bytes() for path in files}
    checksums = {name: hashlib.sha256(body).hexdigest() for name, body in contents.items()}
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        cursor.execute("SELECT to_regclass('public.schema_migrations')")
        exists = cursor.fetchone()[0] is not None
        if not exists and not status_only:
            cursor.execute("""
                CREATE TABLE public.schema_migrations (
                    name TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        applied = {}
        if exists:
            cursor.execute("SELECT name, checksum FROM public.schema_migrations ORDER BY name")
            applied = dict(cursor.fetchall())
        for name, checksum in applied.items():
            if name not in checksums or checksums[name] != checksum:
                raise ValueError(f"Applied migration missing or modified: {name}")
        # Refuse inserting an old migration after newer ones have been applied.
        if applied and any(name < max(applied) for name in contents if name not in applied):
            raise ValueError("Out-of-order migration; add a new version instead")
        results = []
        for name, body in contents.items():
            state = "applied" if name in applied else "pending"
            if state == "pending" and not status_only:
                cursor.execute(body.decode("utf-8"))
                cursor.execute(
                    "INSERT INTO public.schema_migrations (name, checksum) VALUES (%s, %s)",
                    (name, checksums[name]),
                )
                state = "applied"
            results.append((name, state))
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Show status without changing schema")
    args = parser.parse_args()
    try:
        with connection() as conn:
            results = migrate(conn, status_only=args.status)
    except (psycopg2.Error, ValueError):
        # Database exceptions can contain credentials/SQL/input data.
        parser.exit(1, "Migration failed; check database access, migration checksums, and schema compatibility.\n")
    for name, state in results:
        print(f"{name}: {state}")


if __name__ == "__main__":
    main()
