"""One-use staff login stages. Store only token digests, never bearer tokens."""

import hashlib

import psycopg2
from fastapi import HTTPException

from backend.db.connection import connection


def consume_stage(token: str):
    digest = hashlib.sha256(token.encode()).hexdigest()
    try:
        with connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO consumed_auth_stages (token_digest) VALUES (%s)", (digest,))
    except psycopg2.errors.UniqueViolation as exc:
        raise HTTPException(401, "Authentication stage was already used; log in again") from exc
