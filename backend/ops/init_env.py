"""Create a private local .env with independent random secrets; never overwrite it.

This configures a NEW backend Compose database. Native PostgreSQL users must set
the generated DB password to match their separately provisioned local role.
"""

import argparse
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[2]
SECRET_KEYS = (
    "MEDILINK_DB_PASSWORD",
    "MEDILINK_JWT_SECRET",
    "MEDILINK_CARD_SECRET",
    "MEDILINK_ACTION_SIGNING_SECRET",
)


def create_env(destination, *, api_port=8000, template=None):
    if not 1 <= api_port <= 65535:
        raise ValueError("API port must be between 1 and 65535")
    source = template or ROOT / ".env.example"
    replacements = {key: secrets.token_urlsafe(48) for key in SECRET_KEYS}
    replacements["MEDILINK_API_PORT"] = str(api_port)
    lines = []
    found = set()
    for line in source.read_text(encoding="utf-8").splitlines():
        key, separator, _ = line.partition("=")
        if separator and key in replacements:
            line = f"{key}={replacements[key]}"
            found.add(key)
        lines.append(line)
    if found != set(replacements):
        raise ValueError("Environment template is missing required fields")
    # Exclusive creation also refuses existing symlinks. Permissions are applied
    # at creation, rather than exposing a secret file before chmod.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
    except BaseException:
        Path(destination).unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    parser.add_argument("--api-port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        create_env(args.output, api_port=args.api_port)
    except FileExistsError:
        parser.exit(1, "Environment file already exists; it was left unchanged.\n")
    except (OSError, ValueError):
        parser.exit(
            1, "Environment setup failed; check the destination, template and port.\n"
        )
    print(
        "Created a private environment file. Existing database credentials and external service endpoints still require operator configuration."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
