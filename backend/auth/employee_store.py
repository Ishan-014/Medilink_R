"""PostgreSQL-backed employee identity adapter for MediLink authentication.

The 3-layer auth pipeline uses this module as its identity boundary. It reads
from the same PostgreSQL users table used by the application backend.
"""
from dotenv import load_dotenv

load_dotenv()
from typing import Optional

from .models import Employee


def _get_conn():
    from backend.db.connection import connection
    return connection()


def _row_to_employee(row) -> Optional[Employee]:
    if row is None:
        return None
    return Employee(
        employee_id=str(row[0]),
        username=row[2],
        hashed_password=row[3],
        full_name=row[1],
        role=row[4],
    )


def get_employee_by_username(username: str) -> Optional[Employee]:
    """The auth UI calls this field username; MediLink uses staff email."""
    with _get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, full_name, email, password_hash, role
                FROM users
                WHERE lower(email) = %s AND is_active = TRUE
                """,
                (username.strip().lower(),),
            )
            return _row_to_employee(cursor.fetchone())


def get_employee_by_id(employee_id: str) -> Optional[Employee]:
    with _get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, full_name, email, password_hash, role
                FROM users
                WHERE user_id::text = %s AND is_active = TRUE
                """,
                (str(employee_id),),
            )
            return _row_to_employee(cursor.fetchone())


def init_db():
    # Compatibility hook for the existing auth bootstrap.
    return None
