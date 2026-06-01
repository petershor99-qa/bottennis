"""
Одноразовый скрипт: добавляет колонку accepted_at в таблицу matches.
Запускать: railway run python migrate_add_accepted_at.py
"""
import asyncio
from bot.db.database import engine


async def migrate():
    async with engine.begin() as conn:
        # Проверяем есть ли уже колонка
        result = await conn.exec_driver_sql("PRAGMA table_info(matches)")
        columns = [row[1] for row in result.fetchall()]

        if "accepted_at" in columns:
            print("Колонка accepted_at уже существует. Ничего не делаем.")
            return

        await conn.exec_driver_sql(
            "ALTER TABLE matches ADD COLUMN accepted_at DATETIME"
        )
        print("Колонка accepted_at добавлена успешно.")


asyncio.run(migrate())
