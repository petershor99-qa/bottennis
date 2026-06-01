"""
Одноразовый скрипт: добавляет колонку challenge_reminder_sent в таблицу matches.
Запускать: railway run python migrate_add_challenge_reminder.py
"""
import asyncio

from bot.db.database import engine


async def migrate():
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(matches)")
        columns = [row[1] for row in result.fetchall()]

        if "challenge_reminder_sent" in columns:
            print("Колонка challenge_reminder_sent уже существует. Ничего не делаем.")
            return

        await conn.exec_driver_sql(
            "ALTER TABLE matches ADD COLUMN challenge_reminder_sent BOOLEAN NOT NULL DEFAULT 0"
        )
        print("Колонка challenge_reminder_sent добавлена успешно.")


asyncio.run(migrate())
