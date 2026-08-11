"""12-factor configuration. Everything overridable by environment variable so
the same image runs on a laptop at the venue or a managed host."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Placeholder so the app runs out of the box. Explicitly rejected in production
#: by `Settings.assert_production_safe`.
DEV_SECRET_KEY = "dev-only-insecure-change-me-0123456789abcdef"

MIN_SECRET_BYTES = 32


class InsecureConfiguration(RuntimeError):
    """Raised when the app would start with unsafe production settings."""


def _query_value(url: str, key: str) -> str | None:
    query = urlsplit(url).query
    for name, value in parse_qsl(query, keep_blank_values=True):
        if name == key:
            return value
    return None


def _ssl_mode(url: str) -> str | None:
    """Translate libpq's `sslmode` into asyncpg's `ssl`.

    `disable` has no asyncpg equivalent worth expressing — dropping the
    parameter entirely is the same thing. Everything else asks for TLS, and
    asyncpg's `require` is the closest honest mapping: it encrypts but does not
    verify the server certificate, which is what libpq's `require` also does.
    """
    mode = _query_value(url, "sslmode") or _query_value(url, "ssl")
    if mode in (None, "disable", "allow"):
        return None
    return "require" if mode in ("require", "prefer") else mode


def _rewrite_query(url: str, *, drop: tuple[str, ...], add: dict[str, str | None]) -> str:
    parts = urlsplit(url)
    params = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in drop
    ]
    params += [(k, v) for k, v in add.items() if v is not None]
    return urlunsplit(parts._replace(query=urlencode(params)))


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

        Two incompatibilities to absorb, so a platform's own connection string
        can be pasted in unedited:

        1. Render, Heroku and Neon give `postgres://…` or `postgresql://…`.
           Neither names an async driver, so SQLAlchemy reaches for psycopg2 and
           dies at startup.
        2. Neon appends `?sslmode=require&channel_binding=require`. Those are
           libpq options — asyncpg rejects them outright with
           "connect() got an unexpected keyword argument 'sslmode'". asyncpg
           spells the same thing `ssl=require`.
        """
        if value.startswith("postgres://"):
            value = "postgresql+asyncpg://" + value[len("postgres://"):]
        elif value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://"):]

        if "+asyncpg" not in value:
            return value
        # `ssl` is dropped too, not just the libpq spellings: normalising an
        # already-normalised URL would otherwise append a duplicate.
        return _rewrite_query(
            value,
            drop=("sslmode", "channel_binding", "ssl"),
            add={"ssl": _ssl_mode(value)},
        )

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def sync_database_url(self) -> str:
        """The same database through a synchronous driver, for Alembic.

        psycopg wants `sslmode`, which is exactly the parameter the async URL
        had to drop — so translate back rather than passing asyncpg's spelling
        to a driver that has never heard of it.
        """
        url = (
            self.database_url
            .replace("postgresql+asyncpg://", "postgresql+psycopg://")
            .replace("sqlite+aiosqlite://", "sqlite://")
        )
        if "+psycopg" not in url:
            return url
        mode = _query_value(self.database_url, "ssl")
        return _rewrite_query(
            url, drop=("ssl",), add={"sslmode": mode} if mode else {}
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
