"""
Данные Mini App, релиз 3 пакета «Бот + Mini App 2» (v2.168.0): лента клуба,
экран «Итоги» (снимки сводок), экран матча, рейтинг за неделю/месяц, полный
ряд рейтинга для графика с периодами и сравнением, редкость и даты ачивок,
три ближайшие ачивки. Только чтение; расчёты — те же функции, что и в боте.
"""
import html
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import AchievementEarned, ChampionReign, ClubRecordPing, DigestSnapshot, Match, MatchStatus, Player
from bot.services.achievements import ACHIEVEMENTS_LIST, ACHIEVEMENTS_MAP, get_achievements
from bot.services.stats import _compute_player_stats, nearest_achievements
from bot.utils import (
    DRAMA_THRESHOLD,
    MSK_OFFSET,
    _ru_plural,
    build_rating_series,
    compute_h2h,
    get_career_matches,
    match_drama_reason,
    match_drama_score,
    match_rating_delta,
    match_report,
    msk_day_start,
    pluralize_matches,
    pluralize_wins,
)
from bot.webapp.achievement_icons import ACHIEVEMENT_ICONS
from bot.webapp.api import _RECORD_TITLES, _club_match, _msk_date, _person, _signed, achievement_tier
from bot.webapp.text import groups, plain

# Лента за 30 дней (v2.171.0, было 7): главная показывает первую порцию,
# остальное — по «Показать ещё» через /api/feed?offset=N
FEED_DAYS = 30
FEED_LIMIT = 300
FEED_PAGE = 15
SUMMARY_KINDS = [("day", "День"), ("week", "Неделя"), ("month", "Месяц"), ("quarter", "Квартал"), ("year", "Год")]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _day_label(dt: datetime) -> str:
    """«Сегодня», «Вчера» или дата — по МСК."""
    day = (dt + MSK_OFFSET).date()
    today = (_now() + MSK_OFFSET).date()
    if day == today:
        return "Сегодня"
    if day == today - timedelta(days=1):
        return "Вчера"
    return day.strftime("%d.%m")


# ── Лента клуба ───────────────────────────────────────────────────────────────

async def feed_events(session: AsyncSession, days: int = FEED_DAYS, limit: int = FEED_LIMIT) -> list[dict]:
    """События клуба за `days` дней, свежие сверху: матчи (у ярких — репортаж),
    полученные ачивки, рекорды клуба, смены трона."""
    since = _now() - timedelta(days=days)
    players = {p.id: p for p in (await session.execute(select(Player))).scalars().all()}
    events: list[tuple[datetime, dict]] = []

    matches = (await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed, Match.completed_at >= since)
        .order_by(desc(Match.completed_at))
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )).scalars().all()
    # Репортаж — только у самого драматичного матча дня (и если он дотягивает до
    # порога «матча дня»): под каждым матчем текст обесценился бы.
    best_of_day: dict = {}
    for m in matches:
        if m.winner_id is None or not m.completed_at:
            continue
        day = (m.completed_at + MSK_OFFSET).date()
        score = match_drama_score(m)
        if score >= DRAMA_THRESHOLD and score > best_of_day.get(day, (0.0, None))[0]:
            best_of_day[day] = (score, m.id)
    featured_ids = {mid for _, mid in best_of_day.values()}
    for m in matches:
        item = {"type": "match", "id": m.id, **_club_match(m), "delta": ""}
        if m.rating_change is not None and m.winner_id is not None:
            item["delta"] = f"+{m.rating_change:.1f}"
        if m.id in featured_ids:
            winner = players.get(m.winner_id)
            item["report"] = plain(match_report(m, winner.display_name if winner else "?"))
        events.append((m.completed_at, item))

    for row in (await session.execute(
        select(AchievementEarned).where(AchievementEarned.earned_at >= since)
    )).scalars().all():
        ach, player = ACHIEVEMENTS_MAP.get(row.achievement_id), players.get(row.player_id)
        if ach is None or player is None or ach.hidden:
            continue  # скрытые не раскрываем и в ленте
        events.append((row.earned_at, {
            "type": "achievement", "target": f"ach:{row.id}",
            "player": _person(player), "name": ach.name, "desc": ach.desc,
            "icon": ACHIEVEMENT_ICONS.get(ach.id, "trophy"), "tier": achievement_tier(ach.id),
        }))

    for ping in (await session.execute(
        select(ClubRecordPing).where(ClubRecordPing.pinged_at >= since)
    )).scalars().all():
        player = players.get(ping.player_id)
        title, fmt = _RECORD_TITLES.get(ping.kind, ("Рекорд клуба", lambda v: f"{v:.1f}"))
        events.append((ping.pinged_at, {
            "type": "record", "target": f"rec:{ping.id}", "title": title, "value": fmt(ping.value),
            "player": _person(player) if player else None,
        }))

    for reign in (await session.execute(
        select(ChampionReign).where(ChampionReign.started_at >= since)
    )).scalars().all():
        player = players.get(reign.player_id)
        if player:
            events.append((reign.started_at, {"type": "throne", "player": _person(player)}))

    events.sort(key=lambda e: e[0], reverse=True)
    out = []
    for when, item in events[:limit]:
        item["day"] = _day_label(when)
        item["time"] = (when + MSK_OFFSET).strftime("%H:%M")
        out.append(item)
    return out


async def today_line(session: AsyncSession, player: Player) -> str:
    """«Сегодня: 3 матча · +18.2» или пусто, если сегодня не играл."""
    matches = (await session.execute(
        select(Match).where(
            Match.status == MatchStatus.completed,
            Match.completed_at >= msk_day_start(),
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
        )
    )).scalars().all()
    if not matches:
        return ""
    delta = round(sum(match_rating_delta(m, player.id) for m in matches), 1)
    wins = sum(1 for m in matches if m.winner_id == player.id)
    return f"Сегодня: {pluralize_matches(len(matches))}, {pluralize_wins(wins)} · {_signed(delta)}"


# ── Итоги (снимки сводок) ─────────────────────────────────────────────────────

_STANDING_RE = re.compile(r"^(🥇|🥈|🥉|\d+\.)\s*<b>(.+?)</b>(.*)$")
_MARKS = {"🟩": "w", "🟢": "w", "🟥": "l", "🔴": "l", "⬜": "d", "🟡": "d"}
_PLACES = {"🥇": 1, "🥈": 2, "🥉": 3}


def _standing(line: str) -> dict | None:
    m = _STANDING_RE.match(line.strip())
    if not m:
        return None
    mark, name, rest = m.groups()
    place = _PLACES.get(mark) or int(mark.rstrip("."))
    form = [_MARKS[ch] for ch in rest if ch in _MARKS]
    clean = rest.translate({ord(c): None for c in _MARKS})
    return {"place": place, "name": html.unescape(name), "record": plain(clean).strip("— ").strip(), "form": form}


SUMMARY_TITLES = {"day": "Итоги дня", "week": "Итоги недели", "month": "Итоги месяца",
                  "quarter": "Итоги квартала", "year": "Итоги года"}


def parse_summary(text: str, kind: str = "") -> dict:
    """Текст сводки -> заголовок, «сыграно», таблица, остальные блоки. У недели и
    месяца клубная часть начинается сразу с таблицы — тогда заголовок по типу."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    title = SUMMARY_TITLES.get(kind, "Итоги")
    if paragraphs and "Итоги" in paragraphs[0].split("\n")[0]:
        title = plain(paragraphs[0].split("\n")[0])
        paragraphs = paragraphs[1:]
    played: list[str] = []
    standings: list[dict] = []
    standings_title = ""
    rest_lines: list[str] = []
    for para in paragraphs:
        lines = para.split("\n")
        rows = [_standing(line) for line in lines]
        if any(rows) and not standings:
            standings = [r for r in rows if r]
            head = [line for line, r in zip(lines, rows) if not r]
            standings_title = plain(head[0]).rstrip(":") if head else ""
            continue
        if not played and not standings and ("Сыграно" in para or "сыграно" in para):
            played = [plain(line) for line in lines if plain(line)]
            continue
        rest_lines += lines + [""]
    return {"title": title, "played": played, "standings_title": standings_title,
            "standings": standings, "groups": groups(rest_lines)}


async def summary_payload(session: AsyncSession, kind: str) -> dict | None:
    if kind not in dict(SUMMARY_KINDS):
        return None
    latest: dict[str, DigestSnapshot] = {}
    for snap in (await session.execute(select(DigestSnapshot).order_by(desc(DigestSnapshot.created_at)))).scalars():
        latest.setdefault(snap.kind, snap)
    tabs = [{"kind": k, "title": t, "available": k in latest} for k, t in SUMMARY_KINDS]
    snap = latest.get(kind)
    if snap is None:
        empty = ("Итоги года выйдут 30 декабря в 12:00." if kind == "year"
                 else "Эти итоги появятся после ближайшей сводки.")
        return {"kind": kind, "tabs": tabs, "empty": empty}
    return {"kind": kind, "tabs": tabs, "date": _msk_date(snap.created_at, "%d.%m.%Y"), "empty": "",
            **parse_summary(snap.text, kind)}


# ── Экран матча ───────────────────────────────────────────────────────────────

async def match_payload(session: AsyncSession, match_id: int) -> dict | None:
    m = (await session.execute(
        select(Match).where(Match.id == match_id, Match.status == MatchStatus.completed)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )).scalar_one_or_none()
    if m is None:
        return None
    a, b = m.challenger, m.challenged
    winner = "a" if m.winner_id == a.id else ("b" if m.winner_id == b.id else "")
    sets = []
    for s in m.sets_data or []:
        a_pts, b_pts = (s["w"], s["l"]) if winner in ("a", "") else (s["l"], s["w"])
        sets.append({"a": a_pts, "b": b_pts})
    meetings = (await session.execute(
        select(Match).where(Match.status == MatchStatus.completed, or_(
            and_(Match.challenger_id == a.id, Match.challenged_id == b.id),
            and_(Match.challenger_id == b.id, Match.challenged_id == a.id),
        )).order_by(desc(Match.completed_at))
    )).scalars().all()
    h2h = compute_h2h(meetings, a.id, b.id)
    report = ""
    if winner and match_drama_score(m) >= DRAMA_THRESHOLD:
        report = plain(match_report(m, (a if winner == "a" else b).display_name))
    return {
        "id": m.id,
        "date": _msk_date(m.completed_at, "%d.%m.%Y · %H:%M"),
        "a": {**_person(a), "delta": _signed(match_rating_delta(m, a.id)) if m.rating_change is not None else ""},
        "b": {**_person(b), "delta": _signed(match_rating_delta(m, b.id)) if m.rating_change is not None else ""},
        "winner": winner,
        "sets": sets,
        "boss": bool(m.is_boss_fight),
        "reason": plain(match_drama_reason(m)) if match_drama_score(m) >= DRAMA_THRESHOLD else "",
        "report": report,
        "h2h": {"a": h2h["wins"], "b": h2h["losses"], "draws": h2h["draws"],
                "total": _ru_plural(len(meetings), "встреча", "встречи", "встреч")},
    }


# ── Рейтинг за период ─────────────────────────────────────────────────────────

PERIODS = {"week": (7, "за 7 дней"), "month": (30, "за 30 дней"), "quarter": (90, "за 90 дней")}  # 90 — v2.183.0


async def period_leaderboard_payload(session: AsyncSession, viewer: Player, period: str) -> dict | None:
    """Кто больше всех набрал за период: сумма изменений рейтинга, матчи, победы.
    Вечный рейтинг не сбрасывается — это отдельный срез «кто в ударе»."""
    if period not in PERIODS:
        return None
    days, label = PERIODS[period]
    since = _now() - timedelta(days=days)
    matches = (await session.execute(
        select(Match).where(Match.status == MatchStatus.completed, Match.completed_at >= since)
    )).scalars().all()
    gain: dict[int, float] = {}
    count: dict[int, int] = {}
    wins: dict[int, int] = {}
    for m in matches:
        for pid in (m.challenger_id, m.challenged_id):
            gain[pid] = gain.get(pid, 0.0) + match_rating_delta(m, pid)
            count[pid] = count.get(pid, 0) + 1
        if m.winner_id:
            wins[m.winner_id] = wins.get(m.winner_id, 0) + 1
    players = {p.id: p for p in (await session.execute(select(Player).where(Player.id.in_(gain.keys())))).scalars()}
    ordered = sorted(gain, key=lambda pid: (-gain[pid], -count[pid]))
    rows = []
    for i, pid in enumerate(ordered, start=1):
        p = players.get(pid)
        if p is None:
            continue
        rows.append({
            **_person(p), "rank": i, "player_id": pid, "is_viewer": pid == viewer.id,
            "gain": _signed(round(gain[pid], 1)), "up": gain[pid] >= 0,
            "subtitle": f"{pluralize_matches(count[pid])} · {pluralize_wins(wins.get(pid, 0))}",
        })
    return {"period": period, "label": label, "rows": rows,
            "empty": "За этот период матчей не было." if not rows else ""}


# ── График: полный ряд с датами ───────────────────────────────────────────────

async def chart_payload(session: AsyncSession, player: Player) -> dict:
    matches = await get_career_matches(session, player.id)
    rated = sorted((m for m in matches if m.rating_change is not None and m.completed_at), key=lambda m: m.completed_at)
    if len(rated) < 2:
        return {"name": player.display_name, "points": []}
    _labels, values = build_rating_series(rated, player.id, player.rating, limit=0)
    points = [{"d": (m.completed_at + MSK_OFFSET).strftime("%Y-%m-%d"), "v": v} for m, v in zip(rated, values)]
    return {"name": player.display_name, "id": player.id, "points": points}


# ── Ачивки: редкость, даты, три ближайшие ─────────────────────────────────────

async def achievements_extras(session: AsyncSession, player: Player) -> dict:
    """{id: {"rarity": "есть у 2 из 6", "date": "12.10.26"}} и ближайшие три."""
    players = (await session.execute(select(Player))).scalars().all()
    from bot.utils import get_match_counts

    counts = await get_match_counts(session)
    active = [p for p in players if counts.get(p.id, 0) > 0]
    owners: dict[str, int] = {}
    for p in active:
        for ach_id in set(get_achievements(p)):
            owners[ach_id] = owners.get(ach_id, 0) + 1
    dates = {
        row.achievement_id: row.earned_at
        for row in (await session.execute(
            select(AchievementEarned).where(AchievementEarned.player_id == player.id)
        )).scalars()
        if row.earned_at
    }
    per_ach = {}
    for a in ACHIEVEMENTS_LIST:
        info = {"rarity": f"есть у {owners.get(a.id, 0)} из {len(active)}" if active else ""}
        if a.id in dates:
            info["date"] = _msk_date(dates[a.id], "%d.%m.%y")
        per_ach[a.id] = info

    matches = await get_career_matches(session, player.id, with_opponents=True)
    nearest = []
    if matches:
        stats = _compute_player_stats(player, matches)
        for ratio, ach_id, current, target, unit in nearest_achievements(player, stats, len(players))[:3]:
            a = ACHIEVEMENTS_MAP[ach_id]
            nearest.append({"name": a.name, "desc": a.desc, "icon": ACHIEVEMENT_ICONS.get(ach_id, "trophy"),
                            "tier": achievement_tier(ach_id), "progress": round(max(0.0, min(1.0, ratio)), 2),
                            "value": f"{current} из {target} {unit}"})
    return {"per_ach": per_ach, "nearest": nearest}
