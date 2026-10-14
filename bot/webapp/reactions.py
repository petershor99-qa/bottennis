"""
Реакции на матчи в Mini App (v2.169.0, эксперимент).

Игрок может отметить завершённый матч реакцией — «огонь», «класс», «смешно»,
«какашка» (с v2.171.0).
Повторное нажатие снимает её. Реакции живут только в приложении: бот о них
не пишет и никого не уведомляет — по требованию владельца новых уведомлений
у игроков быть не должно. Если эксперимент не приживётся, достаточно убрать
кнопки на экране матча; таблица `match_reactions` ничему не мешает.
"""
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, MatchReaction, MatchStatus, Player

logger = logging.getLogger(__name__)

# kind -> (иконка из static/icons.svg, подпись для экранного диктора)
REACTIONS: dict[str, tuple[str, str]] = {
    "fire": ("flame", "Огонь"),
    "like": ("thumb-up", "Класс"),
    "lol": ("mood-happy", "Смешно"),
    "poo": ("poo", "Какашка"),  # v2.171.0, по просьбе владельца
}


async def reactions_for(
    session: AsyncSession, match_ids: list[int], viewer_id: int, with_names: bool = False,
) -> dict[int, list[dict]]:
    """Реакции на матчи: {match_id: [{kind, icon, title, count, mine}]} — все
    виды по порядку, включая нулевые (экран матча показывает их кнопками).
    with_names — ещё и `who`: имена поставивших в порядке реакций (v2.173.0,
    экран матча; видно только внутри клуба, уведомлений нет)."""
    if not match_ids:
        return {}
    counts: dict[tuple[int, str], int] = {}
    for match_id, kind, n in (await session.execute(
        select(MatchReaction.match_id, MatchReaction.kind, func.count())
        .where(MatchReaction.match_id.in_(match_ids))
        .group_by(MatchReaction.match_id, MatchReaction.kind)
    )).all():
        counts[(match_id, kind)] = n
    mine = set((await session.execute(
        select(MatchReaction.match_id, MatchReaction.kind)
        .where(MatchReaction.match_id.in_(match_ids), MatchReaction.player_id == viewer_id)
    )).all())
    names: dict[tuple[int, str], list[str]] = {}
    if with_names:
        for match_id, kind, name in (await session.execute(
            select(MatchReaction.match_id, MatchReaction.kind, Player.display_name)
            .join(Player, Player.id == MatchReaction.player_id)
            .where(MatchReaction.match_id.in_(match_ids))
            .order_by(MatchReaction.created_at, MatchReaction.id)
        )).all():
            names.setdefault((match_id, kind), []).append(name)
    out: dict[int, list[dict]] = {}
    for mid in match_ids:
        items = []
        for kind, (icon, title) in REACTIONS.items():
            item = {"kind": kind, "icon": icon, "title": title,
                    "count": counts.get((mid, kind), 0), "mine": (mid, kind) in mine}
            if with_names:
                item["who"] = names.get((mid, kind), [])
            items.append(item)
        out[mid] = items
    return out


def feed_counts(reactions: list[dict]) -> list[dict]:
    """Для ленты — только виды, у которых есть хотя бы одна реакция."""
    return [{"icon": r["icon"], "count": r["count"]} for r in reactions if r["count"]]


async def toggle_reaction(session: AsyncSession, match_id: int, viewer: Player, kind: str) -> list[dict] | None:
    """Поставить или снять реакцию. None — нет такого завершённого матча или
    вида реакции. Двойной тап (гонка двух запросов) не даёт дубля: уникальный
    индекс + откат."""
    if kind not in REACTIONS:
        return None
    exists = (await session.execute(
        select(Match.id).where(Match.id == match_id, Match.status == MatchStatus.completed)
    )).scalar_one_or_none()
    if exists is None:
        return None
    try:
        removed = await session.execute(
            delete(MatchReaction).where(
                MatchReaction.match_id == match_id,
                MatchReaction.player_id == viewer.id,
                MatchReaction.kind == kind,
            )
        )
        if not removed.rowcount:
            session.add(MatchReaction(match_id=match_id, player_id=viewer.id, kind=kind))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.info("Реакция %s на матч %s уже стоит (двойной тап)", kind, match_id)
    except OperationalError:
        # База занята записью бота дольше busy-timeout — реакция не сохранится,
        # экран покажет прежнее состояние; матчи и рейтинг важнее
        await session.rollback()
        logger.warning("Реакция %s на матч %s не записана: база занята", kind, match_id)
    return (await reactions_for(session, [match_id], viewer.id, with_names=True))[match_id]
