"""Trusted local provisioning: python -m backend.auth.provision EMAIL --role admin.

Requires database administrator access. Password is prompted, never a CLI flag.
This creates accounts only; existing signed ID card/face enrollment is separate.
"""

import argparse
from getpass import getpass

import psycopg2
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.auth import Registration, create_account
from backend.api.schemas import Role
from backend.core.config import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--role", required=True, choices=[r.value for r in Role])
    args = parser.parse_args()
    name = input("Full name: ")
    password = getpass("Password (at least 12 characters): ")
    if password != getpass("Confirm password: "):
        parser.exit(1, "Passwords do not match.\n")
    try:
        body = Registration(email=args.email, full_name=name, password=password)
        user = create_account(body, Role(args.role), get_settings())
    except (ValidationError, HTTPException, psycopg2.Error):
        # Avoid printing validation input or database details containing secrets.
        parser.exit(1, "Provisioning failed; check account fields, uniqueness, and database setup.\n")
    print(f"Created account {user['user_id']} with role {user['role']}.")


if __name__ == "__main__":
    main()
