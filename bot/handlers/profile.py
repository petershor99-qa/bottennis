from html import escape as h

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import PersonalRecordEarned, Player
from bot.keyboards.inline import (
    achievements_kb,
    back_to_stats_kb,
    player_achievements_kb,
    player_profile_kb,
    stats_kb,
)
from bot.services.achievements import (
    ACHIEVEMENTS_LIST,
    CATEGORY_ORDER,
    get_achievements,
)
from bot.services.stats import _build_career_narrative, _compute_player_stats, _nearest_achievement_progress
from bot.utils import (
    NEWCOMER_THRESHOLD,
    _challenger_among,
    _match_line,
    boss_fight_rematch_blocked,
    compute_ranks,
    format_rank,
    get_active_match,
    get_career_matches,
    get_match_counts,
    get_player,
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

def _render_stats_lines(player, s: dict) -> list[str]:
    """Формирует общие строки статистики (форма, серии, соперники, рекорды и т.д.).

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

    if s["best_opp"]:
        opponent_lines.append(f"🎁 Подарок: <b>{h(s['best_opp']['name'])}</b> ({s['best_opp']['wins']} побед)")
    if s["nemesis"]:
        opponent_lines.append(f"😱 Кошмар: <b>{h(s['nemesis']['name'])}</b> ({s['nemesis']['losses']} поражений)")
    top_opp = s["top_opp"]
    if top_opp and top_opp["total"] >= 2:
        top_draws_str = f" 🤝{top_opp['draws']}" if top_opp["draws"] else ""
        opponent_lines.append(
            f"⚔️ Чаще всего: <b>{h(top_opp['name'])}</b> "
            f"({top_opp['total']} матчей, {top_opp['wins']}–{top_opp['losses']}{top_draws_str})"
        )

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

    lines: list[str] = []
    for group in (form_lines, opponent_lines, rating_lines, misc_lines, insight_lines):
        if group:
            if lines:
                lines.append("")
            lines.extend(group)
    return lines


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
    """Строит (текст, клавиатуру) экрана «Статистика» для уже найденного
    игрока — общая часть для инлайн-кнопки меню (edit_text) и постоянной
    клавиатуры снизу (answer)."""
    players_all, champion, _match_counts, ranks, rank_str, challenger_player = (
        await _load_ranking_context(session, player)
    )

    all_matches = await get_career_matches(session, player.id, with_opponents=True)

    if not all_matches:
        return (
            f"📈 <b>Статистика — {h(player.display_name)}</b>\n\n"
            f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — {rank_str}  🎖 {rank_title(player.rating)}\n\n"
            f"Ты ещё не сыграл ни одного матча.\nВызови кого-нибудь! 🏓",
            stats_kb(),
        )

    matches = all_matches[:5]
    s = _compute_player_stats(player, all_matches)

    draws_part = f"  |  🤝 Ничьих: <b>{s['draws']}</b>" if s["draws"] > 0 else ""
    lines = [
        f"📈 <b>Статистика — {h(player.display_name)}</b>\n",
        f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — {rank_str}  🎖 {rank_title(player.rating)}",
        f"🏆 Побед: <b>{s['wins']}</b>{draws_part}  |  💔 Поражений: <b>{s['losses']}</b>",
        f"📊 Матчи: <b>{s['win_rate']}%</b>  |  🎯 Партии: <b>{s['sets_win_rate']}%</b>",
    ]

    lines.extend(_render_stats_lines(player, s))

    rank_gap = _rank_gap_line(player, players_all, ranks)
    throne_line = _throne_distance_line(
        player, champion, challenger_player, s["wins"] + s["draws"] + s["losses"]
    )
    _append_rank_and_throne_lines(lines, rank_gap, throne_line)

    progress = _nearest_achievement_progress(player, s, len(players_all))
    if progress:
        lines.append(progress)

    if matches:
        lines.append("\n<b>Последние матчи:</b>")
        for m in matches:
            lines.append(_match_line(m, player.id))

    return "\n".join(lines), stats_kb()


@router.callback_query(F.data == "menu_stats")
async def show_my_stats(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    await callback.answer()
    text, kb = await _build_stats_screen(session, player)
    await callback.message.edit_text(text, reply_markup=kb)


@router.message(F.text == "📈 Статистика")
async def show_my_stats_from_reply_kb(message: Message, session: AsyncSession):
    """Тот же экран, что и menu_stats, но с постоянной клавиатуры снизу."""
    player = await get_player(session, message.from_user.id)
    if not player:
        await message.answer("Сначала напиши /start 🏓")
        return
    text, kb = await _build_stats_screen(session, player)
    await message.answer(text, reply_markup=kb)


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
        await callback.message.edit_text(
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

    pr_r = await session.execute(
        select(func.count(func.distinct(PersonalRecordEarned.metric)))
        .where(PersonalRecordEarned.player_id == player.id)
    )
    pr_count = pr_r.scalar() or 0
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
        lines.append(f"🎁 Подарок: <b>{h(s['best_opp']['name'])}</b> ({s['best_opp']['wins']} побед)")
    if s["nemesis"]:
        lines.append(f"😱 Кошмар: <b>{h(s['nemesis']['name'])}</b> ({s['nemesis']['losses']} поражений)")
    if s["boss_fights_played"] > 0:
        lines.append(f"⚔️ Боссфайты: <b>{s['boss_fights_won']}/{s['boss_fights_played']}</b>")
    if player.is_champion:
        lines.append("👑 Прямо сейчас на троне клуба.")
    lines.append("")
    lines.append(achievements_line)
    lines.append(pr_line)

    await callback.message.edit_text("\n".join(lines), reply_markup=back_to_stats_kb())


# ── Player profile (public view) ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("player_profile_"))
async def show_player_profile(callback: CallbackQuery, session: AsyncSession):
    try:
        target_id = int(callback.data.split("_")[2])
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
    matches = all_matches[:5]

    s = _compute_player_stats(player, all_matches)

    draws_part = f"  |  🤝 Ничьих: <b>{s['draws']}</b>" if s["draws"] > 0 else ""
    lines = [
        f"👤 <b>{h(player.display_name)}</b>\n",
        f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — {rank_str}  🎖 {rank_title(player.rating)}",
        f"🏆 Побед: <b>{s['wins']}</b>{draws_part}  |  💔 Поражений: <b>{s['losses']}</b>",
        f"📊 Винрейт: <b>{s['win_rate']}%</b>",
    ]

    lines.extend(_render_stats_lines(player, s))

    rank_gap = _rank_gap_line(player, players_all, ranks)
    throne_line = _throne_distance_line(
        player, champion, challenger_player, s["wins"] + s["draws"] + s["losses"]
    )
    _append_rank_and_throne_lines(lines, rank_gap, throne_line)

    if matches:
        lines.append("\n<b>Последние матчи:</b>")
        for m in matches:
            lines.append(_match_line(m, player.id))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=player_profile_kb(player.id, viewer_id=viewer_id, can_challenge=can_challenge),
    )


# ── Achievements ──────────────────────────────────────────────────────────────

def _render_achievements(earned_ids: list[str], title: str) -> str:
    """Формирует текст экрана достижений, сгруппированный по категориям.

    Внутри каждой категории — сначала полученные (✅, в порядке ACHIEVEMENTS_LIST),
    потом невыполненные (🔒). Плоский список 30+ пунктов подряд читается как
    нечитаемая простыня — тот же принцип, что уже применён к «Статистике»
    (_render_stats_lines) и «Рекордам клуба» (leaderboard.py, v2.73.0).

    Пустая строка между КАЖДЫМ пунктом (не только между категориями, v2.98.0) —
    по просьбе пользователя после живого скриншота прод-экрана: плотный список
    из 43 длинных строк (имя + условие) читался тяжело даже разбитым на 6
    категорий.

    Заголовок категории отбит ДВУМЯ пустыми строками сверху и одной снизу
    (v2.99.0) — с одинарным отступом заголовок визуально не отличался от
    обычного разрыва между пунктами и разделы «сливались» друг с другом.

    Стоит копейки по длине (несколько лишних '\\n', не повтор текста) — даже
    в худшем случае (все 43 заработаны, у каждой строки развёрнутое
    имя+условие) укладывается в лимит Telegram на сообщение (4096 символов)
    с запасом, см. test_render_achievements_stays_under_telegram_limit.
    """
    total = len(ACHIEVEMENTS_LIST)
    earned_set = set(earned_ids)
    count = len([a for a in ACHIEVEMENTS_LIST if a.id in earned_set])
    lines = [f"🏅 <b>{title}</b>  ({count} из {total})"]

    by_category: dict[str, list] = {}
    for a in ACHIEVEMENTS_LIST:
        by_category.setdefault(a.category, []).append(a)

    for category in CATEGORY_ORDER:
        achs = by_category.get(category, [])
        if not achs:
            continue
        lines.append(f"\n\n<b>{category}</b>\n")
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
    text = _render_achievements(earned, "Мои достижения")
    await callback.message.edit_text(text, reply_markup=achievements_kb())


@router.callback_query(F.data.startswith("player_achievements_"))
async def show_player_achievements(callback: CallbackQuery, session: AsyncSession):
    try:
        target_id = int(callback.data.removeprefix("player_achievements_"))
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
    text = _render_achievements(earned, f"Достижения — {h(player.display_name)}")
    await callback.message.edit_text(
        text,
        reply_markup=player_achievements_kb(target_id),
    )
