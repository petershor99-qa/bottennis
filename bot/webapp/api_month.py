"""
«Твой месяц» в Mini App (v2.177.0): короткая история месяца игрока карточками,
как сторис — открывается только самим игроком, без уведомлений. Считается на
лету по базе: матчи и победы, рейтинг за месяц, лучший матч, соперник месяца,
новые ачивки, место в таблице, сравнение с прошлым месяцем; последней —
прощальная карточка с картинкой заставки (v2.194.0).
"""
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import AchievementEarned, Match, MatchStatus, Player
from bot.services.achievements import ACHIEVEMENTS_MAP
from bot.utils import MSK_OFFSET, _ru_plural, match_rating_delta
from bot.webapp.achievement_icons import ACHIEVEMENT_ICONS
from bot.webapp.api import _club_match, _person, _signed, achievement_tier
from bot.webapp.api_club import _now
from bot.webapp.api_day import _matches_between, _standings_at

MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
              "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе",
               "сентябре", "октябре", "ноябре", "декабре"]


def _month_start(year: int, month: int) -> datetime:
    """Начало месяца по МСК в UTC-naive."""
    return datetime(year, month, 1) - MSK_OFFSET


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


async def _player_matches(session: AsyncSession, player_id: int, start: datetime, end: datetime) -> list[Match]:
    return list((await session.execute(
        select(Match).where(
            Match.status == MatchStatus.completed, Match.completed_at >= start, Match.completed_at < end,
            or_(Match.challenger_id == player_id, Match.challenged_id == player_id),
        ).order_by(Match.completed_at).options(selectinload(Match.challenger), selectinload(Match.challenged))
    )).scalars().all())


def _record(matches: list[Match], pid: int) -> tuple[int, int, int]:
    w = sum(1 for m in matches if m.winner_id == pid)
    d = sum(1 for m in matches if m.winner_id is None)
    return w, len(matches) - w - d, d


async def month_payload(session: AsyncSession, player: Player, ym: str | None) -> dict | None:
    today = (_now() + MSK_OFFSET).date()
    if ym:
        if len(ym) != 6 or not ym.isdigit():
            return None
        year, month = int(ym[:4]), int(ym[4:])
        if not 1 <= month <= 12 or date(year, month, 1) > today:
            return None
    else:
        year, month = today.year, today.month
        start = _month_start(year, month)
        if not await _player_matches(session, player.id, start, _now() + timedelta(seconds=1)):
            year, month = _shift(year, month, -1)   # в этом месяце ещё не играл — прошлый
    start = _month_start(year, month)
    ny, nm = _shift(year, month, 1)
    end = _month_start(ny, nm)
    py_, pm = _shift(year, month, -1)
    prev_start = _month_start(py_, pm)
    matches = await _player_matches(session, player.id, start, end)
    title = f"Твой {MONTHS_NOM[month - 1]}"
    nav = {"ym": f"{year}{month:02d}", "label": f"{MONTHS_NOM[month - 1].capitalize()} {year}",
           "prev": f"{py_}{pm:02d}",
           "next": f"{ny}{nm:02d}" if date(ny, nm, 1) <= today else None}
    if not matches:
        return {"title": title, **nav, "cards": [],
                "empty": f"В {MONTHS_PREP[month - 1]} ты не играл."}
    pid = player.id
    w, lost, d = _record(matches, pid)
    cards: list[dict] = []
    cards.append({"kind": "intro", "title": title, "big": str(len(matches)),
                  "big_label": _ru_plural(len(matches), "матч", "матча", "матчей").split(" ", 1)[-1],
                  "lines": [f"{_ru_plural(w, 'победа', 'победы', 'побед')} · "
                            f"{_ru_plural(lost, 'поражение', 'поражения', 'поражений')}"
                            + (f" · ничьих {d}" if d else ""),
                            f"Побед {round(w / len(matches) * 100)}%"]})

    # Рейтинг за месяц: ряд по матчам, откат от текущего рейтинга
    later = await _player_matches(session, pid, end, _now() + timedelta(days=1))
    after_month = player.rating - sum(match_rating_delta(m, pid) for m in later)
    delta = sum(match_rating_delta(m, pid) for m in matches)
    series, value = [], after_month - delta
    for m in matches:
        value += match_rating_delta(m, pid)
        series.append(round(value, 1))
    cards.append({"kind": "rating", "title": "Рейтинг", "big": _signed(round(delta, 1)), "up": delta >= 0,
                  "lines": [f"{after_month - delta:.1f} → {after_month:.1f}"], "series": series})

    # Лучший матч — самая большая прибавка
    wins = [m for m in matches if m.winner_id == pid and m.rating_change is not None]
    if wins:
        best = max(wins, key=lambda m: (m.rating_change, m.completed_at))
        cards.append({"kind": "match", "title": "Лучший матч", "match": _club_match(best),
                      "big": f"+{best.rating_change:.1f}", "up": True,
                      "lines": [(best.completed_at + MSK_OFFSET).strftime("%d.%m")]})

    # Соперник месяца
    opp: dict[int, list[Match]] = {}
    for m in matches:
        opp.setdefault(m.challenged_id if m.challenger_id == pid else m.challenger_id, []).append(m)
    rival_id = max(opp, key=lambda o: (len(opp[o]), opp[o][-1].completed_at))
    rw, rl, rd = _record(opp[rival_id], pid)
    rival = opp[rival_id][0].challenged if opp[rival_id][0].challenger_id == pid else opp[rival_id][0].challenger
    cards.append({"kind": "rival", "title": "Соперник месяца", "player": _person(rival),
                  "big": f"{rw}:{rl}", "up": rw >= rl,
                  "lines": [_ru_plural(len(opp[rival_id]), "встреча", "встречи", "встреч") + (f", ничьих {rd}" if rd else "")]})

    # Ачивки месяца
    rows = (await session.execute(
        select(AchievementEarned).where(AchievementEarned.player_id == pid,
                                        AchievementEarned.earned_at >= start, AchievementEarned.earned_at < end)
        .order_by(AchievementEarned.earned_at)
    )).scalars().all()
    achs = [ACHIEVEMENTS_MAP[r.achievement_id] for r in rows if r.achievement_id in ACHIEVEMENTS_MAP]
    if achs:
        cards.append({"kind": "achievements", "title": "Новые ачивки",
                      "big": str(len(achs)), "big_label": _ru_plural(len(achs), "ачивка", "ачивки", "ачивок").split(" ", 1)[-1],
                      "items": [{"name": a.name, "icon": ACHIEVEMENT_ICONS.get(a.id, "trophy"),
                                 "tier": achievement_tier(a.id)} for a in achs[:6]]})

    # Место в таблице на начало и конец месяца
    players = list((await session.execute(select(Player))).scalars().all())
    club_after = await _matches_between(session, end, _now() + timedelta(days=1))
    club_month = await _matches_between(session, start, end)
    moment_end = min(end, _now() + timedelta(seconds=1))
    place_end = (await _standings_at(session, players, moment_end, club_after)).get(pid)
    place_start = (await _standings_at(session, players, start, club_month + club_after)).get(pid)
    if place_end:
        line = "в начале месяца — без места" if not place_start else f"в начале месяца — #{place_start}"
        cards.append({"kind": "place", "title": "Место в клубе", "big": f"#{place_end}",
                      "up": not place_start or place_end <= place_start, "lines": [line]})

    # Сравнение с прошлым месяцем
    prev = await _player_matches(session, pid, prev_start, start)
    if prev:
        pw, _pl, _pd = _record(prev, pid)
        now_rate, prev_rate = round(w / len(matches) * 100), round(pw / len(prev) * 100)
        cards.append({"kind": "compare", "title": f"А в {MONTHS_PREP[pm - 1]}",
                      "lines": [f"Матчей: {len(prev)} → {len(matches)}", f"Побед: {prev_rate}% → {now_rate}%"],
                      "big": _signed(now_rate - prev_rate).replace(".0", "") + " п.п.", "up": now_rate >= prev_rate})

    # Прощальная карточка (v2.194.0): картинка заставки и итог месяца одной строкой
    cards.append(outro_card(len(matches), w, ny, nm, finished=end <= _now()))
    return {"title": title, **nav, "cards": cards, "empty": ""}


def outro_card(played: int, won: int, next_year: int, next_month: int, finished: bool) -> dict:
    """Последняя карточка «Твоего месяца»: картинка заставки (`art` — адрес
    страница берёт свой, уже из кэша), итог и «до встречи». Закрытый месяц —
    «До встречи в ноябре!», текущий — «Месяц ещё идёт»."""
    summary = (f"{_ru_plural(played, 'матч', 'матча', 'матчей')} · "
               f"{_ru_plural(won, 'победа', 'победы', 'побед')}")
    if finished:
        return {"kind": "outro", "art": True, "title": "Месяц закрыт",
                "lines": [summary, f"До встречи в {MONTHS_PREP[next_month - 1]}!"]}
    return {"kind": "outro", "art": True, "title": "Месяц ещё идёт",
            "lines": [summary, "Увидимся за столом!"]}
