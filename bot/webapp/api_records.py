"""
«Рекорды клуба» в приложении как витрина (v2.199.0): раньше экран был списком
строк бота. Теперь главные рекорды — данными (число, владелец, дата), чтобы
показать их карточками: главный рекорд, «Под угрозой» (кто догоняет, с полоской),
плитки, «Рекордсмены». Правила рекордов те же, что в боте (`_collect_club_records`):
названия и пороги совпадают; остальные рекорды остаются строками ниже.
Тексты утверждены владельцем 2026-10-16.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import ChampionReign, Match, MatchStatus, Player
from bot.utils import (
    MSK_OFFSET,
    _ru_plural,
    longest_champion_reign,
    match_rating_delta,
    most_boss_fight_defenses,
    pluralize_days,
)

NEW_DAYS = 7              # метка «новый» — рекорд установлен за последние 7 дней
THREAT_SHARE = 0.5        # «Под угрозой» — догоняющий прошёл хотя бы половину пути
UPSET_MIN = 15            # как у рекорда «Крупнейший апсет» в боте
HOT_DAY_MIN = 3           # как у «Самого жаркого дня клуба»
HOLDERS_MAX = 6

# Заголовки — те же, что у строк рекордов в боте: по ним строки, ставшие
# карточками, убираются из списка ниже (иначе рекорд был бы дважды)
TITLES = {
    "peak": "Высший рейтинг в истории",
    "matches": "Больше всего матчей",
    "streak": "Лучшая серия побед",
    "reign": "Дольше всех лидировал",
    "defenses": "Больше всего защит трона подряд",
    "upset": "Крупнейший апсет",
    "hot_day": "Самый жаркий день клуба",
    "derby": "Дерби клуба",
    "longest": "Самый длинный матч",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _person(p: Player) -> dict:
    return {"id": p.id, "name": p.display_name, "initial": (p.display_name.strip()[:1] or "?").upper()}


def _date(dt: datetime | None) -> str:
    return (dt + MSK_OFFSET).strftime("%d.%m") if dt else ""


def held_text(since: datetime | None, now: datetime) -> str:
    """«держится 4 мес · с 04.06» (меньше месяца — в днях)."""
    if since is None:
        return ""
    days = max(0, (now - since).days)
    span = pluralize_days(days) if days < 30 else f"{days // 30} мес"
    return f"держится {span} · с {_date(since)}"


def peak_reached_at(player: Player, matches_desc: list[Match]) -> datetime | None:
    """Когда игрок впервые вышел на свой пик рейтинга — откатом рейтинга назад по
    матчам (как места на момент в api_day). Пол 900/1000 откат искажает — тогда
    совпадения нет и даты нет (не выдумываем)."""
    peak = player.peak_rating if player.peak_rating is not None else player.rating
    rating, hit = player.rating, None
    for m in matches_desc:
        if abs(rating - peak) < 0.05:
            hit = m.completed_at
        if m.rating_change is None:
            continue
        rating -= match_rating_delta(m, player.id)
    return hit


def _best_streak(matches_asc: list[Match], pid: int) -> tuple[int, datetime | None]:
    """(лучшая серия побед, когда она впервые достигла этой длины)."""
    best, cur, at = 0, 0, None
    for m in matches_asc:
        if m.winner_id == pid:
            cur += 1
            if cur > best:
                best, at = cur, m.completed_at
        else:
            cur = 0
    return best, at


def _current_streak(matches_asc: list[Match], pid: int) -> int:
    n = 0
    for m in reversed(matches_asc):
        if m.winner_id != pid:
            break
        n += 1
    return n


def _record(key: str, icon: str, value: str, unit: str, holders: list[dict], set_at: datetime | None,
            now: datetime, route: str = "") -> dict:
    return {"key": key, "title": TITLES[key], "icon": icon, "value": value, "unit": unit, "holders": holders,
            "date": _date(set_at), "held": held_text(set_at, now),
            "new": bool(set_at and now - set_at <= timedelta(days=NEW_DAYS)), "route": route}


def _threat(label: str, cur: int, best: int, note: str, tone: str, player_id: int) -> dict:
    return {"label": label, "progress": round(min(1.0, cur / best), 3) if best else 0.0, "note": note,
            "tone": tone, "player_id": player_id}


async def showcase(session: AsyncSession) -> dict | None:
    """Главный рекорд, «Под угрозой», плитки и «Рекордсмены». None — матчей не было."""
    now = _now()
    matches = list((await session.execute(
        select(Match).where(Match.status == MatchStatus.completed, Match.completed_at.is_not(None))
        .options(selectinload(Match.challenger), selectinload(Match.challenged)).order_by(Match.completed_at)
    )).scalars().all())
    if not matches:
        return None
    players = {p.id: p for p in (await session.execute(select(Player))).scalars().all()}
    by_player: dict[int, list[Match]] = defaultdict(list)
    for m in matches:
        by_player[m.challenger_id].append(m)
        by_player[m.challenged_id].append(m)
    active = [players[pid] for pid in by_player if pid in players]

    def person(pid: int) -> dict:
        return _person(players[pid]) if pid in players else {"id": pid, "name": "?", "initial": "?"}

    hero = None
    tiles: list[dict] = []
    single: Counter = Counter()          # рекорды одного игрока — для «Рекордсменов»

    # Высший рейтинг в истории — главный рекорд
    peak_p = max(active, key=lambda p: (p.peak_rating if p.peak_rating is not None else p.rating), default=None)
    if peak_p is not None:
        peak = peak_p.peak_rating if peak_p.peak_rating is not None else peak_p.rating
        hero = _record("peak", "trophy", f"{peak:.1f}", "pts", [person(peak_p.id)],
                       peak_reached_at(peak_p, list(reversed(by_player[peak_p.id]))), now, f"player/{peak_p.id}")
        single[peak_p.id] += 1

    counts = {pid: len(ms) for pid, ms in by_player.items()}
    ranked = sorted(counts, key=lambda pid: (-counts[pid], pid))
    most = ranked[0]
    tiles.append(_record("matches", "calendar", str(counts[most]), _ru_plural(counts[most], "матч", "матча", "матчей")
                         .split(" ", 1)[1], [person(most)], None, now, f"player/{most}"))
    single[most] += 1

    streaks = {pid: _best_streak(ms, pid) for pid, ms in by_player.items()}
    best_pid = min(streaks, key=lambda pid: (-streaks[pid][0], streaks[pid][1] or now))
    best_n, best_at = streaks[best_pid]
    if best_n >= 2:
        tiles.append(_record("streak", "flame", str(best_n), "подряд", [person(best_pid)], best_at, now,
                             f"player/{best_pid}"))
        single[best_pid] += 1

    reign = await longest_champion_reign(session)
    if reign is not None:
        tiles.append(_record("reign", "crown", str(reign[1]), _ru_plural(reign[1], "день", "дня", "дней").split(" ", 1)[1],
                             [person(reign[0])], None, now, "throne"))
        single[reign[0]] += 1
    defenses = await most_boss_fight_defenses(session)
    if defenses is not None:
        tiles.append(_record("defenses", "shield-check", str(defenses[1]), "подряд", [person(defenses[0])], None, now,
                             "throne"))
        single[defenses[0]] += 1

    upsets = [m for m in matches if m.rating_change is not None and m.winner_id is not None and not m.is_boss_fight]
    if upsets:
        up = max(upsets, key=lambda m: m.rating_change)
        if up.rating_change >= UPSET_MIN:
            loser = up.challenged_id if up.winner_id == up.challenger_id else up.challenger_id
            tiles.append(_record("upset", "bolt", f"+{up.rating_change:.1f}", "pts", [person(up.winner_id), person(loser)],
                                 up.completed_at, now, f"match/{up.id}"))
            single[up.winner_id] += 1

    days: dict[str, list[Match]] = defaultdict(list)
    for m in matches:
        days[(m.completed_at + MSK_OFFSET).strftime("%Y%m%d")].append(m)
    hot_key = max(days, key=lambda k: (len(days[k]), k))
    if len(days[hot_key]) >= HOT_DAY_MIN:
        n = len(days[hot_key])
        tiles.append(_record("hot_day", "flame", str(n), _ru_plural(n, "матч", "матча", "матчей").split(" ", 1)[1], [],
                             days[hot_key][-1].completed_at, now, f"summary/day/{hot_key}"))

    pairs = Counter(tuple(sorted((m.challenger_id, m.challenged_id))) for m in matches)
    (pa, pb), pn = max(pairs.items(), key=lambda kv: (kv[1], -kv[0][0]))
    if pn >= 2:
        tiles.append(_record("derby", "swords", str(pn), _ru_plural(pn, "матч", "матча", "матчей").split(" ", 1)[1],
                             [person(pa), person(pb)], None, now, f"pair/{pa}/{pb}"))

    with_sets = [m for m in matches if m.sets_data]
    if with_sets:
        lm = max(with_sets, key=lambda m: len(m.sets_data or []))
        n = len(lm.sets_data or [])
        tiles.append(_record("longest", "clock", str(n), _ru_plural(n, "партия", "партии", "партий").split(" ", 1)[1],
                             [person(lm.challenger_id), person(lm.challenged_id)], lm.completed_at, now, f"match/{lm.id}"))

    # ── Под угрозой ──────────────────────────────────────────────────────────
    threats: list[dict] = []
    if best_n >= 2:
        cur_pid = max(by_player, key=lambda pid: (_current_streak(by_player[pid], pid), -pid))
        cur = _current_streak(by_player[cur_pid], cur_pid)
        if cur_pid != best_pid and cur >= 2 and cur >= THREAT_SHARE * best_n:
            need = best_n - cur + 1
            threats.append(_threat(f"Серия побед: {person(cur_pid)['name']} {cur} из {best_n}", cur, best_n,
                                   f"ещё {_ru_plural(need, 'победа', 'победы', 'побед')} — и рекорд его "
                                   f"(сейчас рекорд — {person(best_pid)['name']})", "streak", cur_pid))
    current = (await session.execute(
        select(ChampionReign).where(ChampionReign.ended_at.is_(None)).order_by(ChampionReign.started_at.desc()).limit(1)
    )).scalar_one_or_none()
    if current is not None and reign is not None:
        cur_days = (now - current.started_at).days
        if cur_days < reign[1] and cur_days >= THREAT_SHARE * reign[1] and cur_days > 0:
            threats.append(_threat(f"Правление: {person(current.player_id)['name']} {cur_days} из {reign[1]} дней",
                                   cur_days, reign[1],
                                   f"трон держит {pluralize_days(cur_days)}, рекорд — {reign[1]}", "reign",
                                   current.player_id))
    if current is not None and defenses is not None:
        from bot.webapp.api import _reign_defenses
        cur_def = await _reign_defenses(session, current, now)
        if cur_def < defenses[1] and cur_def > 0 and cur_def >= THREAT_SHARE * defenses[1]:
            need = defenses[1] - cur_def + 1
            threats.append(_threat(f"Защиты трона: {person(current.player_id)['name']} {cur_def} из {defenses[1]}",
                                   cur_def, defenses[1],
                                   f"ещё {_ru_plural(need, 'защита', 'защиты', 'защит')} — и рекорд его", "reign",
                                   current.player_id))
    if len(ranked) > 1:
        second = ranked[1]
        n1, n2 = counts[most], counts[second]
        if n2 < n1 and n2 >= THREAT_SHARE * n1:
            need = n1 - n2 + 1
            threats.append(_threat(f"{TITLES['matches']}: {person(second)['name']} {n2} из {n1}", n2, n1,
                                   f"ещё {_ru_plural(need, 'матч', 'матча', 'матчей')} — и рекорд его", "matches",
                                   second))

    holders = [{**person(pid), "count": n} for pid, n in sorted(single.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"hero": hero, "tiles": tiles, "threats": threats, "holders": holders[:HOLDERS_MAX]}


def summary_line(total: int, holders: list[dict]) -> str:
    """«17 рекордов · больше всех — Вика, 5» (у лидера должно быть больше одного).
    Имя в именительном падеже: склонять имена нечем («у Вики» не построить)."""
    line = _ru_plural(total, "рекорд", "рекорда", "рекордов")
    if holders and holders[0]["count"] > 1 and (len(holders) == 1 or holders[0]["count"] > holders[1]["count"]):
        line += f" · больше всех — {holders[0]['name']}, {holders[0]['count']}"
    return line
