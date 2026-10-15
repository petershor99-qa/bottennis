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
from bot.services.rating import what_if_range, win_probability
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
    NEWCOMER_THRESHOLD,
    RANK_TITLE_BANDS,
    _ru_plural,
    activity_counts_by_day,
    build_rating_series,
    compute_h2h,
    get_career_matches,
    get_match_counts,
    match_drama_reason,
    match_rating_delta,
    match_score_challenger_first,
    msk_day_start,
    pick_match_of_day,
    pluralize_days,
    pluralize_wins,
    rank_title,
)
from bot.webapp import style_tiers
from bot.webapp.achievement_icons import ACHIEVEMENT_ICONS
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


SPARK_POINTS = 10   # мини-график в строке рейтинга (v2.182.0): рейтинг после последних 10 матчей


async def _sparklines(session: AsyncSession, players: dict[int, float]) -> dict[int, list[float]]:
    """{id игрока: рейтинг после каждого из последних SPARK_POINTS матчей, от старых к
    новым}. Считается назад от текущего рейтинга по изменениям в матчах (как график
    профиля). Меньше двух точек — без графика."""
    matches = (await session.execute(
        select(Match).where(Match.status == MatchStatus.completed, Match.rating_change.is_not(None))
        .order_by(Match.completed_at.desc(), Match.id.desc())
    )).scalars().all()
    out: dict[int, list[float]] = {pid: [round(r, 1)] for pid, r in players.items()}
    current = dict(players)
    for m in matches:
        for pid in (m.challenger_id, m.challenged_id):
            series = out.get(pid)
            if series is None or len(series) > SPARK_POINTS:
                continue
            current[pid] -= match_rating_delta(m, pid)
            series.append(round(current[pid], 1))
        if all(len(v) > SPARK_POINTS for v in out.values()):
            break
    return {pid: list(reversed(v[:SPARK_POINTS])) for pid, v in out.items() if len(v) >= 2}


async def leaderboard_payload(session: AsyncSession, viewer_player_id: int) -> dict:
    rows = await compute_leaderboard(session)
    viewer_index = next((i for i, r in enumerate(rows) if r.player_id == viewer_player_id), None)
    sparks = await _sparklines(session, {r.player_id: r.rating for r in rows})
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
                "spark": sparks.get(r.player_id, []),
            }
            for r in rows
        ],
        "gap": _gap_line(rows, viewer_index),
        "empty": "Пока нет сыгранных матчей." if not rows else "",
    }


# ── Главная «Клуб сегодня» ────────────────────────────────────────────────────

# Короткие заголовки свежих рекордов клуба (журнал пингов `club_record_pings`)
_RECORD_TITLES = {
    "peak": ("Пик рейтинга клуба", lambda v: f"{v:.1f}"),
    "streak": ("Серия побед — рекорд клуба", lambda v: _ru_plural(int(v), "победа", "победы", "побед") + " подряд"),
    "upset": ("Крупнейший апсет", lambda v: f"+{v:.1f} рейтинга"),
    "defense": ("Защиты трона подряд", lambda v: _ru_plural(int(v), "защита", "защиты", "защит")),
}


def _sets_chips(m: Match) -> list[str]:
    """Счёт партий в перспективе challenger'а — для «плашек» партий."""
    text = match_score_challenger_first(m)
    return [x.strip() for x in text.split(",")] if text else []


def _club_match(m: Match) -> dict:
    winner = "a" if m.winner_id == m.challenger_id else ("b" if m.winner_id == m.challenged_id else "")
    return {
        "id": m.id,
        "date": _msk_date(m.completed_at),
        "a": _person(m.challenger), "b": _person(m.challenged),
        "winner": winner,
        "sets": _sets_chips(m),
        "boss": bool(m.is_boss_fight),
    }


async def home_payload(session: AsyncSession, viewer: Player) -> dict:
    from bot.db.models import ClubRecordPing

    rows = await compute_leaderboard(session)
    podium = [
        {"rank": r.rank, "id": r.player_id, "name": r.name,
         "initial": (r.name.strip()[:1] or "?").upper(), "rating": f"{r.rating:.1f}",
         "champion": r.is_champion}
        for r in rows[:3]
    ]
    me_row = next((r for r in rows if r.player_id == viewer.id), None)
    my_matches = await get_career_matches(session, viewer.id)
    week = _week_delta(my_matches, viewer.id)
    me = {
        "id": viewer.id,
        "name": viewer.display_name,
        "initial": (viewer.display_name.strip()[:1] or "?").upper(),
        "rating": f"{viewer.rating:.1f}",
        "place": f"#{me_row.rank} из {len(rows)}" if me_row else "вне рейтинга",
        "week": f"{_signed(week)} за неделю" if week is not None else "",
        "week_up": week is None or week >= 0,
    }

    today = (await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed, Match.completed_at >= msk_day_start())
        .order_by(desc(Match.completed_at))
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )).scalars().all()
    featured = pick_match_of_day(list(today))
    featured_title = "Матч дня"
    if featured is None:
        featured = today[0] if today else (await session.execute(
            select(Match).where(Match.status == MatchStatus.completed)
            .order_by(desc(Match.completed_at)).limit(1)
            .options(selectinload(Match.challenger), selectinload(Match.challenged))
        )).scalar_one_or_none()
        featured_title = "Последний матч"
    featured_payload = None
    if featured is not None:
        featured_payload = {**_club_match(featured), "title": featured_title,
                            "reason": plain(match_drama_reason(featured)) if featured_title == "Матч дня" else ""}

    week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    pings = (await session.execute(
        select(ClubRecordPing).where(ClubRecordPing.pinged_at >= week_ago)
        .order_by(desc(ClubRecordPing.pinged_at)).limit(3)
    )).scalars().all()
    names = {r.player_id: r.name for r in rows}
    records = []
    for p in pings:
        title, fmt = _RECORD_TITLES.get(p.kind, ("Рекорд клуба", lambda v: f"{v:.1f}"))
        records.append({"title": title, "name": names.get(p.player_id, "?"), "value": fmt(p.value),
                        "date": _msk_date(p.pinged_at)})

    counts = await _club_counts(session)
    links = []
    if counts["matches"]:
        links.append({"route": "records", "title": "Рекорды клуба", "value": "", "icon": "trophy"})
        links.append({"route": "matches", "title": "Все матчи клуба", "value": str(counts["matches"]), "icon": "list"})
        links.append({"route": "activity/club", "title": "Активность клуба", "value": "", "icon": "calendar"})
    if counts["reigns"]:
        links.append({"route": "throne", "title": "Зал славы", "value": str(counts["reigns"]), "icon": "crown"})

    return {
        "podium": podium,
        "me": me,
        "today": {
            "count": len(today),
            "label": _ru_plural(len(today), "матч", "матча", "матчей") + " сегодня" if today else "Сегодня ещё не играли",
            "matches": [_club_match(m) for m in today[:5] if m is not featured],
        },
        "featured": featured_payload,
        "records": records,
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
        _available_sections,
        _load_ranking_context,
        _rank_gap_line,
        _rank_title_progress_line,
        _section_lines,
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
        # v2.182.0: рамка карточки по месту (1–3) и заставка «Новое звание»
        "rank": rank,
        "title": rank_title(player.rating),
        "title_level": sum(1 for threshold, _ in RANK_TITLE_BANDS if player.rating >= threshold),
        "week": "",
        "personal": personal,
    }

    matches = await get_career_matches(session, player.id, with_opponents=True)
    if not matches:
        return {
            "head": head,
            "tiles": [],
            "last10": [],
            "goals": [],
            "chart": None,
            "blocks": [],
            "counts": {"matches": 0, "achievements": "", "style": ""},
            "versus": None,
            "empty": "Ещё не сыграно ни одного матча." if not personal else "Ты ещё не сыграл ни одного матча.",
        }

    s = stats_cached(player, matches)
    week = _week_delta(matches, player.id)
    if week is not None:
        head["week"] = f"{_signed(week)} за неделю"
        head["week_up"] = week >= 0

    total = s["wins"] + s["draws"] + s["losses"]
    legend_index, legend_rank, legend_total = await _legend_index_with_rank(session, player, players_all)
    recent = sorted((m for m in matches if m.completed_at), key=lambda m: m.completed_at)[-10:]
    last10 = ["d" if m.winner_id is None else ("w" if m.winner_id == player.id else "l") for m in recent]

    # Серия из одного матча серией не считается — вместо неё исход последнего матча
    if s["streak"] >= 2:
        streak_tile = {"label": "Серия побед", "value": str(s["streak"]), "tone": "win",
                       "sub": f"рекорд {s['best_streak']}" if s["best_streak"] > s["streak"] else "твой рекорд"}
    elif s["loss_streak"] >= 2:
        streak_tile = {"label": "Серия поражений", "value": str(s["loss_streak"]), "tone": "loss"}
    else:
        last = last10[-1] if last10 else "d"
        word, tone = {"w": ("Победа", "win"), "l": ("Поражение", "loss")}.get(last, ("Ничья", ""))
        streak_tile = {"label": "Последний матч", "value": word, "tone": tone}
    peak = max(player.peak_rating or 0.0, player.rating)
    tiles = [
        {"label": "Побед", "value": f"{s['win_rate']}%", "sub": f"{s['wins']} из {total}"},
        streak_tile,
        {"label": "Матчей", "value": str(total), "sub": f"{s['wins']} побед · {s['losses']} поражений"},
        {"label": "Партий выиграно", "value": f"{s['sets_win_rate']}%", "sub": "доля выигранных партий"},
        {"label": "Пик рейтинга", "value": f"{peak:.1f}", "sub": "максимум за всё время"},
        {"label": "Индекс легенды", "value": str(legend_index), "sub": f"#{legend_rank} из {legend_total}",
         "hint": "Индекс легенды считается по достижениям, рекордам клуба и боссфайтам."},
    ]

    goal_lines = []
    if personal:
        goal_lines = [_rank_title_progress_line(player.rating),
                      _nearest_achievement_progress(player, s, len(players_all))]
    goals = [it for g in groups([x for x in goal_lines if x]) for it in g]
    for it in goals:
        if it.get("label", "").startswith("До звания") and "progress" not in it:
            it["progress"] = _title_progress(player.rating)

    position = groups([x for x in (
        _rank_gap_line(player, players_all, ranks),
        _throne_distance_line(player, champion, challenger, total),
    ) if x])

    rated = sorted((m for m in matches if m.rating_change is not None and m.completed_at),
                   key=lambda m: m.completed_at)
    chart = None
    if len(rated) >= 2:
        labels, values = build_rating_series(rated, player.id, player.rating)
        chart = {"labels": labels, "values": values, "reference": 1000.0,
                 "min": min(values), "max": max(values)}

    titled = [{"title": "Положение", "items": position[0]}] if position else []
    for key, title in _available_sections(player, s, matches, include_growth=personal):
        lines = _section_lines(key, player, s, matches, include_growth=personal)
        items = [it for g in groups(lines) for it in g]
        if items:
            titled.append({"title": plain(title), "items": items})

    earned = set(get_achievements(player))
    radar = _build_style_radar(s) if len(matches) >= MIN_MATCHES_FOR_RADAR else None
    radar_ok = radar is not None
    card = _card(radar, s)
    if card and radar:
        # v2.180.0: цвет показателя — относительно клуба (bot/webapp/style_tiers.py)
        ref = await style_tiers.club_reference(session)
        for stat in card["stats"]:
            stat["tier"], stat["tier_hint"] = style_tiers.tier(stat["name"], radar[stat["name"]], s, player.id, ref)
    return {
        "head": head,
        "card": card,
        "tiles": tiles,
        "last10": last10,
        "goals": goals,
        "chart": chart,
        "blocks": titled,
        "counts": {
            "matches": len(matches),
            "achievements": f"{sum(1 for a in ACHIEVEMENTS_LIST if a.id in earned)} из {len(ACHIEVEMENTS_LIST)}",
            "style": _style_archetype(_build_style_radar(s), s) if radar_ok else "",
        },
        "versus": None if personal else f"Ты против {player.display_name}",
        "empty": "",
    }


# Кэш статистики игрока (v2.177.0): профиль, радар и разделы пересчитывали всю
# карьеру на каждый запрос. Ключ меняется с любым новым матчем или рейтингом,
# поэтому кэш не устаревает; размер ограничен.
STATS_CACHE_MAX = 64
_stats_cache: dict[tuple, dict] = {}


def stats_cached(player: Player, matches: list) -> dict:
    last = max((m.id for m in matches), default=0)
    key = (player.id, len(matches), last, round(player.rating, 1), round(player.peak_rating or 0.0, 1))
    hit = _stats_cache.get(key)
    if hit is None:
        if len(_stats_cache) >= STATS_CACHE_MAX:
            _stats_cache.pop(next(iter(_stats_cache)))
        hit = _stats_cache[key] = _compute_player_stats(player, matches)
    return hit


# Характеристики карточки игрока (v2.177.0) — оси радара стиля коротко
CARD_SHORT = {"Винрейт": "ВИН", "Клатч": "КЛЧ", "Дожимание": "ДОЖ", "Камбэки": "КМБ",
              "Доминирование": "ДОМ", "Стабильность": "СТБ"}


def _card(radar: dict | None, s: dict) -> dict | None:
    if not radar:
        return None
    return {"stats": [{"short": CARD_SHORT.get(name, name[:3].upper()), "name": name, "value": round(value)}
                      for name, value in radar.items()],
            "archetype": _style_archetype(radar, s)}


def _title_progress(rating: float) -> float:
    """Доля пути внутри текущей полосы звания (для шкалы «До звания»)."""
    from bot.utils import RANK_TITLE_BANDS

    lower = 0.0
    for threshold, _ in RANK_TITLE_BANDS:
        if rating < threshold:
            span = threshold - max(lower, 900.0)
            return round(min(1.0, max(0.0, (rating - max(lower, 900.0)) / span)), 2) if span > 0 else 0.0
        lower = threshold
    return 1.0


async def player_stats_section_payload(session: AsyncSession, player: Player, viewer: Player, key: str) -> dict | None:
    from bot.handlers.profile import STATS_SECTIONS, _section_lines, _section_title

    personal = player.id == viewer.id
    titles = {k: _section_title(k, t, personal) for k, t in STATS_SECTIONS}
    if key not in titles:
        return None
    matches = await get_career_matches(session, player.id, with_opponents=True)
    if not matches:
        return None
    s = stats_cached(player, matches)
    lines = _section_lines(key, player, s, matches, include_growth=personal)
    return {"title": plain(titles[key]), "subtitle": player.display_name, "groups": groups(lines)}


# ── Достижения ────────────────────────────────────────────────────────────────

# Лестницы — ачивки одного смысла с растущим порогом. Ступень лестницы задаёт
# цвет значка: бронза, серебро, золото (в длинных лестницах по две ступени на
# цвет). Остальные полученные ачивки — цвет категории приложения.
ACHIEVEMENT_LADDERS: list[list[str]] = [
    ["hat_trick", "im_on_fire", "god_mode"],
    ["fk_tyumen", "valley_of_tears"],
    ["fifty", "veteran", "legend", "workhorse", "monument", "superstar"],
    ["point_saver", "sturdy_grinder", "point_farmer"],
    ["set_sniper", "set_veteran", "set_legend"],
    ["punching_bag", "unbreakable", "tempered"],
    ["resilient", "battle_tested", "through_fire"],
]
_TIERS = ("bronze", "silver", "gold")


def achievement_tier(ach_id: str) -> str:
    """bronze | silver | gold для ступени лестницы, пусто — не лестница."""
    for ladder in ACHIEVEMENT_LADDERS:
        if ach_id in ladder:
            step = ladder.index(ach_id)
            return _TIERS[min(2, step * 3 // len(ladder))]
    return ""


def _achievement_icon(ach_id: str) -> str:
    """Имя иконки из набора webapp/static/icons.svg (ti-<имя>); trophy — запасная."""
    return ACHIEVEMENT_ICONS.get(ach_id, "trophy")


def achievements_payload(player: Player) -> dict:
    earned = set(get_achievements(player))
    categories = []
    for i, category in enumerate(CATEGORY_ORDER):
        achs = [a for a in ACHIEVEMENTS_LIST if a.category == category]
        items = []
        for a in sorted(achs, key=lambda a: a.id not in earned):
            if a.id in earned:
                items.append({"id": a.id, "name": a.name, "desc": a.desc, "earned": True,
                              "tier": achievement_tier(a.id), "icon": _achievement_icon(a.id)})
            elif a.hidden:
                items.append({"name": "?", "desc": "Скрытое достижение", "earned": False, "hidden": True,
                              "tier": "", "icon": "lock"})
            else:
                items.append({"id": a.id, "name": a.name, "desc": a.desc, "earned": False,
                              "tier": achievement_tier(a.id), "icon": _achievement_icon(a.id)})
        categories.append({
            "index": i,
            "title": plain(category),
            "value": f"{sum(1 for a in achs if a.id in earned)} из {len(achs)}",
            "items": items,
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
    s = stats_cached(player, matches)
    radar = _build_style_radar(s)
    if radar is None:
        return {"title": "Радар стиля", "subtitle": player.display_name,
                "empty": "Пока не сыграно ни одной партии."}
    archetype = _style_archetype(radar, s)
    hint = _closest_archetype_hint(radar, s) if archetype == NEUTRAL_ARCHETYPE else None
    narrative = _build_style_narrative(radar, s)
    ref = await style_tiers.club_reference(session)   # v2.180.0: цвет оси относительно клуба
    axes = []
    for name, val in radar.items():
        t, t_hint = style_tiers.tier(name, val, s, player.id, ref)
        axes.append({"name": name, "value": round(val), "hint": AXIS_GLOSSARY[name], "tier": t, "tier_hint": t_hint})
    return {
        "title": "Радар стиля",
        "subtitle": player.display_name,
        "axes": axes,
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
        "id": m.id,
        "date": _msk_date(m.completed_at),
        "iso": _msk_date(m.completed_at, "%Y-%m-%d"),  # для фильтра по периоду (v2.176.0)
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
        "versus": versus_table(matches, player.id),
        "empty": "Пока нет сыгранных матчей." if not matches else "",
    }


def versus_table(matches: list[Match], player_id: int) -> list[dict]:
    """«Против каждого» (v2.176.0): по каждому сопернику победы–поражения–ничьи,
    доля побед и дата последней встречи. matches — свежие сверху. Порядок — по
    числу встреч, при равенстве по доле побед."""
    rows: dict[int, dict] = {}
    for m in matches:
        opponent = m.challenged if m.challenger_id == player_id else m.challenger
        row = rows.get(opponent.id)
        if row is None:
            row = rows[opponent.id] = {**_person(opponent), "w": 0, "l": 0, "d": 0,
                                       "last": _msk_date(m.completed_at)}
        key = "d" if m.winner_id is None else ("w" if m.winner_id == player_id else "l")
        row[key] += 1
    out = []
    for row in rows.values():
        total = row["w"] + row["l"] + row["d"]
        rate = round(row["w"] / total * 100)
        score = f"{row['w']}–{row['l']}" + (f"–{row['d']}" if row["d"] else "")
        out.append({**row, "total": total, "rate": f"{rate}%", "score": score,
                    "tone": "win" if row["w"] > row["l"] else ("loss" if row["l"] > row["w"] else "")})
    out.sort(key=lambda r: (-r["total"], -r["w"] / r["total"]))
    return out


def _person(p: Player) -> dict:
    return {"id": p.id, "name": p.display_name, "initial": (p.display_name.strip()[:1] or "?").upper(),
            "rating": f"{p.rating:.1f}"}


async def _forecast(session: AsyncSession, viewer: Player, opponent: Player) -> dict:
    """Шанс победы и «что если» — те же формулы, что у калькулятора в боте."""
    counts = await get_match_counts(session)
    newcomer = counts.get(viewer.id, 0) < NEWCOMER_THRESHOLD
    (win_lo, win_hi), (lose_lo, lose_hi) = what_if_range(viewer.rating, opponent.rating, newcomer)
    return {
        "chance": round(win_probability(viewer.rating, opponent.rating) * 100),
        "win": f"+{win_lo}…+{win_hi}",
        "lose": f"−{lose_lo}…−{lose_hi}",
    }


async def _axes_pair(session: AsyncSession, viewer: Player, opponent: Player) -> list[dict]:
    """Оси стиля обоих рядом — только если у обоих хватает матчей для радара."""
    radars = []
    for p in (viewer, opponent):
        matches = await get_career_matches(session, p.id, with_opponents=True)
        if len(matches) < MIN_MATCHES_FOR_RADAR:
            return []
        radar = _build_style_radar(stats_cached(p, matches))
        if radar is None:
            return []
        radars.append(radar)
    mine, theirs = radars
    return [{"name": name, "me": round(mine[name]), "them": round(theirs.get(name, 0))} for name in mine]


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
    base = {
        "title": f"Ты против {opponent.display_name}",
        "subtitle": "",
        "me": _person(viewer),
        "them": _person(opponent),
        "score": None,
        "forecast": await _forecast(session, viewer, opponent),
        "axes": await _axes_pair(session, viewer, opponent),
    }
    if not matches:
        return {**base, "metrics": [], "matches": [], "empty": "Вы ещё не встречались за столом."}
    s = compute_h2h(matches, viewer.id, opponent.id)
    base["score"] = {"me": s["wins"], "them": s["losses"], "draws": s["draws"]}
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
    from bot.webapp.api_chronicle import duel_history  # v2.177.0; здесь — чтобы не было цикла импортов
    return {**base, "metrics": metrics, "matches": [_match_row(m, viewer.id) for m in matches],
            "duel": duel_history(matches, viewer.id), "empty": ""}


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
            "id": m.id,
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


async def _reign_defenses(session: AsyncSession, reign: ChampionReign, end: datetime) -> int:
    """Защиты трона в правлении — выигранные чемпионом боссфайты (как в
    most_boss_fight_defenses: проигранный боссфайт, закрывший правление, — не защита)."""
    return (await session.execute(
        select(func.count()).select_from(Match).where(
            Match.is_boss_fight == True,  # noqa: E712
            Match.status == MatchStatus.completed,
            Match.winner_id == reign.player_id,
            Match.completed_at >= reign.started_at, Match.completed_at <= end,
        )
    )).scalar_one()


async def hall_of_fame_payload(session: AsyncSession) -> dict:
    """v2.188.0: трон из ракеток — текущему чемпиону добавлены защиты, в факты —
    «дольше всех» и «защит подряд» (рекорды клуба теми же функциями)."""
    from bot.handlers.leaderboard import _reign_end_narrative
    from bot.utils import longest_champion_reign, most_boss_fight_defenses

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
            "defenses": await _reign_defenses(session, current, now),
        }
        n_def = current_payload["defenses"]
        current_payload["defenses_text"] = _ru_plural(n_def, "защита", "защиты", "защит") if n_def else ""
    records = []
    longest = await longest_champion_reign(session)
    if longest is not None:
        records.append({"label": "Дольше всех", "value": f"{name_map.get(longest[0], '?')} · {_days(longest[1])}"})
    defenses = await most_boss_fight_defenses(session)
    if defenses is not None:
        records.append({"label": "Защит за правление", "value": f"{name_map.get(defenses[0], '?')} · {defenses[1]}"})
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
        "records": records,
        "reigns": closed,
        "empty": "",
    }
