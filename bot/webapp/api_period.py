"""
Итоги недели, месяца, квартала и года в Mini App на лету (v2.202.0) — как
«Итоги дня» (api_day): за любой период, стрелками назад и вперёд. Раньше экран
показывал последний снимок сводки бота (текст), без лиц и без листания.

Расчёты те же, что в сводках бота (`scheduler.py`): «Топ» по победам, «Герои»
с теми же названиями, «Матч недели» — `pick_match_of_day`, сравнение объёма —
`_period_totals` / `_pct_change` (неделя — со средним за 4 недели).
"""
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import AchievementEarned, Match, MatchStatus, Player
from bot.utils import (
    MSK_OFFSET,
    _ru_plural,
    compute_alltime_streak,
    match_rating_delta,
    match_report,
    pick_match_of_day,
)
from bot.webapp.api import _club_match, _person, _signed
from bot.webapp.api_club import _now
from bot.webapp.api_day import _matches_between, _standings_at
from bot.webapp.text import plain

MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
              "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM_LOWER = [m.lower() for m in MONTHS_NOM]
ROMAN = ["I", "II", "III", "IV"]

KINDS = ("week", "month", "quarter", "year")
TITLES = {"week": "Итоги недели", "month": "Итоги месяца", "quarter": "Итоги квартала", "year": "Итоги года"}
GEN = {"week": "недели", "month": "месяца", "quarter": "квартала", "year": "года"}
STANDINGS = {"week": "Топ недели", "month": "Топ месяца", "quarter": "Топ квартала", "year": "Топ года"}
MATCH_TITLES = {"week": "Матч недели", "month": "Матч месяца", "quarter": "Топ-матч квартала",
                "year": "Топ-матч года"}
COMPARE_NOTES = {"week": "к среднему за 4 недели", "month": "к прошлому месяцу", "quarter": "к прошлому кварталу"}
YOU_TITLES = {"week": "Твоя неделя", "quarter": "Твой квартал"}
SWING_MIN = 20   # как «Американские горки» в сводках бота


def _msk_date(dt: datetime) -> date:
    return (dt + MSK_OFFSET).date()


def period_bounds(kind: str, day: date) -> tuple[date, date]:
    """[начало, начало следующего) периода, в который попадает день (по МСК)."""
    if kind == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    if kind == "month":
        start = day.replace(day=1)
        return start, (start + timedelta(days=32)).replace(day=1)
    if kind == "quarter":
        start = date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
        nm = start.month + 3
        return start, date(start.year + (nm > 12), (nm - 1) % 12 + 1, 1)
    start = date(day.year, 1, 1)
    return start, date(day.year + 1, 1, 1)


def period_label(kind: str, start: date, end: date) -> str:
    if kind == "week":
        last = end - timedelta(days=1)
        if last.month == start.month:
            return f"Неделя {start.day}–{last.day} {MONTHS_GEN[last.month - 1]}"
        return f"Неделя {start.day} {MONTHS_GEN[start.month - 1]} – {last.day} {MONTHS_GEN[last.month - 1]}"
    if kind == "month":
        return f"{MONTHS_NOM[start.month - 1]} {start.year}"
    if kind == "quarter":
        return f"{ROMAN[(start.month - 1) // 3]} квартал {start.year}"
    return str(start.year)


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day) - MSK_OFFSET


async def _completed(session: AsyncSession, start: datetime, end: datetime) -> list[Match]:
    return list((await session.execute(
        select(Match).where(Match.status == MatchStatus.completed,
                            Match.completed_at >= start, Match.completed_at < end)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
        .order_by(Match.completed_at)
    )).scalars().all())


def _totals(matches: list[Match]) -> dict[str, float]:
    return {"matches": len(matches),
            "sets": sum(len(m.sets_data or []) for m in matches),
            "points": sum(s["w"] + s["l"] for m in matches for s in (m.sets_data or []))}


def _pct(current: float, base: float) -> str:
    if base <= 0:
        return ""
    pct = round((current - base) / base * 100)
    return f"+{pct}%" if pct > 0 else (f"−{-pct}%" if pct < 0 else "0%")


def _form(m: Match, pid: int) -> str:
    return "d" if m.winner_id is None else ("w" if m.winner_id == pid else "l")


def _record(w: int, lost: int, d: int) -> str:
    total = w + lost + d
    rate = int(w / total * 100) if total else 0
    return f"{w}–{lost}" + (f"–{d}" if d else "") + f" ({rate}%)"


def _heroes(kind: str, matches: list[Match], people: dict[int, dict], slackers: list[dict]) -> list[dict]:
    count: dict[int, int] = {}
    wins: dict[int, int] = {}
    delta: dict[int, float] = {}
    by_player: dict[int, list[Match]] = {}
    for m in matches:
        for pid in (m.challenger_id, m.challenged_id):
            count[pid] = count.get(pid, 0) + 1
            delta[pid] = delta.get(pid, 0.0) + match_rating_delta(m, pid)
            by_player.setdefault(pid, []).append(m)
        if m.winner_id:
            wins[m.winner_id] = wins.get(m.winner_id, 0) + 1
    heroes: list[dict] = []

    def add(key, icon, title, pids, value, note=""):
        heroes.append({"key": key, "icon": icon, "title": title,
                       "players": [people[p] for p in pids if p in people], "value": value, "note": note})

    top = max(count, key=lambda p: (count[p], -p))
    tennis_title = {"week": "Главный теннисист недели", "year": "Главный теннисист года"}.get(kind, "Главный теннисист")
    add("active", "ball-tennis", tennis_title, [top], _ru_plural(count[top], "матч", "матча", "матчей"))
    if wins:
        best = max(wins, key=lambda p: (wins[p], -p))
        add("wins", "trophy", "Больше всех побед", [best], str(wins[best]))
    up = max(delta, key=lambda p: (delta[p], -p))
    if delta[up] > 0:
        add("up", "trending-up", "Взлёт года" if kind == "year" else "Лучший рост", [up], f"+{delta[up]:.1f} pts")
    down = min(delta, key=lambda p: (delta[p], p))
    if delta[down] < 0:
        add("down", "trending-down", "Падение года" if kind == "year" else "Отрицательный рост", [down],
            f"−{abs(delta[down]):.1f} pts")
    pairs: dict[tuple[int, int], int] = {}
    for m in matches:
        key = (min(m.challenger_id, m.challenged_id), max(m.challenger_id, m.challenged_id))
        pairs[key] = pairs.get(key, 0) + 1
    if pairs:
        pair, n = max(pairs.items(), key=lambda kv: (kv[1], -kv[0][0]))
        if n >= 2:
            add("derby", "swords", "Чаще всего самбовались", list(pair), _ru_plural(n, "матч", "матча", "матчей"))
    streak = {pid: compute_alltime_streak(ms, pid) for pid, ms in by_player.items()}
    if streak:
        s_pid = max(streak, key=lambda p: (streak[p], -p))
        if streak[s_pid] >= 2:
            add("streak", "flame", f"Нагибатель {GEN[kind]}", [s_pid],
                _ru_plural(streak[s_pid], "победа", "победы", "побед") + " подряд")
    no_loss: dict[int, int] = {}
    for pid, ms in by_player.items():
        cur = best_n = 0
        for m in ms:
            cur = cur + 1 if m.winner_id in (None, pid) else 0
            best_n = max(best_n, cur)
        no_loss[pid] = best_n
    if no_loss:
        nl = max(no_loss, key=lambda p: (no_loss[p], -p))
        if no_loss[nl] >= 2:
            add("noloss", "shield-check", "Без поражений", [nl],
                _ru_plural(no_loss[nl], "матч", "матча", "матчей") + " подряд")
    swing = {}
    for pid, ms in by_player.items():
        ds = [match_rating_delta(m, pid) for m in ms]
        swing[pid] = sum(abs(x) for x in ds) - abs(sum(ds))
    if swing:
        sw = max(swing, key=lambda p: (swing[p], -p))
        if swing[sw] >= SWING_MIN:
            add("swing", "activity", "Американские горки", [sw], f"{swing[sw]:.1f} pts",
                "рейтинг мотало туда-обратно")
    if slackers and kind in ("week", "month"):
        word = "Халявщики" if len(slackers) > 1 else "Халявщик"
        heroes.append({"key": "slackers", "icon": "zzz", "title": f"{word} {GEN[kind]}",
                       "players": slackers[:4], "value": "", "note": ""})
    return heroes


async def _you(session: AsyncSession, viewer: Player, matches: list[Match], players: list[Player],
               start: datetime, end: datetime) -> list[str] | None:
    mine = [m for m in matches if viewer.id in (m.challenger_id, m.challenged_id)]
    if not mine:
        return None
    now = _now()
    later = await _matches_between(session, end, now + timedelta(days=1)) if end <= now else []
    moment_end = min(end, now + timedelta(seconds=1))
    place_end = (await _standings_at(session, players, moment_end, later)).get(viewer.id)
    place_start = (await _standings_at(session, players, start, matches + later)).get(viewer.id)
    lines = []
    if place_end:
        lines.append(f"Место: #{place_end}" + (f" (было #{place_start})" if place_start and place_start != place_end else ""))
    lines.append(f"Рейтинг: {_signed(sum(match_rating_delta(m, viewer.id) for m in mine))}")
    w = sum(1 for m in mine if m.winner_id == viewer.id)
    lost = sum(1 for m in mine if m.winner_id not in (None, viewer.id))
    lines.append(f"{_ru_plural(w, 'победа', 'победы', 'побед')} · {_ru_plural(lost, 'поражение', 'поражения', 'поражений')}")
    achs = (await session.execute(select(func.count()).select_from(AchievementEarned).where(
        AchievementEarned.player_id == viewer.id, AchievementEarned.earned_at >= start,
        AchievementEarned.earned_at < end))).scalar_one()
    if achs:
        lines.append(f"Новые ачивки: {achs}")
    return lines


async def period_payload(session: AsyncSession, viewer: Player, kind: str, day_iso: str | None) -> dict | None:
    if kind not in KINDS:
        return None
    today = _msk_date(_now())
    if day_iso:
        try:
            day = date.fromisoformat(day_iso)
        except ValueError:
            return None
        if day > today:
            return None
    else:
        day = today
    start, end = period_bounds(kind, day)
    matches = await _completed(session, _utc(start), _utc(end))
    if not day_iso and not matches:
        # в текущем периоде ещё не играли — показываем прошлый (как «Твой месяц»)
        start, end = period_bounds(kind, start - timedelta(days=1))
        matches = await _completed(session, _utc(start), _utc(end))
    first = (await session.execute(select(func.min(Match.completed_at)).where(
        Match.status == MatchStatus.completed))).scalar_one_or_none()
    prev_start = period_bounds(kind, start - timedelta(days=1))[0]
    payload: dict = {
        "kind": kind, "title": TITLES[kind], "label": period_label(kind, start, end),
        "prev": prev_start.isoformat() if first and _msk_date(first) < start else None,
        "next": end.isoformat() if end <= today else None,
        "empty": "",
    }
    if not matches:
        payload["empty"] = "За этот период матчей не было."
        return payload

    players = list((await session.execute(select(Player))).scalars().all())
    people = {p.id: _person(p) for p in players}

    # Табло: объём и сравнение (неделя — со средним за 4 недели, как «Клубный пульс»)
    cur = _totals(matches)
    if kind == "week":
        base_matches = await _completed(session, _utc(start - timedelta(days=28)), _utc(start))
        base = {k: v / 4 for k, v in _totals(base_matches).items()}
    elif kind in ("month", "quarter"):
        base = _totals(await _completed(session, _utc(prev_start), _utc(start)))
    else:
        base = {}
    payload["board"] = [{"label": label, "value": str(int(cur[key])), "delta": _pct(cur[key], base.get(key, 0))}
                        for key, label in (("matches", "Матчей"), ("sets", "Партий"), ("points", "Очков"))]
    payload["compare_note"] = COMPARE_NOTES.get(kind, "") if any(t["delta"] for t in payload["board"]) else ""

    # Топ периода: по победам, затем по матчам (как в сводке бота)
    stats: dict[int, list] = {}
    for m in matches:
        for pid in (m.challenger_id, m.challenged_id):
            st = stats.setdefault(pid, [0, 0, 0, [], 0.0])
            st[3].append(_form(m, pid))
            st[4] += match_rating_delta(m, pid)
            if m.winner_id is None:
                st[2] += 1
            elif m.winner_id == pid:
                st[0] += 1
            else:
                st[1] += 1
    order = sorted(stats, key=lambda p: (stats[p][0], sum(stats[p][:3]), -p), reverse=True)
    payload["standings_title"] = STANDINGS[kind]
    payload["standings"] = [{**people[pid], "place": i + 1, "record": _record(*stats[pid][:3]),
                             "form": stats[pid][3][-7:], "delta": _signed(stats[pid][4])}
                            for i, pid in enumerate(order) if pid in people]

    counts_all = dict((await session.execute(
        select(Match.challenger_id, func.count()).where(Match.status == MatchStatus.completed,
                                                        Match.completed_at < _utc(end))
        .group_by(Match.challenger_id))).all())
    for pid, n in (await session.execute(
            select(Match.challenged_id, func.count()).where(Match.status == MatchStatus.completed,
                                                            Match.completed_at < _utc(end))
            .group_by(Match.challenged_id))).all():
        counts_all[pid] = counts_all.get(pid, 0) + n
    slackers = [people[p.id] for p in players if counts_all.get(p.id) and p.id not in stats]
    payload["heroes"] = _heroes(kind, matches, people, slackers)
    payload["heroes_title"] = f"Герои {GEN[kind]}"

    top = pick_match_of_day(matches)
    if top is not None:
        winner_name = (top.challenger if top.winner_id == top.challenger_id else top.challenged).display_name \
            if top.winner_id else ""
        payload["match"] = {**_club_match(top), "title": MATCH_TITLES[kind],
                            "report": plain(match_report(top, winner_name)) if winner_name else ""}

    you = await _you(session, viewer, matches, players, _utc(start), _utc(end))
    if you:
        if kind in YOU_TITLES:
            payload["you"] = {"title": YOU_TITLES[kind], "lines": you}
        elif kind == "month":
            payload["you"] = {"title": f"Твой {MONTHS_NOM_LOWER[start.month - 1]}", "lines": you,
                              "story": f"{start.year}{start.month:02d}"}
    return payload
