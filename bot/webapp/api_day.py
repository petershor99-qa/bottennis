"""
Итоги дня в Mini App (v2.176.0) — экран вместо пересказа текста сводки.
Считается на лету по базе за любой прошедший день (листание «вчера/позавчера»):
табло дня и сравнение с обычным днём, игрок дня и «кто сдал», движение в таблице,
матч дня и «матч, который обсуждали», лента по часам, ачивки и рекорды дня.
Только чтение; расчёты — общие хелперы бота.
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import AchievementEarned, ChampionReign, ClubRecordPing, Match, MatchStatus, Player
from bot.services.achievements import ACHIEVEMENTS_MAP
from bot.utils import (
    MSK_OFFSET,
    compute_ranks,
    match_rating_delta,
    match_report,
    pick_match_of_day,
    pluralize_matches,
)
from bot.webapp.achievement_icons import ACHIEVEMENT_ICONS
from bot.webapp.api import _RECORD_TITLES, _club_match, _person, _signed, achievement_tier
from bot.webapp.api_club import _now
from bot.webapp.reactions import feed_counts, reactions_for
from bot.webapp.text import plain

USUAL_WINDOW_DAYS = 30      # «обычный день» — среднее по игровым дням за 30 дней до этого
USUAL_SAME_PCT = 10         # разница меньше — «как обычно»
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]


def _msk_today() -> date:
    return (_now() + MSK_OFFSET).date()


def _bounds(day: date) -> tuple[datetime, datetime]:
    """Сутки по МСК в UTC-naive: [начало, конец)."""
    start = datetime(day.year, day.month, day.day) - MSK_OFFSET
    return start, start + timedelta(days=1)


def _msk_day(dt: datetime) -> date:
    return (dt + MSK_OFFSET).date()


def day_label(day: date, today: date) -> str:
    """«Сегодня, 14 октября», «Вчера, 13 октября», «12 октября» (год — если не текущий)."""
    text = f"{day.day} {MONTHS_GEN[day.month - 1]}"
    if day.year != today.year:
        text += f" {day.year}"
    if day == today:
        return "Сегодня, " + text
    if day == today - timedelta(days=1):
        return "Вчера, " + text
    return text


def usual_line(count: int, usual: float | None) -> str:
    """Сравнение дня с обычным игровым днём клуба."""
    if not usual:
        return ""
    pct = round((count - usual) / usual * 100)
    usual_text = f"обычно {round(usual, 1):g}".replace(".", ",")
    if abs(pct) < USUAL_SAME_PCT:
        return f"Как обычно ({usual_text})"
    if pct > 0:
        return f"Жарче обычного на {pct}% ({usual_text})"
    return f"Тише обычного на {-pct}% ({usual_text})"


async def _matches_between(session: AsyncSession, start: datetime, end: datetime) -> list[Match]:
    return list((await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed, Match.completed_at >= start, Match.completed_at < end)
        .order_by(Match.completed_at)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )).scalars().all())


async def _neighbour_day(session: AsyncSession, start: datetime, end: datetime, before: bool) -> str | None:
    """Ближайший день с матчами до (before) или после этого дня."""
    cond = Match.completed_at < start if before else Match.completed_at >= end
    agg = func.max(Match.completed_at) if before else func.min(Match.completed_at)
    when = (await session.execute(
        select(agg).where(Match.status == MatchStatus.completed, cond)
    )).scalar_one_or_none()
    if when is None:
        return None
    day = _msk_day(when)
    return None if day > _msk_today() else day.isoformat()


async def _usual_matches(session: AsyncSession, start: datetime) -> float | None:
    """Среднее число матчей в игровой день за USUAL_WINDOW_DAYS до этого дня."""
    rows = (await session.execute(
        select(Match.completed_at).where(
            Match.status == MatchStatus.completed,
            Match.completed_at >= start - timedelta(days=USUAL_WINDOW_DAYS),
            Match.completed_at < start,
        )
    )).scalars().all()
    per_day: dict[date, int] = {}
    for when in rows:
        per_day[_msk_day(when)] = per_day.get(_msk_day(when), 0) + 1
    return sum(per_day.values()) / len(per_day) if per_day else None


async def _standings_at(session: AsyncSession, players: list[Player], moment: datetime,
                        later: list[Match]) -> dict[int, int]:
    """Места в таблице на момент `moment`: рейтинг = текущий минус изменения в
    матчах после него (пол рейтинга не учитывается — как у графика), в таблице
    только игравшие до момента, чемпион того момента закреплён на #1."""
    shift: dict[int, float] = {}
    for m in later:
        for pid in (m.challenger_id, m.challenged_id):
            shift[pid] = shift.get(pid, 0.0) + match_rating_delta(m, pid)
    counts: dict[int, int] = {}
    for a, b in (await session.execute(
        select(Match.challenger_id, Match.challenged_id).where(
            Match.status == MatchStatus.completed, Match.completed_at < moment,
        )
    )).all():
        counts[a] = counts.get(a, 0) + 1
        counts[b] = counts.get(b, 0) + 1
    champion_id = (await session.execute(
        select(ChampionReign.player_id).where(
            ChampionReign.started_at <= moment,
            (ChampionReign.ended_at.is_(None)) | (ChampionReign.ended_at > moment),
        ).order_by(ChampionReign.started_at.desc()).limit(1)
    )).scalar_one_or_none()
    snapshot = [SimpleNamespace(id=p.id, rating=p.rating - shift.get(p.id, 0.0)) for p in players]
    return compute_ranks(snapshot, counts, champion_id=champion_id)


def _form_mark(m: Match, pid: int) -> str:
    return "d" if m.winner_id is None else ("w" if m.winner_id == pid else "l")


async def day_payload(session: AsyncSession, viewer: Player, day_iso: str | None) -> dict | None:
    today = _msk_today()
    if day_iso:
        try:
            day = date.fromisoformat(day_iso)
        except ValueError:
            return None
        if day > today:
            return None
    else:
        last = (await session.execute(
            select(func.max(Match.completed_at)).where(Match.status == MatchStatus.completed)
        )).scalar_one_or_none()
        if last is None:
            return {"empty": "Матчей ещё не было.", "label": day_label(today, today), "date": today.isoformat(),
                    "prev": None, "next": None}
        day = min(_msk_day(last), today)
    start, end = _bounds(day)
    matches = await _matches_between(session, start, end)
    payload: dict = {
        "date": day.isoformat(), "label": day_label(day, today),
        "prev": await _neighbour_day(session, start, end, before=True),
        "next": await _neighbour_day(session, start, end, before=False),
    }
    if not matches:
        payload["empty"] = "В этот день не играли."
        return payload
    payload["empty"] = ""

    # A. Табло и сравнение с обычным днём
    sets = sum(len(m.sets_data or []) for m in matches)
    points = sum(s["w"] + s["l"] for m in matches for s in (m.sets_data or []))
    payload["board"] = {
        "matches": len(matches), "sets": sets, "points": points,
        "usual": usual_line(len(matches), await _usual_matches(session, start)),
    }

    # B. Игрок дня и «кто сдал»
    players = list((await session.execute(select(Player))).scalars().all())
    by_id = {p.id: p for p in players}
    stats: dict[int, dict] = {}
    for m in matches:
        for pid in (m.challenger_id, m.challenged_id):
            st = stats.setdefault(pid, {"delta": 0.0, "w": 0, "l": 0, "d": 0, "form": []})
            st["delta"] += match_rating_delta(m, pid)
            mark = _form_mark(m, pid)
            st[mark] += 1
            st["form"].append(mark)

    def hero(pid: int) -> dict:
        st = stats[pid]
        record = f"{st['w']}–{st['l']}" + (f"–{st['d']}" if st["d"] else "")
        return {**_person(by_id[pid]), "delta": _signed(round(st["delta"], 1)),
                "record": record, "matches": pluralize_matches(st["w"] + st["l"] + st["d"]), "form": st["form"]}

    known = [pid for pid in stats if pid in by_id]
    best = max(known, key=lambda pid: (stats[pid]["delta"], stats[pid]["w"]), default=None)
    worst = min(known, key=lambda pid: (stats[pid]["delta"], -stats[pid]["l"]), default=None)
    payload["hero"] = hero(best) if best is not None and stats[best]["delta"] > 0 else None
    payload["slump"] = hero(worst) if worst is not None and worst != best and stats[worst]["delta"] < 0 else None
    payload["players"] = sorted((hero(pid) for pid in known), key=lambda r: -float(r["delta"].replace("−", "-")))

    # C. Движение в таблице за день
    later_end = await _matches_between(session, end, _now() + timedelta(days=1))
    after = await _standings_at(session, players, min(end, _now() + timedelta(seconds=1)), later_end)
    before = await _standings_at(session, players, start, matches + later_end)
    movement = []
    for pid, place in sorted(after.items(), key=lambda kv: kv[1]):
        if pid not in by_id:
            continue
        was = before.get(pid)
        movement.append({**_person(by_id[pid]), "place": place,
                         "change": (was - place) if was else None, "new": was is None,
                         "played": pid in stats})
    payload["movement"] = movement

    # D. Матч дня и матч, который обсуждали
    reactions = await reactions_for(session, [m.id for m in matches], viewer.id)
    totals = {mid: sum(r["count"] for r in items) for mid, items in reactions.items()}
    top = pick_match_of_day(matches)
    if top is not None:
        winner = by_id.get(top.winner_id) if top.winner_id else None
        payload["match_of_day"] = {
            **_club_match(top), "time": (top.completed_at + MSK_OFFSET).strftime("%H:%M"),
            "report": plain(match_report(top, winner.display_name if winner else "?")) if winner else "",
            "reactions": feed_counts(reactions.get(top.id, [])),
        }
    else:
        payload["match_of_day"] = None
    discussed_id = max(totals, key=lambda mid: (totals[mid], mid), default=None)
    if discussed_id is not None and totals[discussed_id] >= 2:
        m = next(x for x in matches if x.id == discussed_id)
        payload["discussed"] = {**_club_match(m), "reactions": feed_counts(reactions[discussed_id]),
                                "total": totals[discussed_id]}
    else:
        payload["discussed"] = None

    # E. Лента по часам
    hours: dict[int, int] = {}
    timeline = []
    for m in matches:
        hour = (m.completed_at + MSK_OFFSET).hour
        hours[hour] = hours.get(hour, 0) + 1
        item = {**_club_match(m), "time": (m.completed_at + MSK_OFFSET).strftime("%H:%M"),
                "delta": f"+{m.rating_change:.1f}" if m.rating_change is not None and m.winner_id else "",
                "reactions": feed_counts(reactions.get(m.id, []))}
        timeline.append(item)
    lo, hi = min(hours), max(hours)
    payload["hours"] = [{"h": h, "n": hours.get(h, 0)} for h in range(lo, hi + 1)]
    payload["timeline"] = timeline

    # F. Ачивки и рекорды дня
    achievements = []
    for row in (await session.execute(
        select(AchievementEarned).where(AchievementEarned.earned_at >= start, AchievementEarned.earned_at < end)
        .order_by(AchievementEarned.earned_at)
    )).scalars():
        ach, p = ACHIEVEMENTS_MAP.get(row.achievement_id), by_id.get(row.player_id)
        if ach is None or p is None or ach.hidden:
            continue
        achievements.append({"player": _person(p), "name": ach.name, "desc": ach.desc,
                             "icon": ACHIEVEMENT_ICONS.get(ach.id, "trophy"), "tier": achievement_tier(ach.id)})
    records = []
    for ping in (await session.execute(
        select(ClubRecordPing).where(ClubRecordPing.pinged_at >= start, ClubRecordPing.pinged_at < end)
        .order_by(ClubRecordPing.pinged_at)
    )).scalars():
        title, fmt = _RECORD_TITLES.get(ping.kind, ("Рекорд клуба", lambda v: f"{v:.1f}"))
        p = by_id.get(ping.player_id)
        records.append({"title": title, "value": fmt(ping.value), "player": _person(p) if p else None})
    payload["achievements"] = achievements
    payload["records"] = records
    return payload

