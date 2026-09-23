"""Provision a signed MediLink staff ID card from the canonical PostgreSQL user.

Usage:
    python scripts/provision_staff.py doctor@medilink.demo

The script writes the signed QR payload and printable card under assets/.
It does not create or modify the staff account in PostgreSQL.
"""

import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.auth.id_card import generate_employee_qr


def get_user(email: str):
    kwargs = {"dbname": os.environ.get("MEDILINK_DB_NAME", "medilink")}
    for env_name, key in [
        ("MEDILINK_DB_HOST", "host"),
        ("MEDILINK_DB_PORT", "port"),
        ("MEDILINK_DB_USER", "user"),
        ("MEDILINK_DB_PASSWORD", "password"),
    ]:
        value = os.environ.get(env_name)
        if value:
            kwargs[key] = value

    with psycopg2.connect(**kwargs) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, full_name, email, role
                FROM users
                WHERE email = %s AND is_active = TRUE
                """,
                (email,),
            )
            return cursor.fetchone()


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/provision_staff.py <staff-email>")

    user = get_user(sys.argv[1].strip())
    if user is None:
        raise SystemExit("Active MediLink user not found.")

    user_id, full_name, email, role = user
    if role not in {"doctor", "receptionist"}:
        raise SystemExit(f"Unsupported staff role: {role}")

    assets = Path(__file__).resolve().parents[1] / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    safe_id = str(user_id)
    qr_path = assets / f"{safe_id}_qr.png"
    card_path = assets / f"{safe_id}_id_card.png"

    card_string = generate_employee_qr(safe_id, str(qr_path))
    print(f"Provisioned: {full_name} <{email}> ({role})")
    print(f"user_id: {safe_id}")
    print(f"QR: {qr_path}")
    print(f"Signed QR payload: {card_string}")
    print("Printable card generation can reuse generate_demo_card.py with these identity values.")


if __name__ == "__main__":
    main()
