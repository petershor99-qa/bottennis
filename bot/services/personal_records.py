"""
Личные рекорды.

Не путать с ачивками (achievements.py, фиксированные пороги вроде «3 победы
подряд») и клубными рекордами (leaderboard.py, сравнение между игроками).
Здесь сравнение всегда только с собственной историей игрока: «стало ли
значение больше, чем когда-либо было лично у тебя». Не хранятся в БД —
считаются заново из истории матчей на каждое подтверждение результата, тем
же способом, что и скрытые ачивки в achievements.py.

Общее правило порогов на все метрики: не празднуем самое первое измерение
(значение 0 «до» — сравнивать не с чем), любое улучшение после этого уже
личный рекорд, сколь угодно маленькое. Реализовано одним условием
`before > 0 and now > before` — для всех метрик здесь 0 «до» математически
означает именно «это событие ещё ни разу не случалось», не «плохой результат».
"""
from collections import Counter

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, Player
from bot.utils import MSK_OFFSET, as_naive, compute_alltime_streak, get_career_matches


async def _get_matches_asc(session: AsyncSession, player_id: int) -> list[Match]:
    """Карьерная история в хронологии (от старых к новым), включая текущий
    матч — он уже закоммичен к моменту вызова этих проверок."""
    return list(reversed(await get_career_matches(session, player_id)))


def _sets_count(match: Match) -> int:
    return len(match.sets_data) if match.sets_data else 0


def _total_points(match: Match) -> int:
    if not match.sets_data:
        return 0
    return sum(s["w"] + s["l"] for s in match.sets_data)


def _set_margin(match: Match) -> int:
    """Разница очков победитель-проигравший по сумме всех партий (sets_data
    хранится в перспективе победителя)."""
    if not match.sets_data:
        return 0
    return sum(s["w"] - s["l"] for s in match.sets_data)


def _max_no_loss_streak(matches: list[Match], player_id: int) -> int:
    """Лучшая когда-либо серия без поражений (победа ИЛИ ничья, рвётся
    только поражением) — та же логика, что у clube-wide-версии в
    scheduler.py, но для одного игрока."""
    best = cur = 0
    for m in matches:
        if m.winner_id is None or m.winner_id == player_id:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _wins_by_day(matches: list[Match], player_id: int) -> Counter:
    """Число побед по дням (МСК)."""
    counter: Counter = Counter()
    for m in matches:
        if m.winner_id == player_id and m.completed_at:
            day = (as_naive(m.completed_at) + MSK_OFFSET).date()
            counter[day] += 1
    return counter


def _max_streak_vs_any_opponent(matches: list[Match], player_id: int) -> int:
    """Лучшая когда-либо серия ПОБЕД подряд против ОДНОГО конкретного
    соперника (максимум по всем оппонентам) — не путать с общей серией побед
    (та не привязана к конкретному человеку). Ничья тоже рвёт серию против
    этого соперника, как и поражение — это серия побед, не серия без поражений.
    """
    per_opponent: dict[int, int] = {}
    best = 0
    for m in matches:
        opp_id = m.challenged_id if m.challenger_id == player_id else m.challenger_id
        if m.winner_id == player_id:
            per_opponent[opp_id] = per_opponent.get(opp_id, 0) + 1
            best = max(best, per_opponent[opp_id])
        else:
            per_opponent[opp_id] = 0
    return best


def _check_outcome_agnostic_records(matches: list[Match], prior: list[Match]) -> list[str]:
    """Самый долгий матч (#5) и больше всего очков за матч (#6) — не зависят
    от исхода (победа/поражение/ничья), поэтому общие для всех трёх веток."""
    messages: list[str] = []

    sets_now = max(_sets_count(m) for m in matches)
    sets_before = max((_sets_count(m) for m in prior), default=0)
    if sets_before > 0 and sets_now > sets_before:
        messages.append(f"🕰 Длиннее, чем «Санта Барбара». {sets_now} партий — твой самый долгий матч.")

    pts_now = max(_total_points(m) for m in matches)
    pts_before = max((_total_points(m) for m in prior), default=0)
    if pts_before > 0 and pts_now > pts_before:
        messages.append(f"📖 Эпичнее, чем «Война и мир». {pts_now} очков разыграно за матч — твой личный рекорд.")

    return messages


async def check_personal_records_on_win(session: AsyncSession, winner: Player, match: Match) -> list[str]:
    """Личные рекорды победителя после победы (#1, #2, #3, #4, #5, #6, #7)."""
    matches = await _get_matches_asc(session, winner.id)
    if len(matches) < 2:
        return []
    prior = matches[:-1]
    messages: list[str] = []

    streak_now = compute_alltime_streak(matches, winner.id)
    streak_before = compute_alltime_streak(prior, winner.id)
    if streak_before > 0 and streak_now > streak_before:
        messages.append(f"⚡ RAMPAGE! {streak_now} побед подряд — новый личный рекорд.")

    noloss_now = _max_no_loss_streak(matches, winner.id)
    noloss_before = _max_no_loss_streak(prior, winner.id)
    if noloss_before > 0 and noloss_now > noloss_before:
        messages.append(f"🛡 «Ты не пройдёшь!» {noloss_now} матчей без поражений — личный рекорд.")

    day_now = max(_wins_by_day(matches, winner.id).values(), default=0)
    day_before = max(_wins_by_day(prior, winner.id).values(), default=0)
    if day_before > 0 and day_now > day_before:
        messages.append(f"🌞 День сурка, только выигрышный. {day_now} побед за сегодня — твой лучший день за всю историю.")

    margin_now = max((_set_margin(m) for m in matches if m.winner_id == winner.id), default=0)
    margin_before = max((_set_margin(m) for m in prior if m.winner_id == winner.id), default=0)
    if margin_before > 0 and margin_now > margin_before:
        messages.append(f"💥 Hasta la vista, разница в {margin_now} очков — твой личный рекорд разгрома.")

    messages.extend(_check_outcome_agnostic_records(matches, prior))

    vsopp_now = _max_streak_vs_any_opponent(matches, winner.id)
    vsopp_before = _max_streak_vs_any_opponent(prior, winner.id)
    if vsopp_before > 0 and vsopp_now > vsopp_before:
        messages.append(f"🎯 Ты его личный кошмар. {vsopp_now} побед подряд над одним соперником — рекорд.")

    return messages


async def check_personal_records_on_loss(session: AsyncSession, loser: Player, match: Match) -> list[str]:
    """Личные рекорды проигравшего (только #5, #6 — не зависят от исхода)."""
    matches = await _get_matches_asc(session, loser.id)
    if len(matches) < 2:
        return []
    return _check_outcome_agnostic_records(matches, matches[:-1])


async def check_personal_records_on_draw(session: AsyncSession, player: Player, match: Match) -> list[str]:
    """Личные рекорды участника ничьей (#2, #5, #6 — серия без поражений
    продолжается ничьей, длина/очки матча не зависят от исхода)."""
    matches = await _get_matches_asc(session, player.id)
    if len(matches) < 2:
        return []
    prior = matches[:-1]
    messages: list[str] = []

    noloss_now = _max_no_loss_streak(matches, player.id)
    noloss_before = _max_no_loss_streak(prior, player.id)
    if noloss_before > 0 and noloss_now > noloss_before:
        messages.append(f"🛡 «Ты не пройдёшь!» {noloss_now} матчей без поражений — личный рекорд.")

    messages.extend(_check_outcome_agnostic_records(matches, prior))
    return messages
