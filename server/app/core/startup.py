"""Things that must happen before the app serves its first request.

On a free host there is no separate release phase and no shell to run commands
in — the container just starts. So migrations run here, and optionally the demo
data, guarded so a restart never destroys real data.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic.config import Config
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from alembic import command

from .config import Settings

log = logging.getLogger("kitchen_pass.startup")

SERVER_ROOT = Path(__file__).resolve().parents[2]


def _upgrade(settings: Settings) -> None:
    config = Config(str(SERVER_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.sync_database_url)
    config.attributes["configure_logger"] = False

    command.upgrade(config, "head")


async def run_migrations(settings: Settings) -> None:
    """Bring the schema to head, off the event loop.

    Alembic is synchronous — it is handed the sync form of the database URL and
    run in a worker thread, because the caller is already inside a running loop.
    """
    log.info("running migrations")
    await asyncio.to_thread(_upgrade, settings)
    log.info("migrations complete")


async def seed_demo_if_empty(session: AsyncSession) -> bool:
    """Load the demo tournament, but only into an empty database.

    Deployed instances restart for all sorts of reasons — a redeploy, an
    idle-timeout wake, a crash loop. Re-seeding on any of those would wipe
    whatever a real user had entered, so this is strictly a first-boot action.
    """
    from app.models import User

    existing = (await session.exec(select(User).limit(1))).first()
    if existing is not None:
        log.info("database already has users; skipping demo seed")
        return False

    log.info("empty database — loading demo tournament")
    from scripts.seed import seed

    await seed()
    return True
