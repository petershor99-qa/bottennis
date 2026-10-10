"""
Данные страниц Mini App — только чтение (v2.160.0: «Рейтинг клуба»;
v2.161.0: профиль, графики, достижения, рекорды, история, личные встречи,
все матчи клуба, зал славы).

Расчёты не дублируются: таблица — `compute_leaderboard`, статистика —
`_compute_player_stats` и готовые строки экранов бота (их формулировки уже
согласованы, в приложение они попадают через `bot/webapp/text.py` без эмодзи
и HTML), графики — те же ряды, что уходят в картинки бота. Здесь только сборка
JSON, чтобы русские строки приложения жили на сервере и проверялись тестами.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import ChampionReign, Match, MatchStatus, Player
from bot.services.achievements import ACHIEVEMENTS_LIST, CATEGORY_ORDER, get_achievements
from bot.services.leaderboard import LeaderboardRow, compute_leaderboard
from bot.services.stats import (
    AXIS_GLOSSARY,
    NEUTRAL_ARCHETYPE,
    _archetype_description,
    _build_style_narrative,
    _build_style_radar,
    _closest_archetype_hint,
    _compute_player_stats,
    _legend_index_with_rank,
    _nearest_achievement_progress,
    _style_archetype,
)
from bot.utils import (
    HEATMAP_DAYS,
    MSK_OFFSET,
    _ru_plural,
    activity_counts_by_day,
    build_rating_series,
    compute_h2h,
    get_career_matches,
    match_rating_delta,
    match_score_challenger_first,
    pluralize_days,
    pluralize_wins,
    rank_title,
)
from bot.webapp.text import groups, plain

MIN_MATCHES_FOR_RADAR = 5  # как у радара в боте (bot/handlers/history.py)


def _msk_date(dt: datetime | None, fmt: str = "%d.%m") -> str:
    return (dt + MSK_OFFSET).strftime(fmt) if dt else ""


def _signed(x: float) -> str:
    return f"+{x:.1f}" if x >= 0 else f"−{abs(x):.1f}"


# ── Рейтинг клуба ─────────────────────────────────────────────────────────────

def _subtitle(row: LeaderboardRow) -> str:
    """«Чемпион · Тим лид», «Миддл · серия 5 побед» — вторая строка под именем."""
    parts = []
    if row.is_champion:
        parts.append("Чемпион")
    elif row.is_challenger:
        parts.append("Претендент")
    elif row.is_mvp:
        parts.append("MVP месяца")
    parts.append(rank_title(row.rating))
    if row.streak >= 3:
        parts.append(f"серия {pluralize_wins(row.streak)}")
    elif row.inactive:
        parts.append("не играл 7+ дней")
    return " · ".join(parts)


def _week_label(change: int) -> str:
    """«+1 место», «−2 места», пусто без изменений."""
    if change == 0:
        return ""
    sign = "+" if change > 0 else "−"
    return sign + _ru_plural(abs(change), "место", "места", "мест")


def _gap_line(rows: list[LeaderboardRow], viewer_index: int | None) -> str:
    """«До #1 Боб: 13.2 очка.» или у лидера «Отрыв от #2 Вика: 13.2 очка.»
    Не показывается, если разрыв не положителен: выше стоит закреплённый
    чемпион с рейтингом ниже (место #1 не занимается по очкам)."""
    if viewer_index is None:
        return ""
    me = rows[viewer_index]
    if viewer_index > 0:
        above = rows[viewer_index - 1]
        gap = round(above.rating - me.rating, 1)
        return f"До #{above.rank} {above.name}: {gap:.1f} очка." if gap > 0 else ""
    if len(rows) > 1:
        lead = round(me.rating - rows[1].rating, 1)
        return f"Отрыв от #{rows[1].rank} {rows[1].name}: {lead:.1f} очка." if lead > 0 else ""
    return ""


async def _club_counts(session: AsyncSession) -> dict[str, int]:
    matches = (await session.execute(
        select(func.count()).select_from(Match).where(Match.status == MatchStatus.completed)
    )).scalar_one()
    reigns = (await session.execute(select(func.count()).select_from(ChampionReign))).scalar_one()
    return {"matches": matches, "reigns": reigns}


async def leaderboard_payload(session: AsyncSession, viewer_player_id: int) -> dict:
    rows = await compute_leaderboard(session)
    viewer_index = next((i for i, r in enumerate(rows) if r.player_id == viewer_player_id), None)
    counts = await _club_counts(session)
    links = [{"route": f"player/{viewer_player_id}", "title": "Мой профиль", "value": ""}]
    if counts["matches"]:
        records = await _records(session)
        total_records = sum(len(v) for v in records.values()) if records else 0
        links.append({"route": "records", "title": "Рекорды клуба", "value": str(total_records)})
        links.append({"route": "matches", "title": "Все матчи клуба", "value": str(counts["matches"])})
        links.append({"route": "activity/club", "title": "Активность клуба", "value": ""})
    if counts["reigns"]:
        links.append({"route": "throne", "title": "Зал славы", "value": str(counts["reigns"])})
    return {
        "title": "Рейтинг клуба",
        "players_label": _ru_plural(len(rows), "игрок", "игрока", "игроков"),
        "rows": [
            {
                "rank": r.rank,
                "player_id": r.player_id,
                "name": r.name,
                "initial": (r.name.strip()[:1] or "?").upper(),
                "is_viewer": r.player_id == viewer_player_id,
                "subtitle": _subtitle(r),
                "rating": f"{r.rating:.1f}",
                "week_change": r.week_change,
                "week_label": _week_label(r.week_change),
            }
            for r in rows
        ],
        "gap": _gap_line(rows, viewer_index),
        "links": links,
        "empty": "Пока нет сыгранных матчей." if not rows else "",
    }


# ── Профиль ───────────────────────────────────────────────────────────────────

def _week_delta(matches: list[Match], player_id: int) -> float | None:
    week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    recent = [m for m in matches if m.completed_at and m.completed_at >= week_ago]
    if not recent:
        return None
    return round(sum(match_rating_delta(m, player_id) for m in recent), 1)


async def player_payload(session: AsyncSession, player: Player, viewer: Player) -> dict:
    # Импорт здесь: модуль хендлера тянет клавиатуры и роутер, а api нужен и
    # без них (тесты подписей); заодно нет цикла импортов при старте.
    from bot.handlers.profile import (
        STATS_SECTIONS,
        _available_sections,
        _load_ranking_context,
        _rank_gap_line,
        _rank_title_progress_line,
        _stats_groups,
        _throne_distance_line,
    )

    personal = player.id == viewer.id
    players_all, champion, _counts, ranks, _rank_str, challenger = await _load_ranking_context(session, player)
    rank = ranks.get(player.id)
    place = f"#{rank} из {len(ranks)}" if rank else "вне рейтинга"
    head = {
        "name": player.display_name,
        "initial": (player.display_name.strip()[:1] or "?").upper(),
        "rating": f"{player.rating:.1f}",
        "subtitle": f"{rank_title(player.rating)} · {place}",
        "week": "",
        "personal": personal,
    }

    matches = await get_career_matches(session, player.id, with_opponents=True)
    if not matches:
        return {
            "head": head,
            "metrics": [],
            "groups": [],
            "chart": None,
            "sections": [],
            "empty": "Ещё не сыграно ни одного матча." if not personal else "Ты ещё не сыграл ни одного матча.",
        }

    s = _compute_player_stats(player, matches)
    week = _week_delta(matches, player.id)
    if week is not None:
        head["week"] = f"{_signed(week)} за неделю"

    total = s["wins"] + s["draws"] + s["losses"]
    metrics = [{"label": "Победы", "value": f"{s['wins']} из {total}"}]
    if s["draws"]:
        metrics.append({"label": "Ничьи", "value": str(s["draws"])})
    metrics.append({"label": "Винрейт", "value": f"матчи {s['win_rate']}% · партии {s['sets_win_rate']}%"})
    legend_index, legend_rank, legend_total = await _legend_index_with_rank(session, player, players_all)
    metrics.append({"label": "Индекс легенды", "value": f"{legend_index} · #{legend_rank} из {legend_total}"})

    extra = [
        _rank_gap_line(player, players_all, ranks),
        _throne_distance_line(player, champion, challenger, total),
    ]
    if personal:
        extra.append(_rank_title_progress_line(player.rating))
        extra.append(_nearest_achievement_progress(player, s, len(players_all)))
    form = _stats_groups(player, s)["form"]
    body_groups = groups([*form, "", *[x for x in extra if x]])

    rated = sorted((m for m in matches if m.rating_change is not None and m.completed_at),
                   key=lambda m: m.completed_at)
    chart = None
    if len(rated) >= 2:
        labels, values = build_rating_series(rated, player.id, player.rating)
        chart = {"labels": labels, "values": values, "reference": 1000.0}

    earned = set(get_achievements(player))
    earned_count = sum(1 for a in ACHIEVEMENTS_LIST if a.id in earned)
    sections = [{"route": f"player/{player.id}/achievements", "title": "Достижения",
                 "value": f"{earned_count} из {len(ACHIEVEMENTS_LIST)}"}]
    if len(matches) >= MIN_MATCHES_FOR_RADAR:
        radar = _build_style_radar(s)
        if radar is not None:
            sections.append({"route": f"player/{player.id}/radar", "title": "Радар стиля",
                             "value": _style_archetype(radar, s) or ""})
    sections.append({"route": f"player/{player.id}/activity", "title": "Активность", "value": ""})
    sections.append({"route": f"player/{player.id}/history", "title": "История матчей",
                     "value": str(len(matches))})
    if not personal:
        sections.append({"route": f"h2h/{player.id}", "title": "Личные встречи", "value": ""})
    titles = dict(_available_sections(player, s, matches, include_growth=personal))
    for key, _ in STATS_SECTIONS:
        if key in titles:
            sections.append({"route": f"player/{player.id}/stats/{key}", "title": plain(titles[key]), "value": ""})

    return {"head": head, "metrics": metrics, "groups": body_groups, "chart": chart,
            "sections": sections, "empty": ""}


async def player_stats_section_payload(session: AsyncSession, player: Player, viewer: Player, key: str) -> dict | None:
    from bot.handlers.profile import STATS_SECTIONS, _section_lines, _section_title

    personal = player.id == viewer.id
    titles = {k: _section_title(k, t, personal) for k, t in STATS_SECTIONS}
    if key not in titles:
        return None
    matches = await get_career_matches(session, player.id, with_opponents=True)
    if not matches:
        return None
    s = _compute_player_stats(player, matches)
    lines = _section_lines(key, player, s, matches, include_growth=personal)
    return {"title": plain(titles[key]), "subtitle": player.display_name, "groups": groups(lines)}


# ── Достижения ────────────────────────────────────────────────────────────────

def achievements_payload(player: Player) -> dict:
    earned = set(get_achievements(player))
    categories = []
    for i, category in enumerate(CATEGORY_ORDER):
        achs = [a for a in ACHIEVEMENTS_LIST if a.category == category]
        categories.append({
            "index": i,
            "title": plain(category),
            "value": f"{sum(1 for a in achs if a.id in earned)} из {len(achs)}",
        })
    total = sum(1 for a in ACHIEVEMENTS_LIST if a.id in earned)
    return {"title": "Достижения", "subtitle": player.display_name,
            "count": f"{total} из {len(ACHIEVEMENTS_LIST)}", "categories": categories}


def achievement_category_payload(player: Player, index: int) -> dict | None:
    if not 0 <= index < len(CATEGORY_ORDER):
        return None
    category = CATEGORY_ORDER[index]
    earned = set(get_achievements(player))
    achs = [a for a in ACHIEVEMENTS_LIST if a.category == category]
    items = []
    for a in sorted(achs, key=lambda a: a.id not in earned):
        if a.id in earned:
            items.append({"name": a.name, "desc": a.desc, "earned": True})
        elif a.hidden:
            items.append({"name": "?", "desc": "Скрытое достижение", "earned": False, "hidden": True})
        else:
            items.append({"name": a.name, "desc": a.desc, "earned": False})
    count = sum(1 for a in achs if a.id in earned)
    return {"title": plain(category), "subtitle": player.display_name,
            "count": f"{count} из {len(achs)}", "items": items}


# ── Радар стиля и активность ──────────────────────────────────────────────────

async def radar_payload(session: AsyncSession, player: Player) -> dict:
    matches = await get_career_matches(session, player.id, with_opponents=True)
    if len(matches) < MIN_MATCHES_FOR_RADAR:
        return {"title": "Радар стиля", "subtitle": player.display_name,
                "empty": f"Нужно минимум {MIN_MATCHES_FOR_RADAR} матчей для радара стиля."}
    s = _compute_player_stats(player, matches)
    radar = _build_style_radar(s)
    if radar is None:
        return {"title": "Радар стиля", "subtitle": player.display_name,
                "empty": "Пока не сыграно ни одной партии."}
    archetype = _style_archetype(radar, s)
    hint = _closest_archetype_hint(radar, s) if archetype == NEUTRAL_ARCHETYPE else None
    narrative = _build_style_narrative(radar, s)
    return {
        "title": "Радар стиля",
        "subtitle": player.display_name,
        "axes": [{"name": name, "value": round(val), "hint": AXIS_GLOSSARY[name]} for name, val in radar.items()],
        "archetype": archetype or "",
        "description": (_archetype_description(archetype) or "") if archetype else "",
        "hint": plain(hint) if hint else "",
        "narrative": plain(narrative) if narrative else "",
        "empty": "",
    }


async def activity_payload(session: AsyncSession, player: Player | None) -> dict:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=HEATMAP_DAYS)
    q = select(Match).where(Match.status == MatchStatus.completed, Match.completed_at >= since)
    if player is not None:
        q = q.where(or_(Match.challenger_id == player.id, Match.challenged_id == player.id))
    counts = activity_counts_by_day((await session.execute(q)).scalars().all())
    today = (datetime.now(timezone.utc).replace(tzinfo=None) + MSK_OFFSET).date()
    start = today - timedelta(days=HEATMAP_DAYS - 1)
    start_monday = start - timedelta(days=start.weekday())
    days = []
    d = start_monday
    while d <= today:
        days.append({"date": d.strftime("%d.%m"), "count": counts.get(d, 0) if d >= start else None})
        d += timedelta(days=1)
    total = sum(counts.values())
    return {
        "title": "Активность клуба" if player is None else "Активность",
        "subtitle": f"{player.display_name} · " if player else "",
        "period": f"последние {HEATMAP_DAYS} дней",
        "total": _ru_plural(total, "матч", "матча", "матчей"),
        "days": days,
        "legend": ["0", "1", "2–3", "4+"],
    }


# ── История, личные встречи, матчи клуба ──────────────────────────────────────

def _match_row(m: Match, player_id: int) -> dict:
    """Матч в перспективе player_id: соперник, исход, счёт, дельта."""
    opponent = m.challenged if m.challenger_id == player_id else m.challenger
    is_draw = m.winner_id is None
    won = m.winner_id == player_id
    i_am_challenger = m.challenger_id == player_id
    sets = []
    for st in m.sets_data or []:
        mine_first = won or (is_draw and i_am_challenger)
        sets.append(f"{st['w']}:{st['l']}" if mine_first else f"{st['l']}:{st['w']}")
    delta = ""
    if m.rating_change is not None:
        delta = _signed(match_rating_delta(m, player_id))
    return {
        "date": _msk_date(m.completed_at),
        "opponent": opponent.display_name,
        "opponent_id": opponent.id,
        "result": "d" if is_draw else ("w" if won else "l"),
        "score": ", ".join(sets),
        "delta": delta,
        "boss": bool(m.is_boss_fight),
    }


async def history_payload(session: AsyncSession, player: Player) -> dict:
    matches = (await session.execute(
        select(Match)
        .where(or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
               Match.status == MatchStatus.completed)
        .order_by(desc(Match.completed_at))
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )).scalars().all()
    return {
        "title": "История матчей",
        "subtitle": f"{player.display_name} · {_ru_plural(len(matches), 'матч', 'матча', 'матчей')}",
        "matches": [_match_row(m, player.id) for m in matches],
        "empty": "Пока нет сыгранных матчей." if not matches else "",
    }


async def h2h_payload(session: AsyncSession, viewer: Player, opponent: Player) -> dict:
    matches = (await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed, or_(
            and_(Match.challenger_id == viewer.id, Match.challenged_id == opponent.id),
            and_(Match.challenger_id == opponent.id, Match.challenged_id == viewer.id),
        ))
        .order_by(desc(Match.completed_at))
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )).scalars().all()
    base = {"title": "Личные встречи", "subtitle": f"Ты и {opponent.display_name}"}
    if not matches:
        return {**base, "metrics": [], "matches": [], "empty": "Вы ещё не встречались за столом."}
    s = compute_h2h(matches, viewer.id, opponent.id)
    score = f"{s['wins']}–{s['losses']}"
    if s["draws"]:
        score += f", ничьих {s['draws']}"
    metrics = [{"label": "Счёт встреч", "value": score}]
    if s["wins"] + s["losses"] >= 4 and abs(s["wins"] - s["losses"]) <= 1:
        metrics.append({"label": "Противостояние", "value": "Равный бой"})
    metrics.append({"label": "По партиям", "value": f"{s['my_sets']}–{s['opp_sets']}"})
    if s["streak_desc"]:
        metrics.append({"label": "Сейчас", "value": s["streak_desc"]})
    metrics.append({"label": "Рейтинг в противостоянии", "value": f"{_signed(s['rating_delta'])} pts"})
    if s["best_win"] is not None and s["best_win"] > 0:
        metrics.append({"label": "Лучшая победа", "value": f"+{s['best_win']} pts"})
    if s["first_date"]:
        metrics.append({"label": "Первая встреча", "value": s["first_date"].strftime("%d.%m.%y")})
    return {**base, "metrics": metrics, "matches": [_match_row(m, viewer.id) for m in matches], "empty": ""}


CLUB_MATCHES_PAGE = 30


async def club_matches_payload(session: AsyncSession, offset: int) -> dict:
    offset = max(0, offset)
    total = (await session.execute(
        select(func.count()).select_from(Match).where(Match.status == MatchStatus.completed)
    )).scalar_one()
    rows = (await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed)
        .order_by(desc(Match.completed_at), desc(Match.id))
        .offset(offset).limit(CLUB_MATCHES_PAGE)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )).scalars().all()
    items = []
    for m in rows:
        winner = "a" if m.winner_id == m.challenger_id else ("b" if m.winner_id == m.challenged_id else "")
        items.append({
            "date": _msk_date(m.completed_at),
            "a": m.challenger.display_name, "a_id": m.challenger_id,
            "b": m.challenged.display_name, "b_id": m.challenged_id,
            "winner": winner,
            "score": match_score_challenger_first(m),
        })
    next_offset = offset + len(rows)
    return {
        "title": "Все матчи клуба",
        "subtitle": _ru_plural(total, "матч", "матча", "матчей"),
        "matches": items,
        "next_offset": next_offset if next_offset < total else None,
        "empty": "Матчей ещё не было." if total == 0 else "",
    }


# ── Рекорды клуба и зал славы ─────────────────────────────────────────────────

async def _records(session: AsyncSession) -> dict[str, list[str]] | None:
    from bot.handlers.leaderboard import _collect_club_records
    return await _collect_club_records(session)


async def records_payload(session: AsyncSession) -> dict:
    from bot.handlers.leaderboard import RECORD_CATEGORIES

    collected = await _records(session)
    if not collected:
        return {"title": "Рекорды клуба", "categories": [], "empty": "Матчей ещё не было."}
    categories = []
    for key, title in RECORD_CATEGORIES:
        records = collected.get(key) or []
        if not records:
            continue
        items = [g[0] for g in groups([line for r in records for line in (r, "")]) if g]
        categories.append({"key": key, "title": plain(title), "count": len(records), "items": items})
    return {"title": "Рекорды клуба", "categories": categories, "empty": ""}


async def hall_of_fame_payload(session: AsyncSession) -> dict:
    from bot.handlers.leaderboard import _reign_end_narrative

    reigns = (await session.execute(select(ChampionReign).order_by(desc(ChampionReign.started_at)))).scalars().all()
    if not reigns:
        return {"title": "Зал славы", "current": None, "reigns": [], "facts": [],
                "empty": "Боссфайт за 1-е место ещё ни разу не активировался."}
    name_map = {p.id: p.display_name for p in (await session.execute(select(Player))).scalars().all()}
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    def _days(delta_days: int) -> str:
        return "меньше дня" if delta_days == 0 else pluralize_days(delta_days)

    current = next((r for r in reigns if r.ended_at is None), None)
    current_payload = None
    if current is not None:
        current_payload = {
            "name": name_map.get(current.player_id, "?"),
            "player_id": current.player_id,
            "since": current.started_at.strftime("%d.%m.%y"),
            "duration": _days((now - current.started_at).days),
        }
    closed = []
    for r in reigns:
        if r.ended_at is None:
            continue
        narrative = await _reign_end_narrative(session, r, name_map)
        closed.append({
            "name": name_map.get(r.player_id, "?"),
            "player_id": r.player_id,
            "period": f"{r.started_at.strftime('%d.%m.%y')} – {r.ended_at.strftime('%d.%m.%y')}",
            "duration": _days((r.ended_at - r.started_at).days),
            "note": plain(narrative) if narrative else "",
        })
    return {
        "title": "Зал славы",
        "current": current_payload,
        "facts": [{"label": "Смен трона", "value": str(len(reigns))}],
        "reigns": closed,
        "empty": "",
    }
