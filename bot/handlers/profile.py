from html import escape as h

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Player
from bot.keyboards.inline import (
    achievement_category_kb,
    achievements_kb,
    back_to_stats_kb,
    player_achievements_kb,
    player_profile_kb,
    player_stats_section_kb,
    stats_kb,
    stats_section_kb,
    what_if_kb,
)
from bot.services.achievements import (
    ACHIEVEMENTS_LIST,
    CATEGORY_ORDER,
    get_achievements,
)
from bot.services.personal_records import get_personal_records_count
from bot.services.rating import what_if_range
from bot.services.stats import (
    _build_career_narrative,
    _compute_player_stats,
    _growth_area,
    _legend_index_with_rank,
    _nearest_achievement_progress,
)
from bot.utils import (
    NEWCOMER_THRESHOLD,
    REPLY_KB_PROFILE_ALL,
    _challenger_among,
    boss_fight_rematch_blocked,
    cb_data,
    cb_msg,
    compute_ranks,
    format_rank,
    get_active_match,
    get_career_matches,
    get_match_counts,
    get_mvp_of_month,
    get_player,
    hour_range_label,
    msg_user,
    pluralize_days,
    pluralize_matches,
    rank_title,
)

router = Router()


# ── Контекст рейтинг-таблицы (общий для статистики и профиля) ─────────────────

async def _load_ranking_context(session: AsyncSession, player: Player):
    """Все игроки, чемпион, счётчики матчей, ранги, строка ранга игрока и
    текущий претендент — общий блок, ранее дословно дублировался в
    show_my_stats и show_player_profile."""
    players_all = (await session.execute(select(Player))).scalars().all()
    champion = next((p for p in players_all if p.is_champion), None)
    match_counts = await get_match_counts(session)
    ranks = compute_ranks(players_all, match_counts, champion_id=champion.id if champion else None)
    rank_str = format_rank(ranks, player.id)
    challenger_player = _challenger_among(players_all, champion, match_counts) if champion else None
    return players_all, champion, match_counts, ranks, rank_str, challenger_player


# ── Общий рендер строк статистики ─────────────────────────────────────────────

def _stats_groups(player, s: dict) -> dict[str, list[str]]:
    """Формирует группы строк статистики (форма, серии, соперники, рекорды и т.д.).

    Используется и в личной статистике, и в публичном профиле. Возвращает список
    строк без заголовка и без блока «Последние матчи» — их добавляет вызывающий.

    Строки сгруппированы по смыслу (форма/серии → соперники → рейтинг/рекорды →
    разное), каждая непустая группа отделена пустой строкой — те же принципы, что
    и у группировки «Рекорды клуба» (bot/handlers/leaderboard.py, v2.73.0): плоский
    список из 10+ разнородных пунктов подряд читается как нечитаемая простыня.
    """
    form_lines: list[str] = []
    opponent_lines: list[str] = []
    rating_lines: list[str] = []
    misc_lines: list[str] = []
    insight_lines: list[str] = []

    recent_7 = s["recent_7"]
    if recent_7:
        form_icons = []
        for m in recent_7:
            if m.winner_id is None:
                form_icons.append("🟡")
            elif m.winner_id == player.id:
                form_icons.append("🟢")
            else:
                form_icons.append("🔴")
        total_recent = len(form_icons)
        display_icons = form_icons[-10:]
        suffix = f"  <i>({total_recent} матчей)</i>" if total_recent > 10 else ""
        form_lines.append(f"🗓 Форма (7 дней): {''.join(display_icons)}{suffix}")

    streak = s["streak"]
    if streak >= 2:
        form_lines.append(f"🔥 Серия: <b>{streak} побед подряд</b>")
    if s["loss_streak"] >= 2:
        form_lines.append(f"😬 Серия: <b>{s['loss_streak']} поражений подряд</b>")
    if s["best_streak"] >= 2 and s["best_streak"] != streak:
        form_lines.append(f"🎖 Рекорд серии: <b>{s['best_streak']} побед подряд</b>")
    if s["activity_streak_days"] >= 2:
        form_lines.append(f"📆 Играешь <b>{pluralize_days(s['activity_streak_days'])}</b> подряд")

    if s["best_opp"]:
        bo = s["best_opp"]
        opponent_lines.append(f"🎁 Подарок: <b>{h(bo['name'])}</b> ({bo['wins']}–{bo['losses']}, {bo['rate']}% побед)")
    if s["nemesis"]:
        ne = s["nemesis"]
        opponent_lines.append(f"😱 Кошмар: <b>{h(ne['name'])}</b> ({ne['wins']}–{ne['losses']}, {ne['rate']}% поражений)")
    top_opp = s["top_opp"]
    if top_opp and top_opp["total"] >= 2:
        top_draws_str = f" 🤝{top_opp['draws']}" if top_opp["draws"] else ""
        opponent_lines.append(
            f"⚔️ Чаще всего: <b>{h(top_opp['name'])}</b> "
            f"({top_opp['total']} матчей, {top_opp['wins']}–{top_opp['losses']}{top_draws_str})"
        )
    if s["unresolved_debts"]:
        debts = s["unresolved_debts"]
        names_str = ", ".join(h(n) for n in debts[:3])
        if len(debts) > 3:
            names_str += f" +{len(debts) - 3}"
        opponent_lines.append(f"📌 Незакрытые долги: <b>{names_str}</b>")
    if s["debtors"]:
        debtors = s["debtors"]
        names_str = ", ".join(h(n) for n in debtors[:3])
        if len(debtors) > 3:
            names_str += f" +{len(debtors) - 3}"
        opponent_lines.append(f"💸 У тебя в долгу: <b>{names_str}</b>")

    if player.peak_rating and player.peak_rating > player.rating:
        rating_lines.append(f"📈 Пик рейтинга: <b>{round(player.peak_rating, 1)}</b> pts")
    if s["trend_30d"] is not None:
        sign = "+" if s["trend_30d"] >= 0 else ""
        rating_lines.append(
            f"📅 За 30 дней: <b>{sign}{s['trend_30d']} pts</b> ({s['trend_30d_matches']} матчей)"
        )
    avg_delta = s["avg_delta"]
    if avg_delta is not None:
        sign = "+" if avg_delta >= 0 else ""
        rating_lines.append(f"〽️ В среднем за матч: <b>{sign}{avg_delta} pts</b>")
    if s["best_win"] is not None:
        rating_lines.append(f"🏅 Лучший матч: <b>+{s['best_win']} pts</b>")
    if s["total_earned"] > 0 or s["total_lost"] > 0:
        rating_lines.append(f"💰 За карьеру: <b>+{s['total_earned']}</b> / <b>-{s['total_lost']}</b> pts")
    if s["boss_fights_played"] > 0:
        rating_lines.append(
            f"⚔️ Боссфайты: <b>{s['boss_fights_won']}/{s['boss_fights_played']}</b>"
        )

    if s["total_sets_played"] > 0:
        misc_lines.append(f"🎮 Партий сыграно: <b>{s['total_sets_played']}</b>")
    if s["deuce_total"] > 0:
        misc_lines.append(f"🎢 Партий на дьюсе: <b>{s['deuce_total']}</b> (выиграно {s['deuce_won']})")
    if s["first_set_conv"] is not None:
        misc_lines.append(f"⚡ После 1-й партии: <b>{s['first_set_conv']}%</b> побед")
    if s["fav_format"]:
        n = s["fav_format"][0]
        word = "партия" if n == 1 else "партии" if 2 <= n <= 4 else "партий"
        misc_lines.append(f"❤️ Любимый формат: <b>{n} {word}</b>")
    if s["best_day"]:
        misc_lines.append(f"📅 Активный день: <b>{s['best_day']}</b> ({s['best_day_count']} матчей)")
    if s["fav_hour"]:
        hour, hour_count = s["fav_hour"]
        misc_lines.append(
            f"🕐 Любимое время: <b>{hour_range_label(hour)}</b> ({pluralize_matches(hour_count)})"
        )

    if s["lucky_day"]:
        day, wr = s["lucky_day"]
        insight_lines.append(f"🍀 Счастливый день: <b>{day}</b> ({wr}% побед)")
    if s["post_loss"]:
        wr, n = s["post_loss"]
        if wr >= 50:
            insight_lines.append(f"💪 После поражений отыгрываешься: <b>{wr}%</b> побед ({n} матчей)")
        else:
            insight_lines.append(f"😮‍💨 После поражений тяжело: <b>{wr}%</b> побед ({n} матчей)")
    if s["favorite_score"]:
        score, cnt = s["favorite_score"]
        insight_lines.append(f"🎯 Любимый счёт партии: <b>{score}</b> ({cnt} раз)")
    if s["style_insight"]:
        style, own_wr, other_wr = s["style_insight"]
        if style == "sprinter":
            insight_lines.append(
                f"🏃 Ты спринтер: <b>{own_wr}%</b> побед в коротких матчах (vs {other_wr}% в длинных)"
            )
        else:
            insight_lines.append(
                f"🐢 Ты марафонец: <b>{own_wr}%</b> побед в длинных матчах (vs {other_wr}% в коротких)"
            )

    return {
        "form": form_lines,
        "opp": opponent_lines,
        "rating": rating_lines,
        "misc": misc_lines,
        "insight": insight_lines,
    }


def _render_stats_lines(player, s: dict) -> list[str]:
    """Все группы статистики подряд, каждая непустая отделена пустой строкой.
    Плоский вариант (одно сообщение) — для тестов и как база двухуровневого
    экрана (v2.137.0): см. _stats_groups и STATS_SECTIONS."""
    groups = _stats_groups(player, s)
    lines: list[str] = []
    for key in ("form", "opp", "rating", "misc", "insight"):
        group = groups[key]
        if group:
            if lines:
                lines.append("")
            lines.extend(group)
    return lines


# ── Разделы подробной статистики (v2.137.0) ────────────────────────────────────
# Экран статистики/профиля раньше был одной простынёй (~25 строк + последние
# матчи) без проверки на лимит Telegram. Теперь основной экран короткий (шапка,
# форма, индекс легенды, разрыв до соседа/трона, цель), а остальное — в разделах
# на кнопках, по образцу «Достижений» и «Рекордов клуба». Ключ раздела в
# callback_data — стабильная строка, не индекс.

STATS_SECTIONS: list[tuple[str, str]] = [
    ("opp", "🆚 С кем играю"),
    ("rating", "📈 Рейтинг в цифрах"),
    ("game", "🎮 Привычки и советы"),
]

# На чужом профиле «С кем играю» звучит неверно — от третьего лица (v2.148.0).
# Раздел «Последние матчи» убран в v2.148.0: дублировал «📜 История матчей».
OTHER_SECTION_TITLES: dict[str, str] = {"opp": "🆚 С кем играет"}

# Старые сообщения со статистикой/профилем ещё несут кнопку удалённого раздела
# (stat_sec_recent / pstat_{id}_recent) — отвечаем понятно, а не «раздел не найден».
MOVED_RECENT_NOTICE = "Раздел «Последние матчи» теперь в «📜 История матчей»."


def _section_title(key: str, title: str, personal: bool) -> str:
    return title if personal else OTHER_SECTION_TITLES.get(key, title)


def _section_lines(
    key: str, player, s: dict, all_matches: list, include_growth: bool,
) -> list[str]:
    """Строки одного раздела статистики. Пустой список — раздел не показываем.
    include_growth — «Есть над чем поработать» только на личной статистике
    (слабость показываем себе, не другим — см. _growth_area)."""
    groups = _stats_groups(player, s)
    if key == "opp":
        return groups["opp"]
    if key == "rating":
        return groups["rating"]
    if key == "game":
        lines = list(groups["misc"])
        if groups["insight"]:
            if lines:
                lines.append("")
            lines.extend(groups["insight"])
        growth = _growth_area(s) if include_growth else None
        if growth:
            if lines:
                lines.append("")
            lines.append(growth)
        return lines
    return []


def _available_sections(
    player, s: dict, all_matches: list, include_growth: bool,
) -> list[tuple[str, str]]:
    return [
        (key, _section_title(key, title, include_growth)) for key, title in STATS_SECTIONS
        if _section_lines(key, player, s, all_matches, include_growth)
    ]


async def _build_stats_section(
    session: AsyncSession, player: Player, key: str, *, personal: bool,
) -> str | None:
    """Текст раздела статистики; None — раздела с таким ключом нет или он пуст."""
    titles = {k: _section_title(k, t, personal) for k, t in STATS_SECTIONS}
    if key not in titles:
        return None
    all_matches = await get_career_matches(session, player.id, with_opponents=True)
    if not all_matches:
        return None
    s = _compute_player_stats(player, all_matches)
    lines = _section_lines(key, player, s, all_matches, include_growth=personal)
    if not lines:
        return None
    name = h(player.display_name)
    head = (
        f"👤 <b>Мой профиль — {name}</b> · {titles[key]}" if personal
        else f"👤 <b>{name}</b> · {titles[key]}"
    )
    return "\n".join([head, "", *lines])


# ── Разрыв до соседей по таблице / до трона ───────────────────────────────────

def _rank_gap_line(player: Player, players_all: list, ranks: dict[int, int]) -> str | None:
    """«До следующего места» — разрыв в рейтинге до игрока рангом выше.

    При пиннинге чемпиона (боссфайт — #1 не пересчитывается на лету) ранг НЕ
    всегда соответствует сырому рейтингу: игрок рангом выше может иметь более
    низкий сырой рейтинг, чем ты (см. фикс сортировки списка вызова, v2.93.0).
    Если разрыв получается <= 0 — не показываем строку, чтобы не путать
    отрицательным «разрывом» (тебя обгоняют по позиции не по очкам, а по трону).
    """
    my_rank = ranks.get(player.id)
    if not my_rank or my_rank <= 1:
        return None
    prev = next((p for p in players_all if ranks.get(p.id) == my_rank - 1), None)
    if not prev:
        return None
    gap = round(prev.rating - player.rating, 1)
    if gap <= 0:
        return None
    return f"📶 До #{my_rank - 1} (<b>{h(prev.display_name)}</b>): −{gap} pts"


def _throne_distance_line(
    player: Player, champion: Player | None, challenger_player: Player | None, total_matches: int,
) -> str | None:
    """«До трона» — статус игрока в боссфайт-механике: сколько не хватает
    рейтинга/матчей до претендентства, либо призыв вызвать чемпиона, если
    претендент — уже сам игрок. None, если фича боссфайта не активирована
    (чемпион не назначен) или игрок сам чемпион (highlander уже это отражает).

    champion/challenger_player — передаются вызывающим (уже посчитаны для
    ranks/compute_ranks на этом же экране), а не считаются здесь заново —
    иначе каждый просмотр статистики/профиля заново сканировал бы всю
    историю матчей клуба через get_challenger().
    """
    if champion is None or champion.id == player.id:
        return None
    if challenger_player is not None and challenger_player.id == player.id:
        return "🗡 Ты претендент — вызови чемпиона на босс-файт!"
    if player.rating <= champion.rating:
        gap = round(champion.rating - player.rating, 1)
        if gap > 0:
            return f"👑 До трона: −{gap} pts"
        # Точное совпадение рейтинга (gap == 0) — get_challenger() требует
        # СТРОГО больше, ровно столько же ещё не считается «уже выше».
        return "👑 До трона: рейтинг сравнялся с чемпионом — нужно чуть больше очков"
    # Рейтинг уже выше чемпиона — дело за порогом матчей или за тем, что
    # претендентское место сейчас занято кем-то ещё с рейтингом выше.
    if total_matches < NEWCOMER_THRESHOLD:
        left = NEWCOMER_THRESHOLD - total_matches
        return f"🗡 До статуса претендента: рейтинг уже выше чемпиона, не хватает матчей ({left})"
    if challenger_player is not None:
        gap = round(challenger_player.rating - player.rating, 1)
        if gap > 0:
            return (
                f"🗡 До статуса претендента: −{gap} pts "
                f"(сейчас впереди <b>{h(challenger_player.display_name)}</b>)"
            )
    return None


def _append_rank_and_throne_lines(lines: list[str], rank_gap: str | None, throne_line: str | None) -> None:
    """Добавляет «до соседа»/«до трона» как отдельную группу — с пустой строкой
    перед ней, как и остальные группы _render_stats_lines() (v2.99.0). Раньше
    строки добавлялись напрямую через lines.append() без разделителя и
    физически слипались с последней группой статистики."""
    extra = [x for x in (rank_gap, throne_line) if x]
    if extra:
        lines.append("")
        lines.extend(extra)


# ── My stats ──────────────────────────────────────────────────────────────────

async def _build_stats_screen(session: AsyncSession, player: Player):
    """Строит (текст, клавиатуру) экрана «Мой профиль» (раньше «Статистика») для уже найденного
    игрока — общая часть для инлайн-кнопки меню (edit_text) и постоянной
    клавиатуры снизу (answer)."""
    players_all, champion, _match_counts, ranks, rank_str, challenger_player = (
        await _load_ranking_context(session, player)
    )

    all_matches = await get_career_matches(session, player.id, with_opponents=True)

    if not all_matches:
        return (
            f"👤 <b>Мой профиль — {h(player.display_name)}</b>\n\n"
            f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — {rank_str}  🎖 {rank_title(player.rating)}\n\n"
            f"Ты ещё не сыграл ни одного матча.\nВызови кого-нибудь! 🏓",
            stats_kb(),
        )

    s = _compute_player_stats(player, all_matches)

    mvp_id = await get_mvp_of_month(session)

    draws_part = f"  |  🤝 Ничьих: <b>{s['draws']}</b>" if s["draws"] > 0 else ""
    lines = [
        f"👤 <b>Мой профиль — {h(player.display_name)}</b>\n",
        f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — {rank_str}  🎖 {rank_title(player.rating)}",
        f"🏆 Побед: <b>{s['wins']}</b>{draws_part}  |  💔 Поражений: <b>{s['losses']}</b>",
        f"📊 Винрейт: матчи <b>{s['win_rate']}%</b> · партии <b>{s['sets_win_rate']}%</b>",
    ]
    if mvp_id == player.id:
        lines.append("🌟 Ты MVP месяца!")

    form = _stats_groups(player, s)["form"]
    if form:
        lines.append("")
        lines.extend(form)

    legend_index, legend_rank, legend_total = await _legend_index_with_rank(session, player, players_all)
    lines.append("")
    lines.append(
        f"🏵 Индекс легенды: <b>{legend_index}</b>  <i>(#{legend_rank} из {legend_total})</i>"
        f" — ачивки, рекорды, боссфайты"
    )

    rank_gap = _rank_gap_line(player, players_all, ranks)
    throne_line = _throne_distance_line(
        player, champion, challenger_player, s["wins"] + s["draws"] + s["losses"]
    )
    _append_rank_and_throne_lines(lines, rank_gap, throne_line)

    progress = _nearest_achievement_progress(player, s, len(players_all))
    if progress:
        lines.append(progress)

    sections = _available_sections(player, s, all_matches, include_growth=True)
    return "\n".join(lines), stats_kb(sections)


@router.callback_query(F.data == "menu_stats")
async def show_my_stats(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    await callback.answer()
    text, kb = await _build_stats_screen(session, player)
    await cb_msg(callback).edit_text(text, reply_markup=kb)


@router.message(F.text.in_(REPLY_KB_PROFILE_ALL))
async def show_my_stats_from_reply_kb(message: Message, session: AsyncSession):
    """Тот же экран, что и menu_stats, но с постоянной клавиатуры снизу."""
    player = await get_player(session, msg_user(message).id)
    if not player:
        await message.answer("Сначала напиши /start 🏓")
        return
    text, kb = await _build_stats_screen(session, player)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("stat_sec_"))
async def show_my_stats_section(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    key = cb_data(callback).removeprefix("stat_sec_")
    if key == "recent":
        await callback.answer(MOVED_RECENT_NOTICE, show_alert=True)
        return
    text = await _build_stats_section(session, player, key, personal=True)
    if text is None:
        await callback.answer("Раздел не найден или пока пуст.", show_alert=True)
        return
    await callback.answer()
    await cb_msg(callback).edit_text(text, reply_markup=stats_section_kb())


@router.callback_query(F.data.startswith("pstat_"))
async def show_player_stats_section(callback: CallbackQuery, session: AsyncSession):
    try:
        _, raw_id, key = cb_data(callback).split("_", 2)
        target_id = int(raw_id)
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    tp_r = await session.execute(select(Player).where(Player.id == target_id))
    target = tp_r.scalar_one_or_none()
    if not target:
        await callback.answer("Игрок не найден.", show_alert=True)
        return
    if key == "recent":
        await callback.answer(MOVED_RECENT_NOTICE, show_alert=True)
        return
    text = await _build_stats_section(session, target, key, personal=False)
    if text is None:
        await callback.answer("Раздел не найден или пока пуст.", show_alert=True)
        return
    await callback.answer()
    await cb_msg(callback).edit_text(text, reply_markup=player_stats_section_kb(target.id))


# ── Карьер-рекап ──────────────────────────────────────────────────────────────
# «Highlight reel» по запросу в любое время — та же идея, что у «Итогов года»
# (scheduler.py, send_yearly_summary), но не привязана к календарю: пользователь
# явно попросил доступ круглый год, не раз в 31 декабря. Собирается ИЗ уже
# посчитанных _compute_player_stats() полей, кроме двух новых источников —
# числа заработанных ачивок (get_achievements) и числа уникальных ПОБИТЫХ
# личных рекордов (PersonalRecordEarned, distinct по metric — метрику можно
# бить многократно, но в рекапе интересно, СКОЛЬКО ИЗ 7 хоть раз покорились).

@router.callback_query(F.data == "career_recap")
async def show_career_recap(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    await callback.answer()

    all_matches = await get_career_matches(session, player.id, with_opponents=True)
    if not all_matches:
        await cb_msg(callback).edit_text(
            f"🎬 <b>Моя история — {h(player.display_name)}</b>\n\n"
            f"Пока рассказывать нечего — сыграй свой первый матч! 🏓",
            reply_markup=back_to_stats_kb(),
        )
        return

    s = _compute_player_stats(player, all_matches)
    total = s["wins"] + s["draws"] + s["losses"]
    joined_str = player.created_at.strftime("%d.%m.%y") if player.created_at else "неизвестно когда"

    earned_ids = get_achievements(player)
    achievements_line = f"🏅 Ачивок открыто: <b>{len(earned_ids)}/{len(ACHIEVEMENTS_LIST)}</b>"

    pr_count = await get_personal_records_count(session, player.id)
    pr_line = f"💎 Личных рекордов покорено: <b>{pr_count}/7</b>"

    draws_part = f" / <b>{s['draws']}</b> ничьих" if s["draws"] > 0 else ""
    lines = [
        f"🎬 <b>Моя история — {h(player.display_name)}</b>\n",
        f"В клубе с <b>{joined_str}</b>. Позади <b>{pluralize_matches(total)}</b>: "
        f"<b>{s['wins']}</b> побед / <b>{s['losses']}</b> поражений{draws_part} "
        f"(<b>{s['win_rate']}%</b> винрейт).",
    ]

    narrative = _build_career_narrative(player, s)
    if narrative:
        lines.append("")
        lines.append(narrative)

    lines.append("")
    lines.append(f"⭐ Рейтинг сейчас: <b>{round(player.rating, 1)}</b> pts — 🎖 {rank_title(player.rating)}")
    if player.peak_rating and player.peak_rating > player.rating:
        lines.append(f"📈 Пик за карьеру: <b>{round(player.peak_rating, 1)}</b> pts")
    if s["best_win"] is not None:
        lines.append(f"🏆 Лучшая победа: <b>+{s['best_win']} pts</b>")
    if s["best_streak"] >= 2:
        lines.append(f"🔥 Лучшая серия: <b>{s['best_streak']} побед подряд</b>")
    if s["best_opp"]:
        bo = s["best_opp"]
        lines.append(f"🎁 Подарок: <b>{h(bo['name'])}</b> ({bo['wins']}–{bo['losses']}, {bo['rate']}% побед)")
    if s["nemesis"]:
        ne = s["nemesis"]
        lines.append(f"😱 Кошмар: <b>{h(ne['name'])}</b> ({ne['wins']}–{ne['losses']}, {ne['rate']}% поражений)")
    if s["boss_fights_played"] > 0:
        lines.append(f"⚔️ Боссфайты: <b>{s['boss_fights_won']}/{s['boss_fights_played']}</b>")
    if player.is_champion:
        lines.append("👑 Прямо сейчас на троне клуба.")
    lines.append("")
    lines.append(achievements_line)
    lines.append(pr_line)

    await cb_msg(callback).edit_text("\n".join(lines), reply_markup=back_to_stats_kb())


# ── Player profile (public view) ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("player_profile_"))
async def show_player_profile(callback: CallbackQuery, session: AsyncSession):
    try:
        target_id = int(cb_data(callback).split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    tp_r = await session.execute(select(Player).where(Player.id == target_id))
    player = tp_r.scalar_one_or_none()
    if not player:
        await callback.answer("Игрок не найден.", show_alert=True)
        return

    await callback.answer()

    viewer = await get_player(session, callback.from_user.id)
    viewer_id = viewer.id if viewer else None

    # Кнопку «Вызвать» скрываем, если занят зритель ИЛИ владелец профиля —
    # у игрока может быть только один активный матч одновременно (не только
    # именно с этим человеком). Кнопка «Личные встречи» (read-only) показывается
    # всегда для чужого профиля.
    can_challenge = True
    if viewer and viewer.id != player.id:
        if (
            await get_active_match(session, viewer.id)
            or await get_active_match(session, player.id)
            or await boss_fight_rematch_blocked(session, viewer.id, player.id)
        ):
            can_challenge = False

    players_all, champion, _match_counts, ranks, rank_str, challenger_player = (
        await _load_ranking_context(session, player)
    )

    all_matches = await get_career_matches(session, player.id, with_opponents=True)

    s = _compute_player_stats(player, all_matches)

    draws_part = f"  |  🤝 Ничьих: <b>{s['draws']}</b>" if s["draws"] > 0 else ""
    lines = [
        f"👤 <b>{h(player.display_name)}</b>\n",
        f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — {rank_str}  🎖 {rank_title(player.rating)}",
        f"🏆 Побед: <b>{s['wins']}</b>{draws_part}  |  💔 Поражений: <b>{s['losses']}</b>",
        f"📊 Винрейт: матчи <b>{s['win_rate']}%</b> · партии <b>{s['sets_win_rate']}%</b>",
    ]

    form = _stats_groups(player, s)["form"]
    if form:
        lines.append("")
        lines.extend(form)

    legend_index, legend_rank, legend_total = await _legend_index_with_rank(session, player, players_all)
    lines.append("")
    lines.append(
        f"🏵 Индекс легенды: <b>{legend_index}</b>  <i>(#{legend_rank} из {legend_total})</i>"
        f" — ачивки, рекорды, боссфайты"
    )

    rank_gap = _rank_gap_line(player, players_all, ranks)
    throne_line = _throne_distance_line(
        player, champion, challenger_player, s["wins"] + s["draws"] + s["losses"]
    )
    _append_rank_and_throne_lines(lines, rank_gap, throne_line)

    sections = _available_sections(player, s, all_matches, include_growth=False)
    await cb_msg(callback).edit_text(
        "\n".join(lines),
        reply_markup=player_profile_kb(
            player.id, viewer_id=viewer_id, can_challenge=can_challenge, sections=sections,
        ),
    )


# ── Калькулятор «Что если» (базовая версия, v2.121.0) ────────────────────────────
# Ориентировочная дельта рейтинга для гипотетического матча — без начала матча
# и без записи в БД, чистое вычисление по already-known рейтингам. Диапазон
# («на тоненького» vs «разгром»), не точная цифра — реальный счёт партий
# заранее не известен. Расширенный вариант (3 сценария, апсет отдельно) — на
# паузе, см. CLAUDE.md.

@router.callback_query(F.data.startswith("what_if_"))
async def show_what_if(callback: CallbackQuery, session: AsyncSession):
    try:
        target_id = int(cb_data(callback).rsplit("_", 1)[-1])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    viewer = await get_player(session, callback.from_user.id)
    if not viewer:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return

    tp_r = await session.execute(select(Player).where(Player.id == target_id))
    opponent = tp_r.scalar_one_or_none()
    if not opponent:
        await callback.answer("Игрок не найден.", show_alert=True)
        return

    await callback.answer()

    # Тот же расчёт, что и в show_player_profile/show_h2h — не ведём кнопкой
    # «Вызвать» в тупик, если зритель или соперник уже заняты активным матчем.
    can_challenge = not (
        await get_active_match(session, viewer.id)
        or await get_active_match(session, opponent.id)
        or await boss_fight_rematch_blocked(session, viewer.id, opponent.id)
    )

    match_counts = await get_match_counts(session)
    viewer_is_newcomer = match_counts.get(viewer.id, 0) < NEWCOMER_THRESHOLD
    (win_lo, win_hi), (lose_lo, lose_hi) = what_if_range(
        viewer.rating, opponent.rating, viewer_is_newcomer,
    )

    await cb_msg(callback).answer(
        f"🎲 Если сыграешь с <b>{h(opponent.display_name)}</b> сейчас:\n\n"
        f"Твой рейтинг: <b>{round(viewer.rating)}</b> pts\n"
        f"Рейтинг соперника: <b>{round(opponent.rating)}</b> pts\n\n"
        f"🏆 Выиграешь — примерно <b>+{win_lo}…+{win_hi}</b> pts\n"
        f"💔 Проиграешь — примерно <b>−{lose_lo}…−{lose_hi}</b> pts\n\n"
        f"<i>Точная цифра зависит от счёта партий</i>",
        reply_markup=what_if_kb(opponent.id, can_challenge=can_challenge),
    )


# ── Achievements ──────────────────────────────────────────────────────────────

def _achievement_category_progress(earned_ids: list[str]) -> list[tuple[str, int, int]]:
    """(категория, получено, всего) для каждой категории из CATEGORY_ORDER —
    общее для текста оглавления (пока не используется напрямую) и кнопок
    категорий (achievements_kb/player_achievements_kb, inline.py)."""
    earned_set = set(earned_ids)
    result = []
    for category in CATEGORY_ORDER:
        achs = [a for a in ACHIEVEMENTS_LIST if a.category == category]
        earned_count = sum(1 for a in achs if a.id in earned_set)
        result.append((category, earned_count, len(achs)))
    return result


def _render_achievements_toc(earned_ids: list[str], title: str) -> str:
    """Оглавление экрана достижений — только заголовок с общим счётом.

    Список категорий больше не в тексте (v2.133.0, редизайн этапа 2 дорожной
    карты) — он живёт на кнопках (achievements_kb/player_achievements_kb),
    чтобы не дублировать одну и ту же цифру в тексте и на кнопке. Сами
    ачивки — на отдельном экране категории (_render_achievement_category).
    """
    total = len(ACHIEVEMENTS_LIST)
    earned_set = set(earned_ids)
    count = len([a for a in ACHIEVEMENTS_LIST if a.id in earned_set])
    return f"🏅 <b>{title}</b>  ({count} из {total})"


def _render_achievement_category(earned_ids: list[str], category: str) -> str:
    """Текст экрана ОДНОЙ категории — список её ачивок (сначала ✅ полученные,
    потом 🔒 невыполненные; скрытые неполученные — «🔒 ???»).

    Раньше (до v2.133.0) весь список из 6 категорий уходил одним сообщением
    и трижды подряд упирался в лимит Telegram (4096 символов) — лечили
    обрезкой описаний ачивок. Теперь лимит проверяется НА КАТЕГОРИЮ (см.
    test_render_achievement_category_stays_under_telegram_limit), а не на
    весь список сразу — самая крупная категория («Объём и вехи», 20 ачивок)
    всё равно намного меньше прежнего 59-пунктового списка, поэтому
    полные описания вернули без урезания."""
    earned_set = set(earned_ids)
    achs = [a for a in ACHIEVEMENTS_LIST if a.category == category]
    earned_count = sum(1 for a in achs if a.id in earned_set)

    lines = [f"<b>{category}</b>  ({earned_count} из {len(achs)})", ""]
    entries = []
    for a in sorted(achs, key=lambda a: a.id not in earned_set):
        if a.id in earned_set:
            entries.append(f"✅ {a.emoji} <b>{a.name}</b> — <i>{a.desc}</i>")
        elif a.hidden:
            entries.append("🔒 ???")
        else:
            entries.append(f"🔒 {a.emoji} {a.name} — <i>{a.desc}</i>")
    lines.append("\n\n".join(entries))
    return "\n".join(lines)


@router.callback_query(F.data == "my_achievements")
async def show_my_achievements(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    await callback.answer()
    earned = get_achievements(player)
    text = _render_achievements_toc(earned, "Мои достижения")
    progress = _achievement_category_progress(earned)
    await cb_msg(callback).edit_text(text, reply_markup=achievements_kb(progress))


@router.callback_query(F.data.startswith("player_achievements_"))
async def show_player_achievements(callback: CallbackQuery, session: AsyncSession):
    try:
        target_id = int(cb_data(callback).removeprefix("player_achievements_"))
    except ValueError:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    tp_r = await session.execute(select(Player).where(Player.id == target_id))
    player = tp_r.scalar_one_or_none()
    if not player:
        await callback.answer("Игрок не найден.", show_alert=True)
        return
    await callback.answer()
    earned = get_achievements(player)
    text = _render_achievements_toc(earned, f"Достижения — {h(player.display_name)}")
    progress = _achievement_category_progress(earned)
    await cb_msg(callback).edit_text(
        text,
        reply_markup=player_achievements_kb(target_id, progress),
    )


@router.callback_query(F.data.startswith("ach_cat_"))
async def show_my_achievement_category(callback: CallbackQuery, session: AsyncSession):
    """Экран ОДНОЙ категории своих достижений — по кнопке с оглавления."""
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    try:
        category = CATEGORY_ORDER[int(cb_data(callback).removeprefix("ach_cat_"))]
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    await callback.answer()
    earned = get_achievements(player)
    text = _render_achievement_category(earned, category)
    await cb_msg(callback).edit_text(text, reply_markup=achievement_category_kb())


@router.callback_query(F.data.startswith("pach_"))
async def show_player_achievement_category(callback: CallbackQuery, session: AsyncSession):
    """Экран ОДНОЙ категории достижений другого игрока — по кнопке с его оглавления.

    callback_data: pach_{player_id}_{индекс категории в CATEGORY_ORDER}. Не
    начинается с "player_achievements_", чтобы не попасть под startswith
    хендлера оглавления выше."""
    raw = cb_data(callback).removeprefix("pach_")
    try:
        player_id_str, idx_str = raw.rsplit("_", 1)
        target_id = int(player_id_str)
        category = CATEGORY_ORDER[int(idx_str)]
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    tp_r = await session.execute(select(Player).where(Player.id == target_id))
    player = tp_r.scalar_one_or_none()
    if not player:
        await callback.answer("Игрок не найден.", show_alert=True)
        return
    await callback.answer()
    earned = get_achievements(player)
    text = _render_achievement_category(earned, category)
    await cb_msg(callback).edit_text(text, reply_markup=achievement_category_kb(target_id))
