"""
Кэш тяжёлых ответов приложения (v2.189.0) и прогрев в фоне.

Профиль игрока на клубе в несколько тысяч матчей собирался 0,2–0,4 с: вся
карьера игрока из базы, полная статистика, а на первом запросе после матча —
ещё и статистика всех игроков для цвета показателей. Данные меняются только
с новым матчем, поэтому ответ хранится в памяти по «версии данных клуба»:
число и последний id завершённых матчей плюс сумма рейтингов и число игроков
(правка рейтинга админом, новый игрок). Сменилась версия — ответ считается
заново. Срок жизни (TTL) — страховка для текста, зависящего от времени
(«за неделю», «N дней»): через TTL ответ пересчитывается и без нового матча.

Прогрев: раз в минуту фоновая задача веб-сервера проверяет версию (два
лёгких запроса) и, если она сменилась, заранее собирает профили всех игроков
(свой и чужой вид), цвета показателей и рекорды клуба. Первый, кто откроет
профиль после матча, получает готовый ответ.
"""
import asyncio
import copy
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, MatchStatus, Player

logger = logging.getLogger(__name__)

TTL_S = 30 * 60
MAX_ENTRIES = 128
WARM_EVERY_S = 60

_store: dict[tuple, tuple[tuple, float, Any]] = {}
_warmed: dict[str, tuple | None] = {"version": None}


async def club_version(session: AsyncSession) -> tuple:
    count, last = (await session.execute(
        select(func.count(Match.id), func.max(Match.id)).where(Match.status == MatchStatus.completed)
    )).one()
    players, total = (await session.execute(select(func.count(Player.id), func.sum(Player.rating)))).one()
    return count, last, players, round(total or 0.0, 1)


async def cached(session: AsyncSession, key: tuple, build: Callable[[], Awaitable[Any]],
                 version: tuple | None = None) -> Any:
    """Ответ из кэша (копия — вызывающий может дописывать поля) или собранный заново."""
    if version is None:
        version = await club_version(session)
    hit = _store.get(key)
    now = time.monotonic()
    if hit is not None and hit[0] == version and now - hit[1] < TTL_S:
        return copy.deepcopy(hit[2])
    value = await build()
    if len(_store) >= MAX_ENTRIES and key not in _store:
        _store.pop(next(iter(_store)))
    _store[key] = (version, now, value)
    return copy.deepcopy(value)


def clear() -> None:
    _store.clear()
    _warmed["version"] = None


async def warm(session: AsyncSession) -> bool:
    """Собрать тяжёлые ответы заранее, если данные клуба изменились. True — прогрел."""
    from bot.webapp import api, style_tiers

    version = await club_version(session)
    if version == _warmed["version"]:
        return False
    await style_tiers.club_reference(session)
    players = (await session.execute(select(Player))).scalars().all()
    others = {p.id: next((o for o in players if o.id != p.id), p) for p in players}
    for p in players:
        for viewer in (p, others[p.id]):
            await cached(session, ("player", p.id, viewer.id == p.id),
                         lambda p=p, viewer=viewer: api.player_payload(session, p, viewer), version)
            await asyncio.sleep(0)   # не держать цикл событий: запросы игроков идут между игроками
    await cached(session, ("records",), lambda: api.records_payload(session), version)
    _warmed["version"] = version
    return True


async def warm_forever(session_factory) -> None:
    while True:
        try:
            async with session_factory() as session:
                if await warm(session):
                    logger.info("Mini App: кэш профилей прогрет")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Mini App: прогрев кэша не удался", exc_info=True)
        await asyncio.sleep(WARM_EVERY_S)
