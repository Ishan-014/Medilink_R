"""Export/check the public backend OpenAPI contract without connecting services."""

import argparse
import json
from pathlib import Path

from backend.application import create_app
from backend.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "docs/backend/openapi.json"


def content():
    return (
        json.dumps(
            create_app(Settings()).openapi(),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = content()
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != text:
            parser.exit(
                1,
                "OpenAPI snapshot is missing or stale; regenerate it with backend.ops.export_contract.\n",
            )
        print("OpenAPI snapshot matches the backend.")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print("Exported the backend OpenAPI contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
