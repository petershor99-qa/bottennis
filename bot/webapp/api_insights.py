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

from bot.db.models import AchievementEarned, DigestSnapshot, Match, MatchStatus, Player, UsageEvent
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
            "bot": top(bot_events), "app": top(app_events)}


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
