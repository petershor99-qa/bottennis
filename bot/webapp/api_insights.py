"""
Mini App, релиз 4 пакета «Бот + Mini App 2» (v2.170.0):
- «Когда играют» — матчи по дням недели и часам (МСК) за 90 дней, клуб или свои;
- «Использование» — отчёт по `usage_events` для администратора: бот и приложение;
- итоги года в приложении — «Твой год» и неформальные звания (только чтение:
  голосование целиком остаётся в боте).
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AchievementEarned, AppBoot, DigestSnapshot, Match, MatchStatus, Player, UsageEvent
from bot.services.usage import action_label
from bot.services.year_vote import YEAR_VOTE_CLOSE_AT, _full_year_bounds_utc, compute_results
from bot.utils import (
    MSK_OFFSET,
    _ru_plural,
    match_rating_delta,
    pluralize_matches,
    pluralize_players,
    pluralize_votes,
)

WHEN_DAYS = 90
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAYS_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
# Рабочий день по умолчанию: сетка не короче 9:00–19:00, даже если матчей мало
WHEN_MIN_HOUR, WHEN_MAX_HOUR = 9, 18

USAGE_DAYS = 30
USAGE_TOP = 15
APP_PREFIX = "app_"
# Экраны приложения в отчёте (имя из счётчика -> подпись)
APP_SCREEN_LABELS = {
    "home": "Главная «Клуб»",
    "rating": "Рейтинг",
    "me": "Мой профиль",
    "me_matches": "Мой профиль: матчи",
    "me_achievements": "Мой профиль: ачивки",
    "me_style": "Мой профиль: стиль",
    "player": "Профиль игрока",
    "player_matches": "Профиль игрока: матчи",
    "player_achievements": "Профиль игрока: ачивки",
    "player_style": "Профиль игрока: стиль",
    "h2h": "Ты против игрока",
    "records": "Рекорды клуба",
    "matches": "Все матчи клуба",
    "activity_club": "Активность клуба",
    "throne": "Зал славы",
    "summary_day": "Итоги дня",
    "summary_week": "Итоги недели",
    "summary_month": "Итоги месяца",
    "summary_quarter": "Итоги квартала",
    "summary_year": "Итоги года",
    "match": "Экран матча",
    "when": "Когда играют",
    "when_me": "Когда играют: свои",
    "usage": "Использование",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _slot(hour: int) -> str:
    return f"{hour:02d}:00–{hour + 1:02d}:00"


def _hm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


# ── Когда играют ──────────────────────────────────────────────────────────────

async def when_payload(session: AsyncSession, viewer: Player, who: str) -> dict | None:
    """Сетка «день недели × час» по завершённым матчам за WHEN_DAYS дней (МСК).
    who: club — весь клуб, me — матчи зрителя."""
    if who not in ("club", "me"):
        return None
    query = select(Match.completed_at).where(
        Match.status == MatchStatus.completed,
        Match.completed_at >= _now() - timedelta(days=WHEN_DAYS),
    )
    if who == "me":
        query = query.where(or_(Match.challenger_id == viewer.id, Match.challenged_id == viewer.id))
    times = [t + MSK_OFFSET for t in (await session.execute(query)).scalars().all() if t is not None]
    base = {"who": who, "title": "Когда играют", "period": f"за {WHEN_DAYS} дней"}
    if not times:
        empty = "У тебя за 90 дней матчей нет." if who == "me" else "За 90 дней матчей нет."
        return {**base, "empty": empty}

    grid = Counter((t.weekday(), t.hour) for t in times)
    lo = min(WHEN_MIN_HOUR, min(h for _, h in grid))
    hi = max(WHEN_MAX_HOUR, max(h for _, h in grid))
    top = max(grid.values())
    days = [d for d in range(7) if d < 5 or any(grid[(d, h)] for h in range(24))]

    def level(n: int) -> int:
        if n == 0:
            return 0
        return 1 if n * 3 <= top else 2 if n * 3 <= top * 2 else 3

    rows = [{"day": WEEKDAYS[d], "full": WEEKDAYS_FULL[d],
             "cells": [{"h": h, "n": grid[(d, h)], "lvl": level(grid[(d, h)])} for h in range(lo, hi + 1)]}
            for d in days]

    (peak_day, peak_hour), peak_n = max(grid.items(), key=lambda kv: (kv[1], -kv[0][0], -kv[0][1]))
    by_day = Counter(t.weekday() for t in times)
    best_day, best_day_n = max(by_day.items(), key=lambda kv: (kv[1], -kv[0]))
    by_hour = Counter(t.hour for t in times)
    best_hour = max(by_hour.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    per_day: dict = {}
    for t in times:
        per_day.setdefault(t.date(), []).append(t.hour * 60 + t.minute)
    first = median(min(v) for v in per_day.values())
    last = median(max(v) for v in per_day.values())

    facts = [
        {"label": "Пик", "value": f"{WEEKDAYS[peak_day]} {_slot(peak_hour)}", "note": pluralize_matches(peak_n)},
        {"label": "Самый активный день", "value": f"{WEEKDAYS_FULL[best_day]} · {best_day_n}"},
        {"label": "Самый популярный час", "value": _slot(best_hour)},
    ]
    if len(per_day) >= 3:
        verb = "Обычно играешь" if who == "me" else "Обычно играют"
        facts.append({"label": verb, "value": f"с {_hm(first)} до {_hm(last)}"})
    return {**base, "empty": "", "total": pluralize_matches(len(times)),
            "hours": list(range(lo, hi + 1)), "rows": rows, "facts": facts}


# ── Использование (только администратор) ─────────────────────────────────────

def _screen_label(action: str) -> str:
    if action.startswith(APP_PREFIX):
        screen = action[len(APP_PREFIX):]
        return APP_SCREEN_LABELS.get(screen, screen)
    return action_label(action)


async def usage_payload(session: AsyncSession) -> dict:
    """Отчёт по счётчику за USAGE_DAYS дней: итоги, по дням (бот / приложение),
    самые открываемые экраны бота и приложения. Без разбивки по людям."""
    now = _now()
    since = now - timedelta(days=USAGE_DAYS)
    events = (await session.execute(
        select(UsageEvent.user_id, UsageEvent.action, UsageEvent.created_at)
        .where(UsageEvent.created_at >= since)
    )).all()
    base = {"title": "Использование", "period": f"за {USAGE_DAYS} дней"}
    if not events:
        return {**base, "empty": "Событий за 30 дней нет."}

    app_events = [e for e in events if e.action.startswith(APP_PREFIX)]
    users = {e.user_id for e in events}
    app_users = {e.user_id for e in app_events}
    share = round(len(app_events) * 100 / len(events))
    tiles = [
        {"label": "События", "value": str(len(events))},
        {"label": "Игроки", "value": str(len(users))},
        {"label": "Доля приложения", "value": f"{share}%"},
        {"label": "В приложении", "value": str(len(app_users)), "sub": pluralize_players(len(app_users)).split(" ", 1)[1]},
    ]

    today = (now + MSK_OFFSET).date()
    days = [today - timedelta(days=i) for i in range(USAGE_DAYS - 1, -1, -1)]
    per_day = {d: [0, 0] for d in days}
    for e in events:
        d = (e.created_at + MSK_OFFSET).date()
        if d in per_day:
            per_day[d][1 if e.action.startswith(APP_PREFIX) else 0] += 1
    daily = [{"d": d.strftime("%d.%m"), "bot": v[0], "app": v[1]} for d, v in per_day.items()]

    def top(rows) -> list[dict]:
        opens = Counter(e.action for e in rows)
        people: dict[str, set] = {}
        last: dict[str, datetime] = {}
        for e in rows:
            people.setdefault(e.action, set()).add(e.user_id)
            last[e.action] = max(last.get(e.action, e.created_at), e.created_at)
        return [{"label": _screen_label(a),
                 "value": f"{n} · {_ru_plural(len(people[a]), 'игрок', 'игрока', 'игроков')}",
                 "note": "последнее " + (last[a] + MSK_OFFSET).strftime("%d.%m")}
                for a, n in opens.most_common(USAGE_TOP)]

    bot_events = [e for e in events if not e.action.startswith(APP_PREFIX)]
    return {**base, "empty": "", "tiles": tiles, "daily": daily,
            "bot": top(bot_events), "app": top(app_events), "boot": await boot_summary(session)}


BOOT_DAYS = 7
SLOW_BOOT_MS = 3000  # «медленный» запуск в «Использовании» (v2.180.0)


async def boot_summary(session: AsyncSession) -> list[dict]:
    """Запуск приложения за BOOT_DAYS дней (v2.177.0): медиана всего и по клиентам."""
    rows = (await session.execute(
        select(AppBoot.ms, AppBoot.platform, AppBoot.failed)
        .where(AppBoot.created_at >= _now() - timedelta(days=BOOT_DAYS))
    )).all()
    if not rows:
        return []

    def seconds(ms: float) -> str:
        return f"{ms / 1000:.1f} с".replace(".", ",")

    def line(label: str, group: list) -> dict:
        # v2.180.0: кроме медианы — 90-й процентиль, доля медленных и неудачные запуски
        values = sorted(r.ms for r in group if not r.failed)
        fails = sum(1 for r in group if r.failed)
        parts = [_ru_plural(len(values), "запуск", "запуска", "запусков")]
        if values:
            parts.append(f"90% — до {seconds(values[int(0.9 * (len(values) - 1))])}")
            slow = sum(1 for v in values if v > SLOW_BOOT_MS)
            if slow:
                parts.append(f"дольше {SLOW_BOOT_MS // 1000} с — {round(slow * 100 / len(values))}%")
        if fails:
            parts.append(f"не запустилось — {fails}")
        return {"label": label, "value": seconds(median(values)) if values else "—", "note": " · ".join(parts)}

    out = [line(f"Все клиенты, {BOOT_DAYS} дней", list(rows))]
    by_platform: dict[str, list] = {}
    for r in rows:
        by_platform.setdefault(r.platform or "неизвестно", []).append(r)
    for platform, group in sorted(by_platform.items(), key=lambda kv: -len(kv[1])):
        out.append(line(platform, group))
    return out


# ── Итоги года в приложении ───────────────────────────────────────────────────

async def _summary_year(session: AsyncSession) -> int:
    """Год экрана: год последнего снимка «Итогов года» (30.12), иначе текущий."""
    snap = (await session.execute(
        select(DigestSnapshot.created_at).where(DigestSnapshot.kind == "year")
        .order_by(DigestSnapshot.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    return (snap + MSK_OFFSET).year if snap else (_now() + MSK_OFFSET).year


def _votes_published(year: int) -> bool:
    month, day, hour, minute = YEAR_VOTE_CLOSE_AT
    return _now() + MSK_OFFSET >= datetime(year, month, day, hour, minute)


async def year_extras(session: AsyncSession, viewer: Player) -> dict:
    """«Твой год» (матчи, победы, рейтинг за год, ачивки, частый соперник) и,
    после закрытия голосования, неформальные звания года."""
    year = await _summary_year(session)
    start, end = _full_year_bounds_utc(year)
    out: dict = {"year": year}

    matches = (await session.execute(
        select(Match).where(
            Match.status == MatchStatus.completed,
            Match.completed_at >= start, Match.completed_at < end,
            or_(Match.challenger_id == viewer.id, Match.challenged_id == viewer.id),
        )
    )).scalars().all()
    if matches:
        wins = sum(1 for m in matches if m.winner_id == viewer.id)
        draws = sum(1 for m in matches if m.winner_id is None)
        delta = sum(match_rating_delta(m, viewer.id) for m in matches if m.rating_change is not None)
        earned = len((await session.execute(
            select(AchievementEarned.id).where(
                AchievementEarned.player_id == viewer.id,
                AchievementEarned.earned_at >= start, AchievementEarned.earned_at < end,
            )
        )).all())
        rivals = Counter(m.challenged_id if m.challenger_id == viewer.id else m.challenger_id for m in matches)
        rival_id, rival_n = rivals.most_common(1)[0]
        rival = await session.get(Player, rival_id)
        items = [
            {"label": "Матчей", "value": str(len(matches))},
            {"label": "Побед", "value": f"{wins} · {round(wins * 100 / len(matches))}%"},
            {"label": "Рейтинг за год", "value": f"{'+' if delta >= 0 else '−'}{abs(delta):.1f}"},
            {"label": "Ачивок получено", "value": str(earned)},
        ]
        if draws:
            items.insert(2, {"label": "Ничьих", "value": str(draws)})
        if rival:
            items.append({"label": "Чаще всего с", "value": rival.display_name, "note": pluralize_matches(rival_n)})
        out["mine"] = {"title": f"Твой {year}", "items": items}

    if _votes_published(year):
        results, total = await compute_results(session, year)
        if total:
            ids = {pid for _, winners, _ in results for pid in winners}
            names = {p.id: p.display_name for p in (await session.execute(
                select(Player).where(Player.id.in_(ids)))).scalars()} if ids else {}
            items = []
            for nomination, winners, count in results:
                if not winners:
                    continue
                who = " и ".join(names.get(pid, "?") for pid in winners)
                note = pluralize_votes(count) + (" у каждого" if len(winners) > 1 else "")
                items.append({"label": nomination.name, "value": who, "note": note})
            if items:
                out["vote"] = {"title": f"Неформальные звания {year}", "items": items}
    return out


# ── Кто с кем (v2.183.0) ──────────────────────────────────────────────────────

async def pairs_payload(session: AsyncSession) -> dict:
    """Матрица пар клуба за всё время: сколько матчей сыграла каждая пара и
    кто кого чаще обыгрывал. В клетке строки A и столбца B — матчи пары и
    победы A. Плюс самая частая пара и пары, которые ещё не играли."""
    rows = (await session.execute(
        select(Match.challenger_id, Match.challenged_id, Match.winner_id).where(Match.status == MatchStatus.completed)
    )).all()
    base = {"title": "Кто с кем", "period": "за всё время"}
    if not rows:
        return {**base, "empty": "Матчей пока не было."}
    games: dict[tuple[int, int], int] = {}
    wins: dict[tuple[int, int], int] = {}
    ids: set[int] = set()
    for a, b, w in rows:
        ids.update((a, b))
        key = (min(a, b), max(a, b))
        games[key] = games.get(key, 0) + 1
        if w is not None:
            loser = b if w == a else a
            wins[(w, loser)] = wins.get((w, loser), 0) + 1
    players = (await session.execute(select(Player).where(Player.id.in_(ids)))).scalars().all()
    players = sorted(players, key=lambda p: (-p.rating, p.id))
    top = max(games.values())

    def level(n: int) -> int:
        return 0 if n == 0 else 1 if n * 3 <= top else 2 if n * 3 <= top * 2 else 3

    people = [{"id": p.id, "name": p.display_name, "initial": (p.display_name.strip()[:1] or "?").upper()}
              for p in players]
    matrix = []
    for p in players:
        cells = []
        for q in players:
            if p.id == q.id:
                cells.append(None)
                continue
            n = games.get((min(p.id, q.id), max(p.id, q.id)), 0)
            cells.append({"n": n, "wins": wins.get((p.id, q.id), 0), "lvl": level(n)})
        matrix.append(cells)
    names = {p.id: p.display_name for p in players}
    (fa, fb), fn = max(games.items(), key=lambda kv: (kv[1], -kv[0][0], -kv[0][1]))
    facts = [{"label": "Чаще всех", "value": f"{names[fa]} — {names[fb]}", "note": pluralize_matches(fn)}]
    never = [(p, q) for i, p in enumerate(players) for q in players[i + 1:]
             if (min(p.id, q.id), max(p.id, q.id)) not in games]
    if never:
        shown = ", ".join(f"{p.display_name} — {q.display_name}" for p, q in never[:3])
        more = f" и ещё {len(never) - 3}" if len(never) > 3 else ""
        facts.append({"label": "Ещё не играли", "value": str(len(never)), "note": shown + more})
    return {**base, "empty": "", "players": people, "matrix": matrix, "facts": facts}


# ── Гонка рейтинга (v2.184.0) ─────────────────────────────────────────────────

RACE_DAYS = 30


async def race_payload(session: AsyncSession) -> dict:
    """Рейтинг каждого игрока на конец каждого дня за RACE_DAYS дней (МСК) —
    для анимированной «гонки». Считается назад от текущего рейтинга по
    изменениям в матчах (как график профиля). Игроки — с матчами за период."""
    today = (_now() + MSK_OFFSET).date()
    first_day = today - timedelta(days=RACE_DAYS - 1)
    start = datetime(first_day.year, first_day.month, first_day.day) - MSK_OFFSET
    matches = (await session.execute(
        select(Match).where(Match.status == MatchStatus.completed, Match.completed_at >= start,
                            Match.rating_change.is_not(None))
    )).scalars().all()
    base = {"title": "Гонка рейтинга", "period": f"за {RACE_DAYS} дней"}
    if not matches:
        return {**base, "empty": "За 30 дней матчей не было."}
    ids = {pid for m in matches for pid in (m.challenger_id, m.challenged_id)}
    players = sorted((await session.execute(select(Player).where(Player.id.in_(ids)))).scalars().all(),
                     key=lambda p: (-p.rating, p.id))
    # изменение рейтинга каждого игрока по дням
    per_day: dict[tuple[int, object], float] = {}
    for m in matches:
        day = (m.completed_at + MSK_OFFSET).date()
        for pid in (m.challenger_id, m.challenged_id):
            per_day[(pid, day)] = per_day.get((pid, day), 0.0) + match_rating_delta(m, pid)
    days = [first_day + timedelta(days=i) for i in range(RACE_DAYS)]
    frames: list[list[float]] = [[0.0] * len(players) for _ in days]
    for col, p in enumerate(players):
        value = p.rating
        for i in range(len(days) - 1, -1, -1):          # с сегодня назад
            frames[i][col] = round(value, 1)
            value -= per_day.get((p.id, days[i]), 0.0)
    return {**base, "empty": "",
            "players": [{"id": p.id, "name": p.display_name, "initial": (p.display_name.strip()[:1] or "?").upper()}
                        for p in players],
            "days": [d.strftime("%d.%m") for d in days], "frames": frames}
