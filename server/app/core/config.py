"""12-factor configuration. Everything overridable by environment variable so
the same image runs on a laptop at the venue or a managed host."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Placeholder so the app runs out of the box. Explicitly rejected in production
#: by `Settings.assert_production_safe`.
DEV_SECRET_KEY = "dev-only-insecure-change-me-0123456789abcdef"

MIN_SECRET_BYTES = 32


class InsecureConfiguration(RuntimeError):
    """Raised when the app would start with unsafe production settings."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="KP_", extra="ignore"
    )

    app_name: str = "Kitchen Pass"
    debug: bool = False

    #: SQLite by default so the API runs with no services installed. Point this
    #: at postgresql+asyncpg://... in deployment.
    database_url: str = "sqlite+aiosqlite:///./kitchen_pass.db"

    #: Signing key for access tokens. MUST be overridden outside development —
    #: `assert_production_safe` refuses to boot otherwise. At least 32 bytes,
    #: which is the minimum RFC 7518 recommends for HS256.
    secret_key: str = DEV_SECRET_KEY
    access_token_minutes: int = 60 * 12
    #: Court codes let a volunteer scorekeeper join one match without an account.
    court_code_minutes: int = 60 * 6

    #: Optional. Without it the WebSocket hub fans out in-process, which is
    #: correct for a single uvicorn worker and one less service to run.
    redis_url: str = ""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    def assert_production_safe(self) -> None:
        """Refuse to serve with a placeholder or too-short signing key.

        Called from the app lifespan rather than a field validator so that
        `Settings()` stays constructible in tests, while a real deployment
        still fails loudly instead of signing tokens with a public secret.
        """
        if self.debug:
            return
        if self.secret_key == DEV_SECRET_KEY:
            raise InsecureConfiguration(
                "KP_SECRET_KEY is still the development placeholder. Set a "
                "random value (e.g. `openssl rand -hex 32`) before deploying."
            )
        if len(self.secret_key.encode()) < MIN_SECRET_BYTES:
            raise InsecureConfiguration(
                f"KP_SECRET_KEY must be at least {MIN_SECRET_BYTES} bytes for "
                f"HS256; got {len(self.secret_key.encode())}."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
