"""
Профиль в Mini App, v2.177.0: личные рекорды (7 метрик, раньше только в боте),
коронный счёт, главный соперник. Только чтение; рекорды считаются теми же
функциями, что и пинги личных рекордов в боте (bot/services/personal_records.py).
"""
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AchievementEarned, ChampionReign, Match, PersonalRecordEarned, Player
from bot.services.personal_records import (
    _get_matches_asc,
    _max_no_loss_streak,
    _max_streak_vs_any_opponent,
    _set_margin,
    _sets_count,
    _total_points,
    _wins_by_day,
)
from bot.utils import MSK_OFFSET, _my_opp_points, _ru_plural, compute_alltime_streak, match_rating_delta
from bot.webapp.api import _msk_date, _person

SIGNATURE_MIN = 3   # коронный счёт — только если повторялся хотя бы столько раз


def _wins_text(n: int) -> str:
    return _ru_plural(n, "победа", "победы", "побед") + " подряд"


def personal_bests(matches: list[Match], player_id: int) -> list[tuple[str, str, int, str]]:
    """(метрика, название, значение, подпись) по всей карьере; matches — от старых
    к новым. Нулевые значения не показываются."""
    wins = [m for m in matches if m.winner_id == player_id]
    rows = [
        ("win_streak", "Серия побед", compute_alltime_streak(matches, player_id), _wins_text),
        ("no_loss_streak", "Без поражений подряд", _max_no_loss_streak(matches, player_id),
         lambda n: _ru_plural(n, "матч", "матча", "матчей")),
        ("wins_per_day", "Побед за день", max(_wins_by_day(matches, player_id).values(), default=0),
         lambda n: _ru_plural(n, "победа", "победы", "побед")),
        ("set_margin", "Самый крупный разгром", max((_set_margin(m) for m in wins), default=0),
         lambda n: f"+{_ru_plural(n, 'очко', 'очка', 'очков')}"),
        ("longest_match", "Самый долгий матч", max((_sets_count(m) for m in matches), default=0),
         lambda n: _ru_plural(n, "партия", "партии", "партий")),
        ("most_points", "Больше всего очков за матч", max((_total_points(m) for m in matches), default=0),
         lambda n: _ru_plural(n, "очко", "очка", "очков")),
        ("streak_vs_opponent", "Серия над одним соперником", _max_streak_vs_any_opponent(matches, player_id),
         _wins_text),
    ]
    return [(key, title, value, fmt(value)) for key, title, value, fmt in rows if value > 0]


def signature_score(matches: list[Match], player_id: int) -> dict | None:
    """Коронный счёт: самый частый счёт выигранной партии и доля партий на дьюсе."""
    won: Counter = Counter()
    total = deuce = 0
    for m in matches:
        for s in m.sets_data or []:
            mine, theirs = _my_opp_points(m, s, player_id)
            total += 1
            if max(mine, theirs) > 11:
                deuce += 1
            if mine > theirs:
                won[f"{mine}:{theirs}"] += 1
    if not total:
        return None
    score, times = won.most_common(1)[0] if won else ("", 0)
    return {
        "score": score if times >= SIGNATURE_MIN else "",
        "times": _ru_plural(times, "раз", "раза", "раз") if times >= SIGNATURE_MIN else "",
        "deuce": f"{round(deuce / total * 100)}%",
        "sets": _ru_plural(total, "партия", "партии", "партий"),
    }


def main_rival(matches: list[Match], player_id: int, players: dict[int, Player]) -> dict | None:
    """Главный соперник — с кем больше всего встреч (при равенстве — с кем играл
    позже). Счёт встреч и дата последней."""
    met: dict[int, dict] = {}
    for m in matches:  # от старых к новым: последняя запись — самая свежая встреча
        opp = m.challenged_id if m.challenger_id == player_id else m.challenger_id
        row = met.setdefault(opp, {"w": 0, "l": 0, "d": 0, "n": 0})
        row["n"] += 1
        row["last"] = m.completed_at
        key = "d" if m.winner_id is None else ("w" if m.winner_id == player_id else "l")
        row[key] += 1
    if not met:
        return None
    opp_id = max(met, key=lambda pid: (met[pid]["n"], met[pid]["last"] or datetime.min))
    if opp_id not in players:
        return None
    row = met[opp_id]
    score = f"{row['w']}–{row['l']}" + (f"–{row['d']}" if row["d"] else "")
    return {**_person(players[opp_id]), "meetings": _ru_plural(row["n"], "встреча", "встречи", "встреч"),
            "score": score, "last": _msk_date(row["last"]),
            "tone": "win" if row["w"] > row["l"] else ("loss" if row["l"] > row["w"] else "")}


# Отметки на графике рейтинга (v2.201.0): трон, ачивки, личные рекорды — у точки
# матча, на котором случились. Названия личных рекордов — как в personal_bests.
RECORD_TITLES = {
    "win_streak": "Серия побед", "no_loss_streak": "Без поражений подряд", "wins_per_day": "Побед за день",
    "set_margin": "Самый крупный разгром", "longest_match": "Самый долгий матч",
    "most_points": "Больше всего очков за матч", "streak_vs_opponent": "Серия над одним соперником",
}
CHART_MARKS_MAX = 30


def _point_index(points: list[Match], when: datetime | None) -> int | None:
    """Точка графика (матч), к которой относится событие: матч с тем же временем
    или первый после него, в пределах суток."""
    if when is None or not points:
        return None
    for i, m in enumerate(points):
        if m.completed_at and m.completed_at >= when - timedelta(seconds=2):
            return i if m.completed_at - when <= timedelta(days=1) else None
    return None


async def chart_marks(session: AsyncSession, player_id: int, points: list[Match]) -> list[dict]:
    from bot.services.achievements import ACHIEVEMENTS_LIST

    if not points:
        return []
    since = (points[0].completed_at or datetime.min) - timedelta(seconds=2)
    names = {a.id: a.name for a in ACHIEVEMENTS_LIST}
    events: list[tuple[datetime, str, str]] = []
    for r in (await session.execute(select(ChampionReign).where(
            ChampionReign.player_id == player_id, ChampionReign.started_at >= since))).scalars():
        events.append((r.started_at, "throne", "Взял трон"))
    for a in (await session.execute(select(AchievementEarned).where(
            AchievementEarned.player_id == player_id, AchievementEarned.earned_at >= since))).scalars():
        if a.achievement_id in names:
            events.append((a.earned_at, "ach", f"Ачивка «{names[a.achievement_id]}»"))
    for p in (await session.execute(select(PersonalRecordEarned).where(
            PersonalRecordEarned.player_id == player_id, PersonalRecordEarned.earned_at >= since))).scalars():
        events.append((p.earned_at, "record", f"Личный рекорд: {RECORD_TITLES.get(p.metric, p.metric)}"))
    marks: dict[int, dict] = {}
    rank = {"throne": 0, "record": 1, "ach": 2}
    for when, kind, text in sorted(events, key=lambda e: e[0]):
        i = _point_index(points, when)
        if i is None:
            continue
        mark = marks.setdefault(i, {"i": i, "kind": kind, "texts": []})
        if rank[kind] < rank[mark["kind"]]:
            mark["kind"] = kind
        if text not in mark["texts"]:
            mark["texts"].append(text)
    return sorted(marks.values(), key=lambda x: x["i"])[-CHART_MARKS_MAX:]


# «Лучший день» в профиле (v2.201.0): день по МСК с наибольшим приростом рейтинга
BEST_DAY_MIN = 5.0


def best_day(matches: list[Match], player_id: int) -> dict | None:
    days: dict = {}
    for m in matches:
        if not m.completed_at or m.rating_change is None:
            continue
        day = (m.completed_at + MSK_OFFSET).date()
        d = days.setdefault(day, [0.0, 0, 0])
        d[0] += match_rating_delta(m, player_id)
        d[1] += 1
        d[2] += m.winner_id == player_id
    if not days:
        return None
    day, (delta, n, wins) = max(days.items(), key=lambda kv: (round(kv[1][0], 1), kv[0]))
    if delta < BEST_DAY_MIN:
        return None
    return {"date": day.strftime("%d.%m.%y"), "ymd": day.strftime("%Y%m%d"), "delta": f"+{delta:.1f}",
            "note": _ru_plural(n, "матч", "матча", "матчей") + " · " + _ru_plural(wins, "победа", "победы", "побед")}


async def personal_payload(session: AsyncSession, player: Player) -> dict:
    matches = await _get_matches_asc(session, player.id)
    if not matches:
        return {"bests": [], "signature": None, "rival": None, "best_day": None}
    dates: dict[str, tuple] = {}
    for row in (await session.execute(
        select(PersonalRecordEarned).where(PersonalRecordEarned.player_id == player.id)
    )).scalars():
        if row.earned_at and (row.metric not in dates or row.value >= dates[row.metric][0]):
            dates[row.metric] = (row.value, row.earned_at)
    bests = []
    for key, title, value, text in personal_bests(matches, player.id):
        item = {"key": key, "label": title, "value": text}
        known = dates.get(key)
        if known and known[0] == value:
            item["date"] = _msk_date(known[1], "%d.%m.%y")
        bests.append(item)
    players = {p.id: p for p in (await session.execute(select(Player))).scalars()}
    return {"bests": bests, "signature": signature_score(matches, player.id),
            "rival": main_rival(matches, player.id, players), "best_day": best_day(matches, player.id)}
