"""
Голосование «Итоги года: неформальные звания» (v2.134.0, этап 3 дорожной
карты из CLAUDE.md). Раз в год, 21-30 декабря, игроки голосуют за 6
номинаций, которые нельзя посчитать формулой — бот сам рассылает бюллетень,
напоминает, закрывает голосование по времени и объявляет победителей.

Номинации — фиксированный список ниже, менять одной правкой
YEAR_VOTE_NOMINATIONS. Голоса анонимны: в результатах виден только
победитель и число голосов, кто за кого голосовал — нигде не показывается,
даже админу.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape as h

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, MatchStatus, Player, YearVote
from bot.utils import MSK_OFFSET, pluralize_votes


@dataclass(frozen=True)
class YearVoteNomination:
    id: str
    emoji: str
    name: str
    hint: str | None


# Названия — дословно от пользователя (согласовано 2026-09-26). Порядок этого
# списка — порядок кнопок в бюллетене; менять состав/порядок одной правкой здесь.
YEAR_VOTE_NOMINATIONS: list[YearVoteNomination] = [
    YearVoteNomination("gentleman", "🤝", "Джентльмен года", "самый честный и спортивный"),
    YearVoteNomination("excuse", "💬", "Отмазка года", "лучшее оправдание поражения"),
    YearVoteNomination("commentator", "🎙", "Комментатор года", "больше всех болтает у стола"),
    YearVoteNomination("loudest", "📢", "Самый орущий у стола", None),
    YearVoteNomination("magnet", "🧲", "Все хотят посамбоваться", None),
    YearVoteNomination("toughest", "🧱", "С кем сложнее всего", None),
]
_NOMINATIONS_BY_ID = {n.id: n for n in YEAR_VOTE_NOMINATIONS}


def get_nomination(nomination_id: str) -> YearVoteNomination | None:
    return _NOMINATIONS_BY_ID.get(nomination_id)


def is_voting_open(now_msk: datetime) -> bool:
    """Окно голосования: 21 декабря 10:00 МСК — 30 декабря 12:00 МСК (правая
    граница НЕ включена), целиком внутри одного календарного декабря — год
    голосования всегда совпадает с now_msk.year, кросс-годовой границы нет."""
    start = now_msk.replace(month=12, day=21, hour=10, minute=0, second=0, microsecond=0)
    end = now_msk.replace(month=12, day=30, hour=12, minute=0, second=0, microsecond=0)
    return start <= now_msk < end


def _full_year_bounds_utc(year: int) -> tuple[datetime, datetime]:
    """Весь календарный год (по МСК), в naive UTC — как хранятся даты в БД.
    Не путать с scheduler._year_bounds_msk (тот — срез ДО момента запуска
    «Итогов года»): для допуска к голосованию нужен целый год, а не «до сейчас»,
    иначе игрок, сыгравший только в январе, потерял бы право голосовать в декабре
    того же года просто из-за формы среза."""
    start_msk = datetime(year, 1, 1)
    end_msk = datetime(year + 1, 1, 1)
    return start_msk - MSK_OFFSET, end_msk - MSK_OFFSET


async def get_eligible_player_ids(session: AsyncSession, year: int) -> set[int]:
    """Игроки с ≥1 завершённым матчем в году `year` (по МСК) — та же граница
    допуска, что и у видимости в лидерборде (0 матчей — не в счёт)."""
    start_utc, end_utc = _full_year_bounds_utc(year)
    r = await session.execute(
        select(Match.challenger_id, Match.challenged_id).where(
            Match.status == MatchStatus.completed,
            Match.completed_at >= start_utc,
            Match.completed_at < end_utc,
        )
    )
    ids: set[int] = set()
    for challenger_id, challenged_id in r.all():
        ids.add(challenger_id)
        ids.add(challenged_id)
    return ids


async def get_nominees(
    session: AsyncSession,
    year: int,
    exclude_id: int | None = None,
    eligible_ids: set[int] | None = None,
) -> list[Player]:
    """Допущенные кандидаты года, без exclude_id (голосующий за себя) —
    отсортированы по имени для стабильного порядка кнопок.

    eligible_ids — если уже посчитан вызывающим (например, _guard в
    year_vote.py уже сходил за ним ради проверки допуска), передаём готовый
    набор вместо повторного скана всех матчей года на каждый тап кнопки."""
    ids = set(eligible_ids) if eligible_ids is not None else await get_eligible_player_ids(session, year)
    ids.discard(exclude_id)
    if not ids:
        return []
    r = await session.execute(select(Player).where(Player.id.in_(ids)))
    players = r.scalars().all()
    return sorted(players, key=lambda p: p.display_name.lower())


async def get_voter_choices(
    session: AsyncSession, year: int, voter_id: int,
) -> dict[str, int | None]:
    """Текущий выбор voter_id по каждой номинации года — None, если ещё не
    выбрано. Всегда все 6 ключей, даже если голосов пока вообще нет."""
    r = await session.execute(
        select(YearVote).where(YearVote.year == year, YearVote.voter_id == voter_id)
    )
    picks = {v.nomination: v.nominee_id for v in r.scalars().all()}
    return {n.id: picks.get(n.id) for n in YEAR_VOTE_NOMINATIONS}


async def has_all_nominations_filled(session: AsyncSession, year: int, voter_id: int) -> bool:
    choices = await get_voter_choices(session, year, voter_id)
    return all(v is not None for v in choices.values())


async def set_vote(
    session: AsyncSession, year: int, nomination: str, voter_id: int, nominee_id: int,
) -> None:
    """Записывает/перезаписывает голос — UPDATE существующей строки вместо
    второй, уникальный индекс (year, nomination, voter_id) гарантирует
    единственность на БД-уровне тоже."""
    r = await session.execute(
        select(YearVote).where(
            YearVote.year == year,
            YearVote.nomination == nomination,
            YearVote.voter_id == voter_id,
        )
    )
    existing = r.scalar_one_or_none()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if existing:
        existing.nominee_id = nominee_id
        existing.updated_at = now
    else:
        session.add(YearVote(
            year=year, nomination=nomination, voter_id=voter_id,
            nominee_id=nominee_id, updated_at=now,
        ))
    await session.flush()


async def compute_results(
    session: AsyncSession, year: int,
) -> tuple[list[tuple[YearVoteNomination, list[int], int]], int]:
    """(результаты по номинациям, всего голосов за год). Результат по
    номинации — (определение, id победителей [пусто, если голосов не было;
    больше одного — делят звание], число голосов у победителя)."""
    r = await session.execute(select(YearVote).where(YearVote.year == year))
    votes = r.scalars().all()

    # tally.setdefault, а не {n.id: {} for n in YEAR_VOTE_NOMINATIONS} — голос
    # мог быть отдан под id номинации, которой к моменту подсчёта уже нет в
    # списке (список меняется одной правкой прямо в этом файле, а голоса лежат
    # в БД неделю до подсчёта). Прямое tally[v.nomination] уронило бы KeyError
    # и весь send_year_end_combo — осиротевший голос просто не попадёт ни в
    # одну из результирующих номинаций ниже (никто не проголосовал бы в нём
    # без него — не хуже, чем если бы голоса не было вовсе).
    tally: dict[str, dict[int, int]] = {}
    for v in votes:
        counts = tally.setdefault(v.nomination, {})
        counts[v.nominee_id] = counts.get(v.nominee_id, 0) + 1

    results = []
    for nomination in YEAR_VOTE_NOMINATIONS:
        counts = tally.get(nomination.id, {})
        if not counts:
            results.append((nomination, [], 0))
            continue
        top = max(counts.values())
        winners = [pid for pid, c in counts.items() if c == top]
        results.append((nomination, winners, top))
    return results, len(votes)


def render_bulletin(year: int, choices: dict[str, int | None], name_map: dict[int, str]) -> str:
    lines = [f"🗳 <b>Голосование: неформальные звания {year}</b>", ""]
    for n in YEAR_VOTE_NOMINATIONS:
        nominee_id = choices.get(n.id)
        if nominee_id is not None and nominee_id in name_map:
            lines.append(f"{n.emoji} {n.name} — ✅ <b>{h(name_map[nominee_id])}</b>")
        else:
            lines.append(f"{n.emoji} {n.name} — <i>не выбрано</i>")
    lines.append("")
    lines.append("Нажми на номинацию, чтобы выбрать или сменить кандидата.")
    return "\n".join(lines)


def render_nomination_screen(nomination: YearVoteNomination) -> str:
    lines = [f"{nomination.emoji} <b>{nomination.name}</b>"]
    if nomination.hint:
        lines.append(f"<i>{nomination.hint}</i>")
    lines.append("")
    lines.append("Выбери кандидата:")
    return "\n".join(lines)


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " и " + names[-1]


def render_results(
    year: int,
    results: list[tuple[YearVoteNomination, list[int], int]],
    name_map: dict[int, str],
) -> str:
    lines = [f"🏆 <b>Результаты голосования: неформальные звания {year}</b>", ""]
    for nomination, winners, count in results:
        if not winners:
            lines.append(f"{nomination.emoji} {nomination.name} — <i>никто не проголосовал</i>")
        elif len(winners) == 1:
            name = h(name_map.get(winners[0], "?"))
            lines.append(
                f"{nomination.emoji} <b>{nomination.name}</b> — <b>{name}</b> "
                f"({pluralize_votes(count)})"
            )
        else:
            names = _join_names([h(name_map.get(pid, "?")) for pid in winners])
            lines.append(
                f"{nomination.emoji} <b>{nomination.name}</b> — <b>{names}</b> делят звание "
                f"({pluralize_votes(count)} у каждого)"
            )
    return "\n".join(lines)
