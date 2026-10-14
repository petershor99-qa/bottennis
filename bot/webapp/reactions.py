"""
Реакции на матчи в Mini App (v2.169.0, эксперимент).

Игрок может отметить завершённый матч реакцией — «огонь», «класс», «смешно»,
«какашка» (с v2.171.0).
Повторное нажатие снимает её. С v2.174.0 у игрока на матче одна реакция:
нажатие на другую заменяет прежнюю. Реакции живут только в приложении: бот о них
не пишет и никого не уведомляет — по требованию владельца новых уведомлений
у игроков быть не должно. Если эксперимент не приживётся, достаточно убрать
кнопки на экране матча; таблица `match_reactions` ничему не мешает.
"""
import logging
from html import escape as h

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
# Эмодзи вида — для строки «Матч, который обсуждали» в итогах дня (v2.174.0);
# в приложении text.plain() меняет их обратно на слова
REACTION_EMOJI: dict[str, str] = {"fire": "🔥", "like": "👍", "lol": "😂", "poo": "💩"}
DISCUSSED_MIN_REACTIONS = 2


async def most_discussed_line(session: AsyncSession, matches: list[Match], name_map: dict[int, str]) -> str | None:
    """«💬 Матч, который обсуждали: Алиса — Боб (2:1) · 🔥 3 · 💩 1» — матч дня
    с наибольшим числом реакций, при равенстве более поздний. None, если у
    этого матча меньше DISCUSSED_MIN_REACTIONS реакций (v2.174.0)."""
    if not matches:
        return None
    by_id = {m.id: m for m in matches}
    counts: dict[int, dict[str, int]] = {}
    for match_id, kind, n in (await session.execute(
        select(MatchReaction.match_id, MatchReaction.kind, func.count())
        .where(MatchReaction.match_id.in_(list(by_id)))
        .group_by(MatchReaction.match_id, MatchReaction.kind)
    )).all():
        counts.setdefault(match_id, {})[kind] = n
    if not counts:
        return None
    order = {m.id: i for i, m in enumerate(sorted(matches, key=lambda m: (m.completed_at or m.created_at, m.id)))}
    best_id = max(counts, key=lambda mid: (sum(counts[mid].values()), order[mid]))
    if sum(counts[best_id].values()) < DISCUSSED_MIN_REACTIONS:
        return None
    m = by_id[best_id]
    sets = m.sets_data or []
    won = sum(1 for s in sets if s["w"] > s["l"])
    lost = sum(1 for s in sets if s["l"] > s["w"])
    if m.winner_id is not None and m.winner_id == m.challenged_id:
        first, second = m.challenged_id, m.challenger_id
    else:
        first, second = m.challenger_id, m.challenged_id
    score = f" ({won}:{lost})" if sets else ""
    marks = " · ".join(
        f"{REACTION_EMOJI[kind]} {counts[best_id][kind]}" for kind in REACTIONS if counts[best_id].get(kind)
    )
    return (
        f"💬 <b>Матч, который обсуждали:</b> {h(name_map.get(first, '?'))} — "
        f"{h(name_map.get(second, '?'))}{score} · {marks}"
    )


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
    """Поставить, заменить или снять реакцию. У игрока на матче одна реакция
    (v2.174.0): та же — снимается, другая — заменяет прежнюю. None — нет такого
    завершённого матча или вида реакции. Двойной тап (гонка двух запросов) не
    даёт дубля: уникальный индекс (match_id, player_id) + откат."""
    if kind not in REACTIONS:
        return None
    exists = (await session.execute(
        select(Match.id).where(Match.id == match_id, Match.status == MatchStatus.completed)
    )).scalar_one_or_none()
    if exists is None:
        return None
    try:
        current = (await session.execute(
            select(MatchReaction.kind).where(
                MatchReaction.match_id == match_id, MatchReaction.player_id == viewer.id,
            )
        )).scalars().all()
        await session.execute(
            delete(MatchReaction).where(
                MatchReaction.match_id == match_id, MatchReaction.player_id == viewer.id,
            )
        )
        if kind not in current:
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
