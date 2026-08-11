"""Database URL handling.

Every case here is a connection string a real host actually hands out. Getting
any of them wrong means the deployment fails at startup with an error that
points at SQLAlchemy internals rather than at the URL.
"""

from __future__ import annotations

import pytest

from app.core.config import DEV_SECRET_KEY, InsecureConfiguration, Settings


def urls(raw: str) -> tuple[str, str]:
    settings = Settings(database_url=raw)
    return settings.database_url, settings.sync_database_url


@pytest.mark.parametrize(
    ("raw", "expected_async"),
    [
        # Heroku/Render style.
        ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        # Plain libpq style.
        ("postgresql://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        # Already explicit.
        ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        # SQLite is left alone.
        ("sqlite+aiosqlite:///./x.db", "sqlite+aiosqlite:///./x.db"),
    ],
)
def test_scheme_is_normalised_to_an_async_driver(raw, expected_async):
    assert urls(raw)[0] == expected_async


def test_neon_connection_string_is_usable_by_both_drivers():
    """Neon's copy-paste string, verbatim.

    asyncpg rejects `sslmode`/`channel_binding` outright; psycopg requires
    `sslmode`. The two drivers need different spellings of the same intent.
    """
    raw = "postgresql://u:p@ep-x.aws.neon.tech/db?sslmode=require&channel_binding=require"
    async_url, sync_url = urls(raw)

    assert async_url == "postgresql+asyncpg://u:p@ep-x.aws.neon.tech/db?ssl=require"
    assert "sslmode" not in async_url
    assert "channel_binding" not in async_url

    assert sync_url == "postgresql+psycopg://u:p@ep-x.aws.neon.tech/db?sslmode=require"
    assert "ssl=require" not in sync_url.replace("sslmode=require", "")


def test_normalising_twice_does_not_duplicate_ssl():
    once = Settings(database_url="postgresql://u:p@h/db?sslmode=require").database_url
    twice = Settings(database_url=once).database_url
    assert once == twice
    assert twice.count("ssl=") == 1


def test_sslmode_disable_is_dropped_rather_than_mistranslated():
    async_url, sync_url = urls("postgresql://u:p@h/db?sslmode=disable")
    assert "ssl" not in async_url
    assert "sslmode" not in sync_url


def test_other_query_parameters_survive():
    async_url, _ = urls("postgresql://u:p@h/db?sslmode=require&application_name=kp")
    assert "application_name=kp" in async_url


def test_credentials_with_special_characters_are_preserved():
    """Generated passwords routinely contain characters that are meaningful in
    a URL; rewriting the query must not mangle the userinfo."""
    raw = "postgresql://user:p%40ss-w%2Frd@h:5432/db?sslmode=require"
    async_url, _ = urls(raw)
    assert "user:p%40ss-w%2Frd@h:5432" in async_url


def test_sqlite_sync_url_drops_the_async_driver():
    assert urls("sqlite+aiosqlite:///./x.db")[1] == "sqlite:///./x.db"


def test_is_postgres_flag():
    assert Settings(database_url="postgres://u:p@h/db").is_postgres
    assert not Settings(database_url="sqlite+aiosqlite:///./x.db").is_postgres


# ---------------------------------------------------------------------------
# Production safety
# ---------------------------------------------------------------------------


def test_placeholder_key_is_refused_in_production():
    with pytest.raises(InsecureConfiguration, match="development placeholder"):
        Settings(debug=False, secret_key=DEV_SECRET_KEY).assert_production_safe()


def test_short_key_is_refused_in_production():
    with pytest.raises(InsecureConfiguration, match="at least 32 bytes"):
        Settings(debug=False, secret_key="too-short").assert_production_safe()


def test_a_real_key_is_accepted():
    Settings(debug=False, secret_key="x" * 64).assert_production_safe()


def test_debug_mode_skips_the_check():
    """Otherwise `./dev.sh` would demand a generated key on a laptop."""
    Settings(debug=True, secret_key=DEV_SECRET_KEY).assert_production_safe()


def test_cors_origins_accept_a_comma_separated_string():
    settings = Settings(cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]
