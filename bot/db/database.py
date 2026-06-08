import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.db.models import Base

# На Railway: DATABASE_URL=sqlite+aiosqlite:////data/bottennis.db (Volume at /data)
# Локально:   файл bottennis.db рядом с main.py
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bottennis.db")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_db()
    # Бэкфилл достижений по истории матчей (идемпотентен)
    from bot.services.achievements import backfill_achievements
    async with async_session() as session:
        await backfill_achievements(session)


async def _migrate_db() -> None:
    """Добавляет колонки, появившиеся после первого деплоя.
    SQLite не поддерживает IF NOT EXISTS в ALTER TABLE,
    поэтому просто игнорируем ошибку если колонка уже есть.
    """
    migrations = [
        # v1.2.0
        "ALTER TABLE matches ADD COLUMN reminder_sent BOOLEAN NOT NULL DEFAULT 0",
        # v1.2.0
        "ALTER TABLE matches ADD COLUMN completed_at DATETIME",
        # v1.9.0
        "ALTER TABLE matches ADD COLUMN accepted_at DATETIME",
        # v2.12.0
        "ALTER TABLE players ADD COLUMN last_menu_message_id INTEGER",
        # v2.30.0
        "ALTER TABLE players ADD COLUMN peak_rating REAL",
        "UPDATE players SET peak_rating = rating WHERE peak_rating IS NULL",
        # v2.32.0
        "ALTER TABLE players ADD COLUMN achievements TEXT DEFAULT '[]'",
        # v2.41.0
        "ALTER TABLE players ADD COLUMN backfill_version INTEGER DEFAULT 0",
    ]
    async with engine.begin() as conn:
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass  # колонка уже существует — OK
