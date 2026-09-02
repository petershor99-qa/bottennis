"""
Личные рекорды.

Не путать с ачивками (achievements.py, фиксированные пороги вроде «3 победы
подряд») и клубными рекордами (leaderboard.py, сравнение между игроками).
Здесь сравнение всегда только с собственной историей игрока: «стало ли
значение больше, чем когда-либо было лично у тебя». Сама флавор-текст-логика
считается заново из истории матчей на каждое подтверждение результата, тем
же способом, что и скрытые ачивки в achievements.py — но с v2.106.0 каждое
срабатывание ДОПОЛНИТЕЛЬНО пишется в PersonalRecordEarned (см. models.py) для
дат: в отличие от ачивок метрику можно бить многократно за карьеру, поэтому
это полная история, не одна строка на метрику.

Общее правило порогов на все метрики: не празднуем самое первое измерение
(значение 0 «до» — сравнивать не с чем), любое улучшение после этого уже
личный рекорд, сколь угодно маленькое. Реализовано одним условием
`before > 0 and now > before` — для всех метрик здесь 0 «до» математически
означает именно «это событие ещё ни разу не случалось», не «плохой результат».
"""
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, PersonalRecordEarned, Player
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


def _check_outcome_agnostic_records(
    matches: list[Match], prior: list[Match],
) -> list[tuple[str, str, float]]:
    """Самый долгий матч (#5) и больше всего очков за матч (#6) — не зависят
    от исхода (победа/поражение/ничья), поэтому общие для всех трёх веток.

    Возвращает (сообщение, ключ_метрики, значение) — ключ/значение нужны
    вызывающему для записи в PersonalRecordEarned (v2.106.0), сам этот хелпер
    не пишет в БД (нет доступа к player_id/match здесь)."""
    records: list[tuple[str, str, float]] = []

    sets_now = max(_sets_count(m) for m in matches)
    sets_before = max((_sets_count(m) for m in prior), default=0)
    if sets_before > 0 and sets_now > sets_before:
        records.append((
            f"🕰 Длиннее, чем «Санта Барбара». {sets_now} партий — твой самый долгий матч.",
            "longest_match", sets_now,
        ))

    pts_now = max(_total_points(m) for m in matches)
    pts_before = max((_total_points(m) for m in prior), default=0)
    if pts_before > 0 and pts_now > pts_before:
        records.append((
            f"📖 Эпичнее, чем «Война и мир». {pts_now} очков разыграно за матч — твой личный рекорд.",
            "most_points", pts_now,
        ))

    return records


def _record_and_extract_messages(
    session: AsyncSession, player_id: int, match_id: int, earned_at,
    records: list[tuple[str, str, float]],
) -> list[str]:
    """Пишет PersonalRecordEarned (v2.106.0) на каждый сработавший рекорд и
    возвращает только тексты — внешний контракт check_personal_records_on_*
    (list[str]) не меняется, вызывающие в match_result.py его не видят."""
    for message, metric, value in records:
        session.add(PersonalRecordEarned(
            player_id=player_id, metric=metric, value=value,
            match_id=match_id, earned_at=earned_at,
        ))
    return [message for message, _, _ in records]


async def check_personal_records_on_win(session: AsyncSession, winner: Player, match: Match) -> list[str]:
    """Личные рекорды победителя после победы (#1, #2, #3, #4, #5, #6, #7)."""
    matches = await _get_matches_asc(session, winner.id)
    if len(matches) < 2:
        return []
    prior = matches[:-1]
    records: list[tuple[str, str, float]] = []

    streak_now = compute_alltime_streak(matches, winner.id)
    streak_before = compute_alltime_streak(prior, winner.id)
    if streak_before > 0 and streak_now > streak_before:
        records.append((
            f"⚡ RAMPAGE! {streak_now} побед подряд — новый личный рекорд.",
            "win_streak", streak_now,
        ))

    noloss_now = _max_no_loss_streak(matches, winner.id)
    noloss_before = _max_no_loss_streak(prior, winner.id)
    if noloss_before > 0 and noloss_now > noloss_before:
        records.append((
            f"🛡 «Ты не пройдёшь!» {noloss_now} матчей без поражений — личный рекорд.",
            "no_loss_streak", noloss_now,
        ))

    day_now = max(_wins_by_day(matches, winner.id).values(), default=0)
    day_before = max(_wins_by_day(prior, winner.id).values(), default=0)
    if day_before > 0 and day_now > day_before:
        records.append((
            f"🌞 День сурка, только выигрышный. {day_now} побед за сегодня — твой лучший день за всю историю.",
            "wins_per_day", day_now,
        ))

    margin_now = max((_set_margin(m) for m in matches if m.winner_id == winner.id), default=0)
    margin_before = max((_set_margin(m) for m in prior if m.winner_id == winner.id), default=0)
    if margin_before > 0 and margin_now > margin_before:
        records.append((
            f"💥 Hasta la vista, разница в {margin_now} очков — твой личный рекорд разгрома.",
            "set_margin", margin_now,
        ))

    records.extend(_check_outcome_agnostic_records(matches, prior))

    vsopp_now = _max_streak_vs_any_opponent(matches, winner.id)
    vsopp_before = _max_streak_vs_any_opponent(prior, winner.id)
    if vsopp_before > 0 and vsopp_now > vsopp_before:
        records.append((
            f"🎯 Ты его личный кошмар. {vsopp_now} побед подряд над одним соперником — рекорд.",
            "streak_vs_opponent", vsopp_now,
        ))

    return _record_and_extract_messages(session, winner.id, match.id, match.completed_at, records)


async def check_personal_records_on_loss(session: AsyncSession, loser: Player, match: Match) -> list[str]:
    """Личные рекорды проигравшего (только #5, #6 — не зависят от исхода)."""
    matches = await _get_matches_asc(session, loser.id)
    if len(matches) < 2:
        return []
    records = _check_outcome_agnostic_records(matches, matches[:-1])
    return _record_and_extract_messages(session, loser.id, match.id, match.completed_at, records)


async def check_personal_records_on_draw(session: AsyncSession, player: Player, match: Match) -> list[str]:
    """Личные рекорды участника ничьей (#2, #5, #6 — серия без поражений
    продолжается ничьей, длина/очки матча не зависят от исхода)."""
    matches = await _get_matches_asc(session, player.id)
    if len(matches) < 2:
        return []
    prior = matches[:-1]
    records: list[tuple[str, str, float]] = []

    noloss_now = _max_no_loss_streak(matches, player.id)
    noloss_before = _max_no_loss_streak(prior, player.id)
    if noloss_before > 0 and noloss_now > noloss_before:
        records.append((
            f"🛡 «Ты не пройдёшь!» {noloss_now} матчей без поражений — личный рекорд.",
            "no_loss_streak", noloss_now,
        ))

    records.extend(_check_outcome_agnostic_records(matches, prior))
    return _record_and_extract_messages(session, player.id, match.id, match.completed_at, records)


async def backfill_personal_records(session: AsyncSession) -> None:
    """Восстанавливает историю личных рекордов из истории матчей (v2.106.0).

    В отличие от backfill_achievements() не привязан к версии — личные
    рекорды никогда не хранились на Player, нечего версионировать. Запускается
    для каждого игрока, у которого есть матчи, но НЕТ вообще ни одной строки в
    PersonalRecordEarned — то есть ровно один раз в жизни игрока; после первой
    строки (от бэкфилла или от реал-тайм check_personal_records_on_*) история
    непрерывна и повторный проход не нужен.

    В отличие от ачивок (флаг да/нет) метрику можно бить многократно за
    карьеру — проходим по матчам хронологически и на КАЖДОМ шаге сравниваем
    значение метрики с максимумом ДО этого матча, тем же правилом порогов
    (before > 0 and now > before), что и в реал-тайм проверках выше. Логика
    веток по исходу дублирует check_personal_records_on_win/loss/draw — тот же
    компромисс, что уже принят в backfill_achievements() vs check_win_achievements:
    реал-тайм проверяет только ПОСЛЕДНИЙ матч, бэкфиллу нужно растущее окно на
    каждом шаге, единую функцию под оба случая было бы сложнее читать.
    """
    players_r = await session.execute(select(Player))
    players = players_r.scalars().all()

    for player in players:
        count_r = await session.execute(
            select(func.count()).select_from(PersonalRecordEarned)
            .where(PersonalRecordEarned.player_id == player.id)
        )
        if count_r.scalar() > 0:
            continue  # уже был бэкфилл или идут реал-тайм записи — не трогаем

        matches = list(reversed(await get_career_matches(session, player.id)))  # по возрастанию даты
        if len(matches) < 2:
            continue

        for i in range(1, len(matches)):
            prior = matches[:i]
            upto_now = matches[: i + 1]
            m = matches[i]
            is_win = m.winner_id == player.id
            is_draw = m.winner_id is None
            records: list[tuple[str, float]] = []

            if is_win:
                streak_now = compute_alltime_streak(upto_now, player.id)
                streak_before = compute_alltime_streak(prior, player.id)
                if streak_before > 0 and streak_now > streak_before:
                    records.append(("win_streak", streak_now))

                day_now = max(_wins_by_day(upto_now, player.id).values(), default=0)
                day_before = max(_wins_by_day(prior, player.id).values(), default=0)
                if day_before > 0 and day_now > day_before:
                    records.append(("wins_per_day", day_now))

                margin_now = max((_set_margin(mm) for mm in upto_now if mm.winner_id == player.id), default=0)
                margin_before = max((_set_margin(mm) for mm in prior if mm.winner_id == player.id), default=0)
                if margin_before > 0 and margin_now > margin_before:
                    records.append(("set_margin", margin_now))

                vsopp_now = _max_streak_vs_any_opponent(upto_now, player.id)
                vsopp_before = _max_streak_vs_any_opponent(prior, player.id)
                if vsopp_before > 0 and vsopp_now > vsopp_before:
                    records.append(("streak_vs_opponent", vsopp_now))

            if is_win or is_draw:
                noloss_now = _max_no_loss_streak(upto_now, player.id)
                noloss_before = _max_no_loss_streak(prior, player.id)
                if noloss_before > 0 and noloss_now > noloss_before:
                    records.append(("no_loss_streak", noloss_now))

            # Outcome-agnostic (#5, #6) — независимо от победы/поражения/ничьей
            sets_now = max(_sets_count(mm) for mm in upto_now)
            sets_before = max((_sets_count(mm) for mm in prior), default=0)
            if sets_before > 0 and sets_now > sets_before:
                records.append(("longest_match", sets_now))

            pts_now = max(_total_points(mm) for mm in upto_now)
            pts_before = max((_total_points(mm) for mm in prior), default=0)
            if pts_before > 0 and pts_now > pts_before:
                records.append(("most_points", pts_now))

            for metric, value in records:
                session.add(PersonalRecordEarned(
                    player_id=player.id, metric=metric, value=value,
                    match_id=m.id, earned_at=m.completed_at,
                ))

    await session.commit()
