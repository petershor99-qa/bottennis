"""
Одноразовый скрипт: поднимает рейтинг всех игроков ниже 1000 до 1000.
Запускать: railway run python fix_ratings.py
"""
import asyncio
from bot.db.database import async_session
from bot.db.models import Player
from sqlalchemy import select, update


async def fix():
    async with async_session() as session:
        # Показываем кого затронет
        r = await session.execute(select(Player).where(Player.rating < 1000.0))
        players = r.scalars().all()

        if not players:
            print("Все рейтинги уже >= 1000. Ничего не изменено.")
            return

        print(f"Найдено игроков с рейтингом < 1000: {len(players)}")
        for p in players:
            print(f"  {p.display_name}: {p.rating} → 1000.0")

        await session.execute(
            update(Player).where(Player.rating < 1000.0).values(rating=1000.0)
        )
        await session.commit()
        print("Готово. Рейтинги обновлены.")


asyncio.run(fix())
