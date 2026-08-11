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
    #: at postgresql+asyncpg://... in deployment. Managed hosts usually inject a
    #: bare `postgres://` URL, which `_normalise_database_url` rewrites.
    database_url: str = "sqlite+aiosqlite:///./kitchen_pass.db"

    #: Load the demo tournament on first boot, if the database is empty. Handy
    #: for a test deployment; never re-seeds over existing data.
    seed_on_start: bool = False

    #: Serve the built frontend from this directory. One service, one origin,
    #: no CORS, and same-origin WebSockets — which is what free hosts make easy.
    static_dir: str = ""

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

    @field_validator("database_url", mode="after")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Accept the URL forms managed hosts actually hand out.

        Render, Heroku, Neon and friends inject `postgres://…` or
        `postgresql://…`, neither of which names an async driver — SQLAlchemy
        would try psycopg2 and fail at startup with a confusing error. Rewrite
        to asyncpg so the platform's own value can be pasted in unedited.
        """
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://"):]
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://"):]
        return value

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def sync_database_url(self) -> str:
        """The same database through a synchronous driver, for Alembic."""
        return (
            self.database_url
            .replace("postgresql+asyncpg://", "postgresql+psycopg://")
            .replace("sqlite+aiosqlite://", "sqlite://")
        )

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
