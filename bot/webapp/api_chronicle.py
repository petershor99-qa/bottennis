"""
Mini App, v2.177.0: летопись клуба (ключевые события за всё время), счета клуба
(частые счета партий, дьюсы, сухие партии) и история дуэли двух игроков для
экрана «Ты против …». Только чтение.
"""
from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import ChampionReign, ClubRecordPing, Match, MatchStatus, Player
from bot.utils import MSK_OFFSET, _my_opp_points, _ru_plural, match_drama_score
from bot.webapp.api import _RECORD_TITLES, _club_match, _msk_date, _person

CLUB_MILESTONES = (1, 100, 250, 500, 750, 1000, 1500, 2000, 3000, 5000)
PLAYER_MILESTONES = (100, 250, 500, 750, 1000)
CHRONICLE_LIMIT = 200
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
          "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _month_title(dt: datetime) -> str:
    d = dt + MSK_OFFSET
    return f"{MONTHS[d.month - 1]} {d.year}"


async def _all_matches(session: AsyncSession) -> list[Match]:
    return list((await session.execute(
        select(Match).where(Match.status == MatchStatus.completed, Match.completed_at.is_not(None))
        .order_by(Match.completed_at, Match.id)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )).scalars().all())


# ── Летопись клуба ────────────────────────────────────────────────────────────

async def chronicle_payload(session: AsyncSession) -> dict:
    matches = await _all_matches(session)
    if not matches:
        return {"months": [], "empty": "Летопись начнётся с первого матча."}
    players = {p.id: p for p in (await session.execute(select(Player))).scalars()}
    events: list[tuple[datetime, dict]] = []

    played: Counter = Counter()
    for n, m in enumerate(matches, start=1):
        if n in CLUB_MILESTONES:
            title = "Первый матч клуба" if n == 1 else f"{n}-й матч клуба"
            events.append((m.completed_at, {"kind": "club", "icon": "ball-tennis", "title": title,
                                            "match": _club_match(m)}))
        for pid in (m.challenger_id, m.challenged_id):
            played[pid] += 1
            p = players.get(pid)
            if p is None:
                continue
            if played[pid] == 1 and n > 1:
                events.append((m.completed_at, {"kind": "debut", "icon": "user", "player": _person(p),
                                                "title": f"{p.display_name}: первый матч в клубе"}))
            elif played[pid] in PLAYER_MILESTONES:
                events.append((m.completed_at, {"kind": "milestone", "icon": "flag", "player": _person(p),
                                                "title": f"{p.display_name}: {played[pid]}-й матч"}))

    reigns = (await session.execute(select(ChampionReign).order_by(ChampionReign.started_at))).scalars().all()
    for i, r in enumerate(reigns):
        p = players.get(r.player_id)
        if p is None:
            continue
        title = f"{p.display_name} — первый чемпион клуба" if i == 0 else f"{p.display_name} занимает трон"
        events.append((r.started_at, {"kind": "throne", "icon": "crown", "player": _person(p), "title": title}))

    for ping in (await session.execute(select(ClubRecordPing))).scalars():
        p = players.get(ping.player_id)
        title, fmt = _RECORD_TITLES.get(ping.kind, ("Рекорд клуба", lambda v: f"{v:.1f}"))
        events.append((ping.pinged_at, {"kind": "record", "icon": "trophy", "title": title,
                                        "sub": " · ".join(x for x in (p.display_name if p else "", fmt(ping.value)) if x),
                                        "player": _person(p) if p else None}))

    events.sort(key=lambda e: e[0], reverse=True)
    months: list[dict] = []
    for when, item in events[:CHRONICLE_LIMIT]:
        item["date"] = _msk_date(when, "%d.%m.%y")
        title = _month_title(when)
        if not months or months[-1]["title"] != title:
            months.append({"title": title, "items": []})
        months[-1]["items"].append(item)
    first = matches[0].completed_at
    return {"months": months, "empty": "",
            "subtitle": f"с {_msk_date(first, '%d.%m.%Y')} · {_ru_plural(len(matches), 'матч', 'матча', 'матчей')}"}


# ── Счета клуба ───────────────────────────────────────────────────────────────

async def scores_payload(session: AsyncSession) -> dict:
    matches = await _all_matches(session)
    sets = [(s["w"], s["l"]) for m in matches for s in (m.sets_data or [])]
    if not sets:
        return {"empty": "Партий ещё не было."}
    total = len(sets)
    norm = Counter(f"{max(a, b)}:{min(a, b)}" for a, b in sets)
    deuce = sum(1 for a, b in sets if max(a, b) > 11)
    shutouts = sum(1 for a, b in sets if min(a, b) == 0)
    longest = max(sets, key=lambda x: (x[0] + x[1], max(x)))
    by_sets = Counter(f"{sum(1 for s in m.sets_data if s['w'] > s['l'])}:{sum(1 for s in m.sets_data if s['l'] > s['w'])}"
                      for m in matches if m.sets_data and m.winner_id is not None)
    top = [{"score": sc, "count": n, "share": round(n / total * 100)} for sc, n in norm.most_common(6)]
    return {
        "empty": "",
        "subtitle": _ru_plural(total, "партия", "партии", "партий"),
        "top": top,
        "facts": [
            {"label": "На дьюсе", "value": f"{round(deuce / total * 100)}%",
             "sub": _ru_plural(deuce, "партия", "партии", "партий")},
            {"label": "Очков в партии", "value": f"{sum(a + b for a, b in sets) / total:.1f}".replace(".", ","),
             "sub": "в среднем"},
            {"label": "Самая длинная", "value": f"{max(longest)}:{min(longest)}", "sub": "партия клуба"},
            {"label": "Всухую", "value": str(shutouts), "sub": "партий 11:0"},
        ],
        "match_scores": [{"score": sc, "count": n} for sc, n in by_sets.most_common(4)],
    }


# ── История дуэли (экран «Ты против …») ───────────────────────────────────────

def duel_history(matches: list[Match], me: int) -> dict | None:
    """matches — встречи пары в любом порядке. Баланс побед по времени, лучшие
    серии каждого, лучший матч пары и разбивка по месяцам."""
    ordered = sorted((m for m in matches if m.completed_at), key=lambda m: (m.completed_at, m.id))
    if len(ordered) < 2:
        return None
    balance, run = [], 0
    best = {"me": 0, "them": 0}
    cur_who, cur_len = None, 0
    months: dict[tuple, dict] = {}
    for m in ordered:
        who = None if m.winner_id is None else ("me" if m.winner_id == me else "them")
        run += 1 if who == "me" else (-1 if who == "them" else 0)
        balance.append({"d": _msk_date(m.completed_at), "v": run})
        if who and who == cur_who:
            cur_len += 1
        else:
            cur_who, cur_len = who, (1 if who else 0)
        if who:
            best[who] = max(best[who], cur_len)
        d = m.completed_at + MSK_OFFSET
        row = months.setdefault((d.year, d.month), {"m": f"{MONTHS_SHORT[d.month - 1]} {str(d.year)[2:]}",
                                                    "me": 0, "them": 0})
        if who:
            row[who] += 1
    top = max(ordered, key=lambda m: (match_drama_score(m), m.completed_at))
    points_me = sum(_my_opp_points(m, s, me)[0] for m in ordered for s in (m.sets_data or []))
    points_all = sum(s["w"] + s["l"] for m in ordered for s in (m.sets_data or []))
    return {
        "balance": balance,
        "best_streak_me": best["me"], "best_streak_them": best["them"],
        "best_match": {**_club_match(top), "date": _msk_date(top.completed_at, "%d.%m.%y")},
        "months": [months[k] for k in sorted(months)][-6:],
        "points_share": round(points_me / points_all * 100) if points_all else None,
    }
