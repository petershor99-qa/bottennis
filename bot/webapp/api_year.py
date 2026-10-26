"""
«Твой год» в Mini App (v2.214.0): год игрока карточками-сторис, как «Твой месяц»
(тот же рендер и те же виды карточек, см. api_month). Доступен с 1 декабря и
после конца года; на лету по базе, без уведомлений. На главной карточка
«Твой 2026 готов» с 1 декабря по 10 января.
"""
from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AchievementEarned, PersonalRecordEarned, Player
from bot.services.achievements import ACHIEVEMENTS_MAP
from bot.utils import MSK_OFFSET, _ru_plural, match_rating_delta
from bot.webapp.achievement_icons import ACHIEVEMENT_ICONS
from bot.webapp.api import _club_match, _person, _signed, achievement_tier
from bot.webapp.api_club import _now
from bot.webapp.api_day import _matches_between, _standings_at
from bot.webapp.api_month import (
    MONTHS_NOM,
    MOOD_MIN,
    MOOD_ORDER,
    _day_card,
    _days,
    _numbers,
    _player_matches,
    _record,
    match_mood,
)

YEAR_OPEN = (12, 1)        # с какого дня года доступен «Твой год»
READY_UNTIL = (1, 10)      # карточка на главной — до 10 января следующего года
SERIES_MAX = 60            # точек на графике рейтинга за год
YEAR_MOOD_TITLES = {"comeback": "Год камбэков", "rally": "Год отыгрышей", "blowout": "Год разгромов",
                    "deuce": "Год дьюсов", "boss": "Год боссфайтов"}


def _year_start(year: int) -> datetime:
    """Начало года по МСК в UTC-naive."""
    return datetime(year, 1, 1) - MSK_OFFSET


def year_open(today: date, year: int) -> bool:
    """Год открыт: прошедший или текущий начиная с 1 декабря."""
    return year < today.year or (year == today.year and (today.month, today.day) >= YEAR_OPEN)


def ready_year(today: date) -> int | None:
    """За какой год показывать «Твой N готов» на главной сегодня (None — не показывать)."""
    if (today.month, today.day) >= YEAR_OPEN:
        return today.year
    if (today.month, today.day) <= READY_UNTIL:
        return today.year - 1
    return None


def year_character(matches: list, pid: int) -> tuple[str, int] | None:
    """Самый частый характер выигранных матчей года: от MOOD_MIN и больше трети."""
    counts: Counter = Counter()
    for m in matches:
        mood = match_mood(m)
        if mood and (m.winner_id == pid or mood == "boss"):
            counts[mood] += 1
    if not counts:
        return None
    best = max(counts, key=lambda k: (counts[k], -MOOD_ORDER.index(k)))
    n = counts[best]
    return (best, n) if n >= MOOD_MIN and n * 3 > len(matches) else None


async def year_ready(session: AsyncSession, player: Player) -> dict | None:
    """Карточка на главной с 1 декабря по 10 января."""
    today = (_now() + MSK_OFFSET).date()
    year = ready_year(today)
    if year is None:
        return None
    if not await _player_matches(session, player.id, _year_start(year), _year_start(year + 1)):
        return None
    return {"ym": f"year{year}", "route": f"year/{year}", "title": f"Твой {year} готов",
            "sub": "Год в карточках — посмотри, как он прошёл"}


def _nav(year: int, today: date, first_year: int | None) -> dict:
    return {"base": "year", "ym": str(year), "label": str(year),
            "prev": str(year - 1) if first_year is not None and year - 1 >= first_year else None,
            "next": str(year + 1) if year_open(today, year + 1) else None}


async def year_payload(session: AsyncSession, player: Player, ys: str | None) -> dict | None:
    today = (_now() + MSK_OFFSET).date()
    if ys:
        if len(ys) != 4 or not ys.isdigit():
            return None
        year = int(ys)
        if year < 2000 or year > today.year:
            return None
    else:
        year = today.year if year_open(today, today.year) else today.year - 1
    pid = player.id
    first = (await _player_matches(session, pid, datetime(2000, 1, 1), _now() + timedelta(seconds=1)))[:1]
    first_year = (first[0].completed_at + MSK_OFFSET).year if first else None
    nav = _nav(year, today, first_year)
    title = f"Твой {year}"
    if not year_open(today, year):
        return {"title": title, **nav, "cards": [], "empty": f"{year} откроется 1 декабря."}
    start, end = _year_start(year), _year_start(year + 1)
    matches = await _player_matches(session, pid, start, end)
    if not matches:
        return {"title": title, **nav, "cards": [], "empty": f"В {year} ты ещё не играл."}

    w, lost, d = _record(matches, pid)
    cards: list[dict] = [{
        "kind": "intro", "title": title, "big": str(len(matches)),
        "big_label": _ru_plural(len(matches), "матч", "матча", "матчей").split(" ", 1)[-1],
        "lines": [f"{_ru_plural(w, 'победа', 'победы', 'побед')} · "
                  f"{_ru_plural(lost, 'поражение', 'поражения', 'поражений')}" + (f" · ничьих {d}" if d else ""),
                  f"Побед {round(w / len(matches) * 100)}%"]}]

    # Рейтинг за год: откат от текущего рейтинга
    later = await _player_matches(session, pid, end, _now() + timedelta(days=1))
    after_year = player.rating - sum(match_rating_delta(m, pid) for m in later)
    delta = sum(match_rating_delta(m, pid) for m in matches)
    series, value = [], after_year - delta
    for m in matches:
        value += match_rating_delta(m, pid)
        series.append(round(value, 1))
    step = max(1, -(-len(series) // SERIES_MAX))
    sampled = series[::step]
    if sampled[-1] != series[-1]:
        sampled.append(series[-1])
    cards.append({"kind": "rating", "title": "Рейтинг", "big": _signed(round(delta, 1)), "up": delta >= 0,
                  "lines": [f"{after_year - delta:.1f} → {after_year:.1f}"], "series": sampled})

    # Месяцы: лучший по приросту рейтинга и самый активный
    by_month: dict[int, list] = {}
    for m in matches:
        by_month.setdefault((m.completed_at + MSK_OFFSET).month, []).append(m)
    gain = {mo: sum(match_rating_delta(m, pid) for m in ms) for mo, ms in by_month.items()}
    best_month = max(gain, key=lambda mo: (round(gain[mo], 1), -mo))
    if gain[best_month] > 0:
        bw, _bl, _bd = _record(by_month[best_month], pid)
        cards.append({"kind": "month", "title": "Лучший месяц", "big": _signed(round(gain[best_month], 1)), "up": True,
                      "lines": [MONTHS_NOM[best_month - 1].capitalize(),
                                f"{_ru_plural(len(by_month[best_month]), 'матч', 'матча', 'матчей')} · "
                                f"{_ru_plural(bw, 'победа', 'победы', 'побед')}"]})

    days = _days(matches, pid)
    numbers = _numbers(matches, pid)
    busiest = max(by_month, key=lambda mo: (len(by_month[mo]), -mo))
    numbers.append(f"Самый активный месяц: {MONTHS_NOM[busiest - 1]} — "
                   f"{_ru_plural(len(by_month[busiest]), 'матч', 'матча', 'матчей')}")
    numbers.append(f"Игровых дней: {len(days)}")
    cards.append({"kind": "numbers", "title": "Год в цифрах", "lines": numbers})
    best_day = max(days, key=lambda dd: (round(days[dd][0], 1), dd))
    if days[best_day][0] > 0:
        cards.append(_day_card("Лучший день", best_day, days[best_day], True))
    worst_day = min(days, key=lambda dd: (round(days[dd][0], 1), dd))
    if days[worst_day][0] < 0 and worst_day != best_day:
        cards.append(_day_card("Худший день", worst_day, days[worst_day], False))

    wins = [m for m in matches if m.winner_id == pid and m.rating_change is not None]
    if wins:
        best = max(wins, key=lambda m: (m.rating_change, m.completed_at))
        cards.append({"kind": "match", "title": "Лучший матч", "match": _club_match(best),
                      "big": f"+{best.rating_change:.1f}", "up": True,
                      "lines": [(best.completed_at + MSK_OFFSET).strftime("%d.%m")]})

    opp: dict[int, list] = {}
    for m in matches:
        opp.setdefault(m.challenged_id if m.challenger_id == pid else m.challenger_id, []).append(m)
    rival_id = max(opp, key=lambda o: (len(opp[o]), opp[o][-1].completed_at))
    rw, rl, rd = _record(opp[rival_id], pid)
    rival = opp[rival_id][0].challenged if opp[rival_id][0].challenger_id == pid else opp[rival_id][0].challenger
    cards.append({"kind": "rival", "title": "Чаще всего с", "player": _person(rival),
                  "big": f"{rw}:{rl}", "up": rw >= rl,
                  "lines": [_ru_plural(len(opp[rival_id]), "встреча", "встречи", "встреч")
                            + (f", ничьих {rd}" if rd else "")]})

    character = year_character(matches, pid)
    if character:
        cards.append({"kind": "character", "title": "Характер года", "mood": character[0],
                      "stamp": YEAR_MOOD_TITLES[character[0]],
                      "lines": [f"{_ru_plural(character[1], 'матч', 'матча', 'матчей')} из {len(matches)}"]})

    rows = (await session.execute(
        select(AchievementEarned).where(AchievementEarned.player_id == pid,
                                        AchievementEarned.earned_at >= start, AchievementEarned.earned_at < end)
        .order_by(AchievementEarned.earned_at))).scalars().all()
    achs = [ACHIEVEMENTS_MAP[r.achievement_id] for r in rows if r.achievement_id in ACHIEVEMENTS_MAP]
    if achs:
        cards.append({"kind": "achievements", "title": "Ачивки года", "big": str(len(achs)),
                      "big_label": _ru_plural(len(achs), "ачивка", "ачивки", "ачивок").split(" ", 1)[-1],
                      "items": [{"name": a.name, "icon": ACHIEVEMENT_ICONS.get(a.id, "trophy"),
                                 "tier": achievement_tier(a.id)} for a in achs[:6]]})

    from bot.webapp.api_personal import RECORD_TITLES

    best_records: dict[str, float] = {}
    for r in (await session.execute(select(PersonalRecordEarned).where(
            PersonalRecordEarned.player_id == pid, PersonalRecordEarned.earned_at >= start,
            PersonalRecordEarned.earned_at < end))).scalars():
        best_records[r.metric] = max(best_records.get(r.metric, 0), r.value)
    if best_records:
        cards.append({"kind": "records", "title": "Рекорды года", "lines": [
            f"{RECORD_TITLES.get(k, k)}: {int(v)}" for k, v in list(best_records.items())[:5]]})

    players = list((await session.execute(select(Player))).scalars().all())
    club_after = await _matches_between(session, end, _now() + timedelta(days=1))
    club_year = await _matches_between(session, start, end)
    moment_end = min(end, _now() + timedelta(seconds=1))
    place_end = (await _standings_at(session, players, moment_end, club_after)).get(pid)
    place_start = (await _standings_at(session, players, start, club_year + club_after)).get(pid)
    if place_end:
        line = "в начале года — без места" if not place_start else f"в начале года — #{place_start}"
        cards.append({"kind": "place", "title": "Место в клубе", "big": f"#{place_end}",
                      "up": not place_start or place_end <= place_start, "lines": [line]})

    summary = (f"{_ru_plural(len(matches), 'матч', 'матча', 'матчей')} · "
               f"{_ru_plural(w, 'победа', 'победы', 'побед')}")
    finished = end <= _now()
    cards.append({"kind": "outro", "art": True, "title": "Год закрыт" if finished else "Год ещё идёт",
                  "lines": [summary, f"До встречи в {year + 1}!" if finished else "Увидимся за столом!"]})

    stats = [["Матчей", str(len(matches))], ["Побед", f"{round(w / len(matches) * 100)}%"],
             ["Рейтинг", _signed(round(delta, 1))]]
    if place_end:
        stats.append(["Место", f"#{place_end}"])
    stats.append(["Ачивки", str(len(achs))])
    stats.append(["Игровых дней", str(len(days))])
    cards.append({"kind": "final", "title": title, "player": _person(player), "stats": stats,
                  "confetti": delta > 0})
    return {"title": title, **nav, "cards": cards, "empty": ""}
