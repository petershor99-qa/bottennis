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

from bot.db.models import AchievementEarned, Match, MatchStatus, PersonalRecordEarned, Player
from bot.services.achievements import ACHIEVEMENTS_MAP
from bot.utils import MSK_OFFSET, _my_opp_points, _ru_plural, compute_alltime_streak, match_rating_delta
from bot.webapp.achievement_icons import ACHIEVEMENT_ICONS
from bot.webapp.api import _club_match, _person, _signed, achievement_tier
from bot.webapp.api_club import _now
from bot.webapp.api_day import _matches_between, _standings_at

MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
              "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_ACC = MONTHS_NOM   # «На ноябрь» — винительный совпадает с именительным
MOOD_TITLES = {"comeback": "Месяц камбэков", "rally": "Месяц отыгрышей", "blowout": "Месяц разгромов",
               "deuce": "Месяц дьюсов", "boss": "Месяц боссфайтов"}
MOOD_ORDER = ("comeback", "boss", "blowout", "deuce", "rally")
MOOD_MIN = 4          # «Характер месяца» — от 4 таких матчей и больше трети месяца
MONTH_READY_DAYS = 3  # карточка «Твой октябрь готов» на главной — первые дни месяца
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


def match_mood(m: Match) -> str:
    """Характер матча — как штамп в приложении (`matchMood`, 20-match.js);
    sets_data — в перспективе победителя."""
    sets = m.sets_data or []
    if m.winner_id is None or not sets:
        return ""
    lost_first = sets[0]["l"] > sets[0]["w"]
    lost_two = len(sets) >= 3 and lost_first and sets[1]["l"] > sets[1]["w"]
    all_won = all(s["w"] > s["l"] for s in sets)
    margin = sum(s["w"] - s["l"] for s in sets) / len(sets)
    deuces = sum(1 for s in sets if max(s["w"], s["l"]) > 11)
    if m.is_boss_fight:
        return "boss"
    if lost_two:
        return "comeback"
    if lost_first and len(sets) >= 3:
        return "rally"
    if all_won and margin >= 6:
        return "blowout"
    if deuces >= 2:
        return "deuce"
    return ""


def month_character(matches: list[Match], pid: int) -> tuple[str, int] | None:
    """Самый частый характер выигранных матчей месяца: от MOOD_MIN и больше трети."""
    counts: dict[str, int] = {}
    for m in matches:
        mood = match_mood(m)
        if mood and (m.winner_id == pid or mood == "boss"):
            counts[mood] = counts.get(mood, 0) + 1
    if not counts:
        return None
    best = max(counts, key=lambda k: (counts[k], -MOOD_ORDER.index(k)))
    n = counts[best]
    return (best, n) if n >= MOOD_MIN and n * 3 > len(matches) else None


def _days(matches: list[Match], pid: int) -> dict[date, list]:
    days: dict[date, list] = {}
    for m in matches:
        d = days.setdefault((m.completed_at + MSK_OFFSET).date(), [0.0, 0, 0])
        d[0] += match_rating_delta(m, pid)
        d[1] += 1
        d[2] += m.winner_id == pid
    return days


def _day_card(title: str, day: date, v: list, up: bool) -> dict:
    return {"kind": "day", "title": title, "big": _signed(round(v[0], 1)), "up": up,
            "date": day.strftime("%d.%m"), "route": "summary/day/" + day.strftime("%Y%m%d"),
            "lines": [_ru_plural(v[1], "матч", "матча", "матчей") + " · " + _ru_plural(v[2], "победа", "победы", "побед")]}


def _numbers(matches: list[Match], pid: int) -> list[str]:
    from bot.webapp.api_personal import signature_score

    points = sets = dw = dt = 0
    for m in matches:
        for s in m.sets_data or []:
            mine, theirs = _my_opp_points(m, s, pid)
            points += mine
            sets += 1
            if max(mine, theirs) > 11:
                dt += 1
                dw += mine > theirs
    lines = [f"Очков: {points}", f"Партий: {sets}"]
    if dt:
        lines.append(f"Дьюсов выиграно: {dw} из {dt}")
    streak = compute_alltime_streak(matches, pid)
    if streak >= 2:
        lines.append(f"Лучшая серия: {_ru_plural(streak, 'победа', 'победы', 'побед')}")
    sig = signature_score(matches, pid)
    if sig and sig["score"]:
        lines.append(f"Коронный счёт: {sig['score']}")
    return lines


async def month_ready(session: AsyncSession, player: Player) -> dict | None:
    """Карточка на главной в первые дни месяца (v2.202.0): прошлый месяц игрока готов."""
    today = (_now() + MSK_OFFSET).date()
    if today.day > MONTH_READY_DAYS:
        return None
    py_, pm = _shift(today.year, today.month, -1)
    if not await _player_matches(session, player.id, _month_start(py_, pm), _month_start(today.year, today.month)):
        return None
    return {"ym": f"{py_}{pm:02d}", "title": f"Твой {MONTHS_NOM[pm - 1]} готов",
            "sub": "Месяц в карточках — посмотри, как он прошёл"}


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

    # v2.202.0: календарь, месяц в цифрах, лучший и худший день
    days = _days(matches, pid)
    month_days = (date(ny, nm, 1) - date(year, month, 1)).days
    cards.append({"kind": "calendar", "title": "Календарь месяца", "offset": date(year, month, 1).weekday(),
                  "days": [("w" if days[d][0] > 0 else "l" if days[d][0] < 0 else "e") if d in days else ""
                           for d in (date(year, month, i + 1) for i in range(month_days))],
                  "lines": [f"Игровых дней: {len(days)}"]})
    cards.append({"kind": "numbers", "title": "Месяц в цифрах", "lines": _numbers(matches, pid)})
    best_day = max(days, key=lambda d: (round(days[d][0], 1), d))
    if days[best_day][0] > 0:
        cards.append(_day_card("Лучший день", best_day, days[best_day], True))
    worst_day = min(days, key=lambda d: (round(days[d][0], 1), d))
    if days[worst_day][0] < 0 and worst_day != best_day:
        cards.append(_day_card("День, который лучше забыть", worst_day, days[worst_day], False))

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

    # Характер месяца (v2.202.0)
    character = month_character(matches, pid)
    if character:
        cards.append({"kind": "character", "title": "Характер месяца", "mood": character[0],
                      "stamp": MOOD_TITLES[character[0]],
                      "lines": [f"{_ru_plural(character[1], 'матч', 'матча', 'матчей')} из {len(matches)}"]})

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

    # Прирост в клубе (v2.202.0): место по сумме изменений рейтинга за месяц
    gain: dict[int, float] = {}
    for m in club_month:
        for p_id in (m.challenger_id, m.challenged_id):
            gain[p_id] = gain.get(p_id, 0.0) + match_rating_delta(m, p_id)
    if len(gain) >= 2 and pid in gain:
        order = sorted(gain, key=lambda p_id: (-gain[p_id], p_id))
        cards.append({"kind": "gain", "title": "Прирост в клубе", "big": f"#{order.index(pid) + 1}",
                      "up": gain[pid] > 0, "lines": [f"из {len(order)} по приросту рейтинга"]})

    # Личные рекорды месяца (v2.202.0)
    from bot.webapp.api_personal import RECORD_TITLES

    best_records: dict[str, float] = {}
    for r in (await session.execute(select(PersonalRecordEarned).where(
            PersonalRecordEarned.player_id == pid, PersonalRecordEarned.earned_at >= start,
            PersonalRecordEarned.earned_at < end))).scalars():
        best_records[r.metric] = max(best_records.get(r.metric, 0), r.value)
    if best_records:
        cards.append({"kind": "records", "title": "Личные рекорды", "lines": [
            f"{RECORD_TITLES.get(k, k)}: {int(v)}" for k, v in list(best_records.items())[:5]]})

    # Цели недели за месяц (v2.202.0): закрытые недели, начавшиеся в этом месяце
    from bot.webapp.api_return import closed_weeks

    closed, weeks = await closed_weeks(session, player, start, end)
    if weeks:
        cards.append({"kind": "goals", "title": "Цели недели", "big": f"{closed} из {weeks}",
                      "up": closed * 2 >= weeks, "big_label": "недель закрыто"})

    # Сравнение с прошлым месяцем
    prev = await _player_matches(session, pid, prev_start, start)
    if prev:
        pw, _pl, _pd = _record(prev, pid)
        now_rate, prev_rate = round(w / len(matches) * 100), round(pw / len(prev) * 100)
        cards.append({"kind": "compare", "title": f"А в {MONTHS_PREP[pm - 1]}",
                      "lines": [f"Матчей: {len(prev)} → {len(matches)}", f"Побед: {prev_rate}% → {now_rate}%"],
                      "big": _signed(now_rate - prev_rate).replace(".0", "") + " п.п.", "up": now_rate >= prev_rate})

    # «На ноябрь» (v2.202.0): ориентир — только у текущего и только что закрытого месяца
    if date(ny, nm, 1) >= date(today.year, today.month, 1):
        from bot.handlers.profile import _rank_title_progress_line
        from bot.services.stats import _nearest_achievement_progress
        from bot.utils import get_career_matches
        from bot.webapp.api import stats_cached
        from bot.webapp.text import plain

        career = await get_career_matches(session, pid, with_opponents=True)
        aims = [_rank_title_progress_line(player.rating),
                _nearest_achievement_progress(player, stats_cached(player, career), len(players))]
        aims = [plain(x.split("\n")[0]) for x in aims if x]
        if aims:
            cards.append({"kind": "next", "title": f"На {MONTHS_ACC[nm - 1]}", "lines": aims})

    # Прощальная карточка (v2.194.0): картинка заставки и итог месяца одной строкой
    cards.append(outro_card(len(matches), w, ny, nm, finished=end <= _now()))

    # «Месяц одной картинкой» (v2.202.0): главное цифрами и «Поделиться картинкой»
    stats = [["Матчей", str(len(matches))], ["Побед", f"{round(w / len(matches) * 100)}%"],
             ["Рейтинг", _signed(round(delta, 1))]]
    if place_end:
        stats.append(["Место", f"#{place_end}"])
    stats.append(["Ачивки", str(len(achs))])
    stats.append(["Игровых дней", str(len(days))])
    cards.append({"kind": "final", "title": title, "player": _person(player), "stats": stats,
                  "confetti": delta > 0})
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
