"""
Одноразовый скрипт: откатывает ОДНО последнее авто-освобождение трона,
закрытое БЕЗ боя (не боссфайтом), и рассылает клубу объясняющее сообщение.

Контекст (2026-09-28): до фикса v2.135.3 порог авто-освобождения трона был
7 дней — слишком коротко для клуба с редкими перерывами в игре. Чемпион не
играл на выходных + начало недели, автоджоба ошибочно забрала у него трон.
Порог поднят до 14 дней, но задним числом это не отменяет уже случившийся
перенос — этот скрипт откатывает его вручную.

Находит последнее ЗАКРЫТОЕ правление (ChampionReign), у которого НЕТ
соответствующего боссфайт-матча с completed_at == ended_at (та же логика,
что использует _reign_end_narrative в bot/handlers/leaderboard.py для
нарратива в Зале славы — "нет такого матча" там прямо означает "трон
отошёл без боя", то есть авто-освобождением). Откатывает именно его:
удаляет ошибочно созданную новую запись ChampionReign, переоткрывает
старую, возвращает Player.is_champion бывшему чемпиону. Рейтинг никогда
не трогается — только флаг трона и запись правления.

Без хардкода имён игроков — репозиторий публичный (см. правило в
CLAUDE.md), скрипт находит нужное правление и игроков из состояния самой
БД, а не из аргументов командной строки.

Запуск на VPS (после деплоя v2.135.3, чтобы новые авто-освобождения больше
не срабатывали раньше 14 дней):
    cd /opt/bottennis && .venv/bin/python scripts/revert_last_auto_release.py
"""
import asyncio
from html import escape as h

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv
from sqlalchemy import or_, select

load_dotenv()

import os  # noqa: E402 (после load_dotenv — см. main.py про тот же порядок)

from bot.db.database import async_session  # noqa: E402
from bot.db.models import ChampionReign, Match, MatchStatus, Player  # noqa: E402


async def main() -> None:
    async with async_session() as session:
        reigns_r = await session.execute(
            select(ChampionReign)
            .where(ChampionReign.ended_at.is_not(None))
            .order_by(ChampionReign.ended_at.desc())
        )
        target = None
        for reign in reigns_r.scalars().all():
            boss_fight_r = await session.execute(
                select(Match).where(
                    Match.is_boss_fight == True,  # noqa: E712
                    Match.status == MatchStatus.completed,
                    Match.completed_at == reign.ended_at,
                    or_(
                        Match.challenger_id == reign.player_id,
                        Match.challenged_id == reign.player_id,
                    ),
                    Match.winner_id != reign.player_id,
                ).limit(1)
            )
            if boss_fight_r.scalar_one_or_none() is None:
                target = reign
                break

        if target is None:
            print("Не найдено ни одного правления, закрытого авто-освобождением (без боя). Ничего не делаю.")
            return

        old_champion = (
            await session.execute(select(Player).where(Player.id == target.player_id))
        ).scalar_one()

        new_reign = (
            await session.execute(
                select(ChampionReign).where(
                    ChampionReign.started_at == target.ended_at,
                    ChampionReign.player_id != target.player_id,
                )
            )
        ).scalar_one_or_none()
        new_champion = None
        if new_reign is not None:
            new_champion = (
                await session.execute(select(Player).where(Player.id == new_reign.player_id))
            ).scalar_one_or_none()

        print(f"Откатываю авто-освобождение: правление {old_champion.display_name} закрылось {target.ended_at}")
        if new_champion is not None:
            print(f"  Ошибочный преемник: {new_champion.display_name} — is_champion снимается")
            new_champion.is_champion = False
        if new_reign is not None:
            await session.delete(new_reign)

        old_champion.is_champion = True
        target.ended_at = None

        await session.commit()
        print("Готово: трон и запись правления восстановлены, рейтинг не менялся.")

        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            print("BOT_TOKEN не найден в .env — сообщение клубу не отправлено, сделайте это вручную.")
            return

        players = (await session.execute(select(Player))).scalars().all()
        text = (
            "⚖️ Произошло обнуление чемпионских сроков. "
            f"<b>{h(old_champion.display_name)}</b> продолжает править. "
            "Вбросов и нарушений замечено не было. "
            "Всё прошло демократическим и легитимным путём. Такова жись."
        )

        bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            for p in players:
                try:
                    await bot.send_message(p.telegram_id, text)
                except Exception as e:
                    print(f"  не удалось отправить {p.display_name}: {e}")
        finally:
            await bot.session.close()

        print("Сообщение разослано.")


if __name__ == "__main__":
    asyncio.run(main())
