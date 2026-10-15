"""
Меню клуба по использованию (v2.192.0). Плиток на главной стало больше десятка
(итоги, месяц, рекорды, матчи, активность, трон, летопись, счета, «Когда
играют», «Кто с кем», гонка…), и нужные терялись среди редких. Порядок теперь
по тому, что клуб реально открывает в приложении за последние 30 дней
(события `app_<экран>` счётчика, v2.166.0): первые OFTEN плиток видны сразу,
остальные — под «Ещё». Пока данных мало (меньше MIN_EVENTS открытий плиток),
порядок прежний. Это мягкий шаг к этапу 4 дорожной карты (уборка экранов
по данным) — ничего не удаляется.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import UsageEvent

OFTEN = 6
WINDOW_DAYS = 30
MIN_EVENTS = 20


def screen_action(route: str) -> str:
    """Действие счётчика для адреса плитки — как screenName() на странице."""
    return "app_" + (route.replace("/", "_") or "home")


def rank_links(links: list[dict], counts: dict[str, int]) -> list[dict]:
    """Плитки по убыванию открытий (при равенстве — прежний порядок); «more» —
    под «Ещё». Мало данных — прежний порядок, «Ещё» тоже после первых OFTEN."""
    total = sum(counts.get(screen_action(link["route"]), 0) for link in links)
    order = list(range(len(links)))
    if total >= MIN_EVENTS:
        order.sort(key=lambda i: (-counts.get(screen_action(links[i]["route"]), 0), i))
    out = []
    for place, i in enumerate(order):
        item = dict(links[i])
        if place >= OFTEN:
            item["more"] = True
        out.append(item)
    return out


async def usage_counts(session: AsyncSession) -> dict[str, int]:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=WINDOW_DAYS)
    rows = (await session.execute(
        select(UsageEvent.action, func.count(UsageEvent.id))
        .where(UsageEvent.created_at >= since, UsageEvent.action.like("app\\_%", escape="\\"))
        .group_by(UsageEvent.action)
    )).all()
    return {action: n for action, n in rows}


async def ranked_links(session: AsyncSession, links: list[dict]) -> list[dict]:
    return rank_links(links, await usage_counts(session))
