"""Environment configuration for the backend foundation (no AI credentials)."""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    db_name: str = "medilink"
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str | None = field(default=None, repr=False)
    db_connect_timeout: int = 5
    db_statement_timeout_ms: int = 5000
    jwt_secret: str | None = field(default=None, repr=False)
    access_token_minutes: int = 60
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173", "http://127.0.0.1:5173"
    )

    def __post_init__(self):
        if not 1 <= self.db_port <= 65535:
            raise ValueError("MEDILINK_DB_PORT must be between 1 and 65535")
        if self.db_connect_timeout < 1 or self.db_statement_timeout_ms < 1:
            raise ValueError("Database timeouts must be positive")
        if not 1 <= self.access_token_minutes <= 480:
            raise ValueError("MEDILINK_ACCESS_TOKEN_MINUTES must be between 1 and 480")

    @classmethod
    def from_env(cls):
        # Existing environment values take precedence over the local .env file.
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls(
            db_name=os.getenv("MEDILINK_DB_NAME", "medilink"),
            db_host=os.getenv("MEDILINK_DB_HOST", "localhost"),
            db_port=int(os.getenv("MEDILINK_DB_PORT", "5432")),
            db_user=os.getenv("MEDILINK_DB_USER", "postgres"),
            db_password=os.getenv("MEDILINK_DB_PASSWORD"),
            db_connect_timeout=int(os.getenv("MEDILINK_DB_CONNECT_TIMEOUT", "5")),
            db_statement_timeout_ms=int(os.getenv("MEDILINK_DB_STATEMENT_TIMEOUT_MS", "5000")),
            jwt_secret=os.getenv("MEDILINK_JWT_SECRET"),
            access_token_minutes=int(os.getenv("MEDILINK_ACCESS_TOKEN_MINUTES", "60")),
            cors_origins=tuple(
                origin.strip() for origin in os.getenv(
                    "MEDILINK_CORS_ORIGINS",
                    "http://localhost:5173,http://127.0.0.1:5173",
                ).split(",") if origin.strip()
            ),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
