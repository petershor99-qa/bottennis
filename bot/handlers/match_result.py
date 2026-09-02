from datetime import datetime, timezone
from html import escape as h

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import and_, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, MatchStatus, Player
from bot.keyboards.inline import after_set_kb, back_to_menu_kb, main_menu_kb, rematch_kb
from bot.services.achievements import (
    ACHIEVEMENTS_MAP,
    check_boss_fight_challenger_defeat_achievement,
    check_boss_fight_defense_achievement,
    check_chance_blown_achievement,
    check_draw_achievements,
    check_loss_achievements,
    check_win_achievements,
)
from bot.services.personal_records import (
    check_personal_records_on_draw,
    check_personal_records_on_loss,
    check_personal_records_on_win,
)
from bot.services.rating import calculate_draw_rating_change, calculate_rating_change
from bot.services.validation import validate_set_score
from bot.states.states import MatchResultStates
from bot.utils import (
    NEWCOMER_THRESHOLD,
    get_challenger,
    get_champion,
    get_h2h_matches,
    get_player,
    match_report,
    msk_day_start,
    msk_hour_and_weekday,
    notify_all_players,
    rating_tenths,
    try_transfer_champion,
)

router = Router()

# ── Константы рейтинговой системы ─────────────────────────────────────────────
# NEWCOMER_THRESHOLD (порог новичок/ветеран, он же порог права на босс-файт) — в utils.py
NEWCOMER_FLOOR = 1000.0   # пол рейтинга для новичков (<15 матчей)
VETERAN_FLOOR = 900.0     # пол рейтинга для ветеранов (15+ матчей)
NEWCOMER_BONUS = 1.2      # бонус к победам новичка
REPEAT_MIN = 0.5          # минимальный множитель за повтор
MAX_SETS = 10             # максимальное число партий в матче
BOSS_FIGHT_MULT = 2.0     # множитель дельты в босс-файте


def _fmt_delta(d: float) -> str:
    """Форматирует дельту рейтинга: +8.5 или -3.2"""
    return f"+{d}" if d >= 0 else str(d)


async def _send_personal_records(bot: Bot, player: Player, messages: list[str]) -> None:
    """Отправляет игроку все сработавшие уведомления о личных рекордах
    (bot/services/personal_records.py) — их может быть несколько за один матч."""
    for text in messages:
        try:
            await bot.send_message(player.telegram_id, text)
        except Exception:
            pass


async def _notify_achievements(bot: Bot, player, new_ids: list[str]) -> None:
    """Отправляет игроку уведомление о новых достижениях."""
    if not new_ids:
        return
    achs = [ACHIEVEMENTS_MAP[aid] for aid in new_ids if aid in ACHIEVEMENTS_MAP]
    if not achs:
        return
    if len(achs) == 1:
        a = achs[0]
        text = f"🏅 <b>Новое достижение!</b>\n\n{a.emoji} <b>{a.name}</b>\n<i>{a.desc}</i>"
    else:
        lines = "\n".join(f"{a.emoji} <b>{a.name}</b> — <i>{a.desc}</i>" for a in achs)
        text = f"🏅 <b>Новые достижения!</b>\n\n{lines}"
    try:
        await bot.send_message(player.telegram_id, text)
    except Exception:
        pass


async def _collect_egg_context(
    session: AsyncSession,
    winner: Player,
    loser: Player,
    final_sets: list[dict],
    match_id: int,
    old_winner_rating: float,
    old_loser_rating: float,
    h2h_matches: list[Match],
) -> dict:
    """Собирает все данные для пасхалок из БД. Возвращает контекст.

    h2h_matches — завершённые матчи winner/loser ДО текущего (desc по
    completed_at), уже загруженные вызывающим через bot.utils.get_h2h_matches()
    — раньше здесь были ещё 2 отдельных запроса за той же историей.
    """

    # ── Матчи победителя ─────────────────────────────────────────────────────
    w_r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == winner.id, Match.challenged_id == winner.id),
            Match.status == MatchStatus.completed,
        )
        .order_by(desc(Match.completed_at))
    )
    w_matches = w_r.scalars().all()

    previous_wins = sum(1 for m in w_matches if m.winner_id == winner.id and m.id != match_id)
    streak = 0
    for m in w_matches:
        if m.winner_id == winner.id:
            streak += 1
        else:
            break

    loss_streak_before = 0
    for m in w_matches:
        if m.id == match_id:
            continue
        if m.winner_id != winner.id:
            loss_streak_before += 1
        else:
            break

    # ── H2H: первая кровь и реванш ───────────────────────────────────────────
    first_blood = not any(m.winner_id == winner.id for m in h2h_matches)
    revenge = bool(h2h_matches) and h2h_matches[0].winner_id == loser.id

    # ── Впервые на #1 ────────────────────────────────────────────────────────
    first_time_top1 = False
    top1_r = await session.execute(
        select(func.count()).select_from(Player).where(Player.rating > winner.rating)
    )
    if top1_r.scalar() == 0:
        others_r = await session.execute(
            select(func.count()).select_from(Player).where(
                Player.rating > old_winner_rating,
                Player.id != winner.id,
                Player.id != loser.id,
            )
        )
        was_top1 = (others_r.scalar() == 0 and old_loser_rating <= old_winner_rating)
        first_time_top1 = not was_top1

    # ── Матчи проигравшего ───────────────────────────────────────────────────
    l_r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == loser.id, Match.challenged_id == loser.id),
            Match.status == MatchStatus.completed,
        )
        .order_by(desc(Match.completed_at))
    )
    l_matches = l_r.scalars().all()

    loss_streak = 0
    for m in l_matches:
        if m.winner_id is not None and m.winner_id != loser.id:
            loss_streak += 1
        else:
            break

    prev_losses = sum(
        1 for m in l_matches
        if m.winner_id is not None and m.winner_id != loser.id and m.id != match_id
    )

    return {
        # факты матча
        "flawless":          any(s["l"] == 0 for s in final_sets),
        "clean_sweep":       len(final_sets) >= 2 and all(s["w"] > s["l"] for s in final_sets),
        "shutout":           all(s["l"] == 0 for s in final_sets),
        "deuce_decider":     final_sets[-1]["w"] >= 12 and final_sets[-1]["w"] - final_sets[-1]["l"] == 2,
        "comeback":          len(final_sets) >= 3 and final_sets[0]["w"] < final_sets[0]["l"],
        "marathon":          len(final_sets) >= 5,
        "old_winner_rating": old_winner_rating,
        "old_loser_rating":  old_loser_rating,
        # победитель
        "previous_wins":     previous_wins,
        "streak":            streak,
        "loss_streak_before": loss_streak_before,
        "first_blood":       first_blood,
        "revenge":           revenge,
        "first_time_top1":   first_time_top1,
        "winner_total":      len(w_matches),
        # проигравший
        "loss_streak":       loss_streak,
        "prev_losses":       prev_losses,
        "loser_total":       len(l_matches),
    }


async def _send_winner_eggs(bot: Bot, winner: Player, loser: Player, ctx: dict) -> None:
    """Отправляет пасхалки победителю."""

    async def _msg(text: str, **kw) -> None:
        try:
            await bot.send_message(winner.telegram_id, text, **kw)
        except Exception:
            pass

    if ctx["flawless"]:
        await _msg("🩸 Flawless Victory")
    if ctx["clean_sweep"]:
        await _msg("💥 FINISH HIM!")
    if ctx["shutout"]:
        await _msg("Читы включил? 🎮")
    if ctx["deuce_decider"]:
        await _msg("⚡ Драматично!")
    if rating_tenths(winner.rating) % 500 == 0:
        await _msg(f"🎯 Ровно {round(winner.rating, 1)}. Как ты это подгадал?")

    # Серийная пасхалка (по приоритету)
    streak, previous_wins = ctx["streak"], ctx["previous_wins"]
    if previous_wins == 0:
        egg = "🎮 First kill"
    elif streak == 10:
        egg = "😤 Пососано нахуй"
    elif streak == 5:
        egg = "🔥 Я горяч нахуй"
    elif streak == 3:
        egg = "💪 Абать ты хорош"
    elif ctx["old_loser_rating"] - ctx["old_winner_rating"] >= 100:
        egg = "🤖 Аста лависта бэйби!"
    else:
        egg = None
    if egg:
        await _msg(egg)

    if ctx["revenge"]:
        await _msg("⚡ Мы в расчёте")
    if ctx["comeback"]:
        await _msg("💪 Упал — отжался — победил")
    if ctx["first_time_top1"]:
        await _msg("👑 Трон твой. Пока.")
    if ctx["first_blood"]:
        await _msg(f"🩸 Первая кровь — <b>{h(loser.display_name)}</b>")
    if ctx["loss_streak_before"] >= 3:
        await _msg("💪 Вылез из жопы")

    total = ctx["winner_total"]
    if total in range(25, 501, 25):
        milestone = (
            f"🤯 {total}-й матч. Дальше уже считать бессмысленно."
            if total == 500
            else f"🎯 Юбилей! Это твой <b>{total}-й</b> матч!"
        )
        await _msg(milestone)


async def _send_loser_eggs(
    bot: Bot, loser: Player, winner: Player, ctx: dict, old_loser_rating: float
) -> None:
    """Отправляет пасхалки проигравшему."""

    async def _msg(text: str, **kw) -> None:
        try:
            await bot.send_message(loser.telegram_id, text, **kw)
        except Exception:
            pass

    if ctx["prev_losses"] == 0:
        await _msg("🕶 Добро пожаловать в реальный мир")
    if ctx["loss_streak"] == 3:
        await _msg("💪 Надо собраться")
    if loser.rating < 1000.0 and ctx["loser_total"] >= NEWCOMER_THRESHOLD and old_loser_rating >= 1000.0:
        await _msg("🕳 Добро пожаловать на дно")

    total = ctx["loser_total"]
    if total in range(25, 501, 25):
        milestone = (
            f"🤯 {total}-й матч. Дальше уже считать бессмысленно."
            if total == 500
            else f"🎯 Юбилей! Это твой <b>{total}-й</b> матч!"
        )
        await _msg(milestone)


async def _send_time_based_eggs(bot: Bot, players: list[Player], completed_at: datetime) -> None:
    """Пасхалки по времени завершения матча (ночь / выходной / вечер пятницы) —
    обоим участникам. Взаимоисключающие (приоритет сверху вниз), чтобы на один
    матч не сыпалось сразу несколько сообщений об одном и том же факте времени."""
    if completed_at is None:
        return
    hour, weekday = msk_hour_and_weekday(completed_at)
    if 0 <= hour < 6:
        text = "🌙 Тебе точно не спится?"
    elif weekday >= 5:
        text = "Вышел на работу ради тенниса? Уважаемо! 🫡"
    elif weekday == 4 and hour >= 18:
        text = "Закрываем неделю красиво 🍻"
    else:
        return
    for p in players:
        try:
            await bot.send_message(p.telegram_id, text)
        except Exception:
            pass


async def _send_h2h_milestone_egg(bot: Bot, session: AsyncSession, p1: Player, p2: Player) -> None:
    """Пасхалка на круглую цифру личных встреч между этой парой (эта — включительно)."""
    r = await session.execute(
        select(func.count()).select_from(Match).where(
            Match.status == MatchStatus.completed,
            or_(
                and_(Match.challenger_id == p1.id, Match.challenged_id == p2.id),
                and_(Match.challenger_id == p2.id, Match.challenged_id == p1.id),
            ),
        )
    )
    total = r.scalar()
    if total not in (10, 25, 50, 100):
        return
    text = f"🎉 Юбилейная битва — {total}-я встреча между вами!"
    for p in (p1, p2):
        try:
            await bot.send_message(p.telegram_id, text)
        except Exception:
            pass


async def _send_quick_rematch_egg(
    bot: Bot, p1: Player, p2: Player, created_at: datetime | None, h2h_matches: list[Match],
) -> None:
    """Пасхалка: та же пара сыграла повторно в течение 10 минут после предыдущего матча.

    Сравнивает СТАРТ этого матча (created_at) с ОКОНЧАНИЕМ предыдущего —
    та же семантика, что у ачивки no_rest_win (achievements.py). Раньше тут
    ошибочно использовался completed_at этого матча — из-за этого пасхалка
    могла не сработать для честного быстрого реванша, если сам матч-реванш
    оказался долгим (гэп мерился от конца затянувшегося матча, а не от его
    старта сразу после предыдущего).

    h2h_matches — завершённые матчи между p1/p2 ДО текущего (desc completed_at),
    из общего bot.utils.get_h2h_matches() — раньше запрашивались здесь же отдельно.
    """
    if created_at is None:
        return
    prev = h2h_matches[0] if h2h_matches else None
    if prev is None or not prev.completed_at:
        return
    gap = (created_at - prev.completed_at).total_seconds()
    if not (0 <= gap <= 600):
        return
    for p in (p1, p2):
        try:
            await bot.send_message(p.telegram_id, "Не наигрался? 😤")
        except Exception:
            pass


async def _send_easter_eggs(
    bot: Bot,
    session: AsyncSession,
    winner: Player,
    loser: Player,
    old_winner_rating: float,
    old_loser_rating: float,
    final_sets: list[dict],
    match_id: int,
    h2h_matches: list[Match],
    completed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> None:
    """Пасхалки после матча — победителю, проигравшему и обоим."""
    ctx = await _collect_egg_context(
        session, winner, loser, final_sets, match_id, old_winner_rating, old_loser_rating, h2h_matches,
    )
    await _send_winner_eggs(bot, winner, loser, ctx)
    await _send_loser_eggs(bot, loser, winner, ctx, old_loser_rating)

    # ── Обоим игрокам ─────────────────────────────────────────────────────────
    if ctx["marathon"]:
        for p in (winner, loser):
            try:
                await bot.send_message(p.telegram_id, "🕰 Три часа спустя…")
            except Exception:
                pass

    today_start = msk_day_start()
    for p in (winner, loser):
        today_r = await session.execute(
            select(func.count()).select_from(Match).where(
                or_(Match.challenger_id == p.id, Match.challenged_id == p.id),
                Match.status == MatchStatus.completed,
                Match.completed_at >= today_start,
            )
        )
        if today_r.scalar() == 7:
            try:
                await bot.send_message(p.telegram_id, "7 матчей за сегодня! А поработать не хочешь? 😄")
            except Exception:
                pass

    if completed_at is not None:
        await _send_time_based_eggs(bot, [winner, loser], completed_at)
        await _send_h2h_milestone_egg(bot, session, winner, loser)
    if created_at is not None:
        await _send_quick_rematch_egg(bot, winner, loser, created_at, h2h_matches)


def _restart_notice_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🎮 Мои матчи", callback_data="menu_matches"))
    return b.as_markup()


def _confirm_kb(match_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Всё верно", callback_data=f"confirm_{match_id}"),
        InlineKeyboardButton(text="✏️ Исправить", callback_data=f"redo_{match_id}"),
    )
    b.row(InlineKeyboardButton(text="✖ Отменить", callback_data="cancel_report"))
    return b.as_markup()


# ── Cancel ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "cancel_report")
async def cancel_report(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("Отменено.", reply_markup=back_to_menu_kb())


# ── Сброс FSM рестартом бота ──────────────────────────────────────────────────
# MemoryStorage теряет все состояния при рестарте (в т.ч. после автодеплоя).
# Без этого фолбэка нажатие кнопки шага ввода/подтверждения при пустом состоянии
# просто не находит хендлер — колбэк не отвечен, спиннер висит ~15с до "query is
# too old". Регистрируем ПОСЛЕ специфичных хендлеров тех же callback_data: они
# требуют конкретное состояние и не пересекаются с StateFilter(None) на одном апдейте.

@router.callback_query(
    F.data.startswith(("finish_sets_", "undo_set_", "redo_", "confirm_")),
    StateFilter(None),
)
async def fsm_reset_notice(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "⚠️ Бот перезапускался, ввод результата сбросился.\n\n"
        "Начни заново через «Внести результат» в 🎮 <b>Мои матчи</b>.",
        reply_markup=_restart_notice_kb(),
    )


# ── Step 1: "Я победил" ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("report_"))
async def start_report(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    try:
        match_id = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    r = await session.execute(select(Match).where(Match.id == match_id))
    match = r.scalar_one_or_none()

    if not match or match.status != MatchStatus.accepted:
        await callback.answer("Матч не найден или уже завершён.", show_alert=True)
        return

    player = await get_player(session, callback.from_user.id)
    if not player or player.id not in (match.challenger_id, match.challenged_id):
        await callback.answer("Это не твой матч.", show_alert=True)
        return

    await state.clear()
    await state.set_state(MatchResultStates.entering_set_score)
    await state.update_data(
        match_id=match_id,
        reporter_player_id=player.id,
        sets_data=[],
        fsm_chat_id=callback.message.chat.id,
        fsm_bot_message_id=callback.message.message_id,
    )

    await callback.message.edit_text(
        "🏓 <b>Вносим результат</b>\n\n"
        "Введи счёт <b>партии 1</b> — <b>твои:соперника</b>\n"
        "Например: <code>11:7</code>\n"
        "<i>Или сразу все партии: <code>11:7 9:11 11:8</code></i>",
        reply_markup=after_set_kb(match_id, has_sets=False),
    )
    await callback.answer()


# ── Вспомогательная функция: текст прогресса ввода партий ────────────────────

def _sets_progress_text(sets_data: list) -> str:
    """Форматирует текущее состояние ввода партий."""
    next_set_num = len(sets_data) + 1
    if sets_data:
        lines = []
        for i, s in enumerate(sets_data, 1):
            icon = "✅" if s["reporter"] > s["opponent"] else "❌"
            lines.append(f"  Партия {i}: {s['reporter']}:{s['opponent']} {icon}")
        sets_block = "\n".join(lines)

        my_sets = sum(1 for s in sets_data if s["reporter"] > s["opponent"])
        opp_sets = sum(1 for s in sets_data if s["opponent"] > s["reporter"])
        if my_sets > opp_sets:
            score_line = f"Счёт: <b>ты ведёшь {my_sets}–{opp_sets}</b>"
        elif opp_sets > my_sets:
            score_line = f"Счёт: <b>соперник ведёт {opp_sets}–{my_sets}</b>"
        else:
            score_line = f"Счёт: <b>{my_sets}–{opp_sets}</b>"

        return (
            f"🏓 <b>Вносим результат</b>\n\n"
            f"Партии:\n{sets_block}\n"
            f"{score_line}\n\n"
            f"Введи счёт <b>партии {next_set_num}</b> — <b>твои:соперника</b>\n"
            f"Например: <code>11:7</code>"
        )
    return (
        "🏓 <b>Вносим результат</b>\n\n"
        "Введи счёт <b>партии 1</b> — <b>твои:соперника</b>\n"
        "Например: <code>11:7</code>\n"
        "<i>Или сразу все партии: <code>11:7 9:11 11:8</code></i>"
    )


# ── Завершить ввод партий ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("finish_sets_"), MatchResultStates.entering_set_score)
async def finish_sets(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    sets_data: list = data["sets_data"]
    match_id: int = data["match_id"]

    if not sets_data:
        await callback.answer("Нет ни одной партии!", show_alert=True)
        return

    r = await session.execute(select(Match).where(Match.id == match_id))
    match = r.scalar_one_or_none()
    if not match or match.status != MatchStatus.accepted:
        await state.clear()
        await callback.message.edit_text(
            "⚠️ Матч уже завершён или отменён — возможно, соперник внёс результат раньше тебя.",
            reply_markup=main_menu_kb(),
        )
        await callback.answer()
        return

    reporter_sets_won = sum(1 for s in sets_data if s["reporter"] > s["opponent"])
    opponent_sets_won = sum(1 for s in sets_data if s["opponent"] > s["reporter"])
    is_draw = reporter_sets_won == opponent_sets_won

    if is_draw and match.is_boss_fight:
        await callback.answer(
            "⚔️ В босс-файте не может быть ничьей — играйте решающую партию. "
            "В конце останется только один.",
            show_alert=True,
        )
        return

    sets_preview = "  ".join(f"{s['reporter']}:{s['opponent']}" for s in sets_data)

    if is_draw:
        summary = f"🤝 <b>Ничья</b> — {reporter_sets_won}:{opponent_sets_won} по партиям"
    elif reporter_sets_won > opponent_sets_won:
        summary = f"🏆 Ты победил — {reporter_sets_won}:{opponent_sets_won} по партиям"
    else:
        summary = (
            f"😔 Ты проиграл — {reporter_sets_won}:{opponent_sets_won} по партиям\n"
            f"<i>(Результат будет записан корректно)</i>"
        )

    await state.update_data(is_draw=is_draw)
    await state.set_state(MatchResultStates.confirming)

    await callback.message.edit_text(
        f"📋 <b>Проверь результат:</b>\n\n"
        f"Счёт партий: <b>{sets_preview}</b>\n"
        f"{summary}\n\n"
        f"Всё верно?",
        reply_markup=_confirm_kb(match_id),
    )
    await callback.answer()


# ── Убрать последнюю партию ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("undo_set_"), MatchResultStates.entering_set_score)
async def undo_set(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sets_data: list = data["sets_data"]
    match_id: int = data["match_id"]

    if sets_data:
        sets_data.pop()
    await state.update_data(sets_data=sets_data)

    await callback.message.edit_text(
        _sets_progress_text(sets_data),
        reply_markup=after_set_kb(match_id, has_sets=bool(sets_data)),
    )
    await callback.answer()


# ── Прямой ввод счёта без нажатия кнопки ─────────────────────────────────────

@router.message(
    StateFilter(None),
    F.text.regexp(r'^\d+[:\-]\d+(\s+\d+[:\-]\d+)*$'),
)
async def handle_direct_score(message: Message, session: AsyncSession, state: FSMContext):
    """Игрок пишет счёт напрямую — автоматически стартует FSM для активного матча.

    Срабатывает только вне FSM (StateFilter(None)) — на экране подтверждения
    счёт текстом игнорируется, чтобы случайно не сбросить ввод.
    """
    player = await get_player(session, message.from_user.id)
    if not player:
        return

    active_r = await session.execute(
        select(Match).where(
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
            Match.status == MatchStatus.accepted,
        )
        .order_by(desc(Match.accepted_at))
    )
    active = active_r.scalars().all()
    if not active:
        return  # нет активного матча — молча игнорируем
    if len(active) > 1:
        # Неоднозначно — у игрока несколько активных матчей
        await message.answer(
            "У тебя несколько активных матчей. "
            "Выбери нужный через 🎮 <b>Мои матчи</b> → «📋 Внести результат».",
            reply_markup=main_menu_kb(),
        )
        return
    match = active[0]

    await state.clear()
    await state.set_state(MatchResultStates.entering_set_score)
    await state.update_data(
        match_id=match.id,
        reporter_player_id=player.id,
        sets_data=[],
        fsm_chat_id=message.chat.id,
        fsm_bot_message_id=None,
    )
    await process_set_score(message, state)


# ── Step 2: ввод счёта очередной партии ──────────────────────────────────────

@router.message(MatchResultStates.entering_set_score)
async def process_set_score(message: Message, state: FSMContext):
    data = await state.get_data()
    sets_data: list = data["sets_data"]
    match_id: int = data["match_id"]

    if not message.text:
        await message.answer("Введи счёт текстом, например <code>11:7</code>:")
        return

    # Принимаем и двоеточие, и дефис как разделитель: 11:7 или 11-7
    tokens = message.text.strip().replace("-", ":").split()

    # ── Пакетный ввод: несколько счётов через пробел ("11:7 9:11 11:8") ──────
    if len(tokens) > 1:
        if len(sets_data) + len(tokens) > MAX_SETS:
            await message.answer(
                f"⚠️ Максимум {MAX_SETS} партий в матче.",
            )
            return
        new_sets = []
        for token in tokens:
            if ":" not in token:
                await message.answer(
                    f"⚠️ Не могу прочитать <code>{token}</code>.\n"
                    f"Формат: <code>11:7 9:11 11:8</code>",
                )
                return
            try:
                my_s, op_s = map(int, token.split(":", 1))
            except ValueError:
                await message.answer(
                    f"⚠️ Только цифры. Проблема в <code>{token}</code>",
                )
                return
            err = validate_set_score(my_s, op_s)
            if err == "negative":
                await message.answer(f"⚠️ Отрицательный счёт: <code>{token}</code>")
                return
            if err == "draw":
                await message.answer(f"⚠️ В партии не может быть ничьей: <code>{token}</code>")
                return
            if err == "invalid":
                await message.answer(
                    f"⚠️ Некорректный счёт <code>{token}</code>\n"
                    f"Партия — до 11 с отрывом ≥2 (дьюс: 12:10, 13:11…)",
                )
                return
            new_sets.append({"reporter": my_s, "opponent": op_s})

        sets_data.extend(new_sets)
        sent = await message.answer(
            _sets_progress_text(sets_data),
            reply_markup=after_set_kb(match_id, has_sets=True),
        )
        await state.update_data(
            sets_data=sets_data,
            fsm_chat_id=message.chat.id,
            fsm_bot_message_id=sent.message_id,
        )
        return

    # ── Одиночный счёт ────────────────────────────────────────────────────────
    raw = tokens[0].replace(" ", "")
    if ":" not in raw:
        await message.answer(
            "Неверный формат. Введи счёт через двоеточие, например <code>11:7</code>:",
        )
        return

    try:
        my_score, opp_score = map(int, raw.split(":", 1))
    except ValueError:
        await message.answer("Только цифры, например <code>11:7</code>:")
        return

    error = validate_set_score(my_score, opp_score)
    if error == "negative":
        await message.answer("Счёт не может быть отрицательным.")
        return
    if error == "draw":
        await message.answer("В партии не может быть ничьей. Введи счёт ещё раз:")
        return
    if error == "invalid":
        await message.answer(
            "⚠️ Некорректный счёт партии.\n\n"
            "Партия играется до <b>11 очков</b> с отрывом ≥2.\n"
            "При дьюсе: <code>12:10</code>, <code>13:11</code> и т.д.\n\n"
            "Введи счёт ещё раз:",
        )
        return

    if len(sets_data) >= MAX_SETS:
        await message.answer(f"⚠️ Максимум {MAX_SETS} партий в матче.")
        return

    sets_data.append({"reporter": my_score, "opponent": opp_score})
    sent = await message.answer(
        _sets_progress_text(sets_data),
        reply_markup=after_set_kb(match_id, has_sets=True),
    )
    await state.update_data(
        sets_data=sets_data,
        fsm_chat_id=message.chat.id,
        fsm_bot_message_id=sent.message_id,
    )


# ── Step 4: подтверждение ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("redo_"), MatchResultStates.confirming)
async def redo_result(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    match_id = data["match_id"]

    await state.update_data(sets_data=[])
    await state.set_state(MatchResultStates.entering_set_score)

    await callback.message.edit_text(
        "🔄 Начинаем заново.\n\n"
        "Введи счёт <b>партии 1</b> — <b>твои:соперника</b>\n"
        "Например: <code>11:7</code>",
        reply_markup=after_set_kb(match_id, has_sets=False),
    )
    await callback.answer()


# ── confirm_result — извлечённые под-блоки ────────────────────────────────────
# Разбито на именованные корутины (не меняя поведения) — сама confirm_result
# разрослась до ~480 строк одним куском (ветка ничьей + победы + трон + ачивки +
# пасхалки), каждое добавление было маленьким и оправданным само по себе, но
# сумма уже плохо читалась. Расчёт рейтинга и мутация Match/Player намеренно
# остаются прямо в confirm_result — самая чувствительная часть, трогать её
# лишний раз не нужно; извлечены только более «листовые» блоки уведомлений/
# ачивок/пасхалок.

async def _handle_boss_fight_outcome(
    session: AsyncSession, bot: Bot, match: Match,
    challenger: Player, challenged: Player, winner: Player, loser: Player,
) -> None:
    """Боссфайт: перенос трона + клубное уведомление об исходе + ачивки защиты/поражения претендента.

    is_champion берём с challenger/challenged ДО этого блока — рейтинги уже
    обновлены и закоммичены выше (в confirm_result), но флаг трона ещё не
    тронут, поэтому тут он всё ещё отражает истинное положение на момент
    старта этого матча.
    """
    champion_role = challenger if challenger.is_champion else (
        challenged if challenged.is_champion else None
    )
    bf_result_text = None
    if champion_role is not None:
        if winner.id != champion_role.id:
            # CAS: если трон уже сменился где-то ещё (гонка с авто-освобождением
            # трона, scheduler.py) — не додаём его насильно поверх; исход этого
            # конкретного боссфайта в плане трона устарел, рейтинг уже применён.
            if await try_transfer_champion(
                session, champion_role.id, winner.id, at=match.completed_at
            ):
                await session.commit()
                bf_result_text = f"👑 <b>Новый чемпион — {h(winner.display_name)}!</b>"
        else:
            bf_result_text = (
                f"🛡 <b>Трон удержан!</b> {h(winner.display_name)} остаётся чемпионом."
            )
            new_ach_defense = await check_boss_fight_defense_achievement(winner)
            if new_ach_defense:
                await session.commit()
                await _notify_achievements(bot, winner, new_ach_defense)
            # loser здесь по построению — проигравший претендент (winner
            # уже подтверждён как champion_role в этой ветке "трон удержан")
            new_ach_denied = await check_boss_fight_challenger_defeat_achievement(loser)
            if new_ach_denied:
                await session.commit()
                await _notify_achievements(bot, loser, new_ach_denied)
    if bf_result_text is not None:
        await notify_all_players(bot, session, bf_result_text)


async def _award_draw_achievements_and_eggs(
    session: AsyncSession, bot: Bot, challenger: Player, challenged: Player,
    final_sets: list, match: Match, match_id: int,
) -> None:
    """Достижения обоих участников ничьей + пасхалки (Договорнячок / марафон /
    7 матчей за день / время / H2H-юбилей / быстрый реванш)."""
    new_ch_ach = await check_draw_achievements(session, challenger, final_sets, is_challenger=True)
    new_cd_ach = await check_draw_achievements(session, challenged, final_sets, is_challenger=False)
    await session.commit()
    await _notify_achievements(bot, challenger, new_ch_ach)
    await _notify_achievements(bot, challenged, new_cd_ach)

    # Пасхалка — ничья
    for p in (challenger, challenged):
        try:
            await bot.send_message(p.telegram_id, "🤝 Договорнячок")
        except Exception:
            pass

    # Пасхалка — марафон (5+ партий) при ничье
    marathon = len(final_sets) >= 5
    if marathon:
        for p in (challenger, challenged):
            try:
                await bot.send_message(p.telegram_id, "🕰 Три часа спустя…")
            except Exception:
                pass

    # Пасхалка — 7 матчей за день (ничья)
    today_start = msk_day_start()
    for p in (challenger, challenged):
        today_count_r = await session.execute(
            select(func.count()).select_from(Match).where(
                or_(Match.challenger_id == p.id, Match.challenged_id == p.id),
                Match.status == MatchStatus.completed,
                Match.completed_at >= today_start,
            )
        )
        if today_count_r.scalar() == 7:
            try:
                await bot.send_message(p.telegram_id, "7 матчей за сегодня! А поработать не хочешь? 😄")
            except Exception:
                pass

    await _send_time_based_eggs(bot, [challenger, challenged], match.completed_at)
    await _send_h2h_milestone_egg(bot, session, challenger, challenged)
    h2h_matches = await get_h2h_matches(session, challenger.id, challenged.id, exclude_match_id=match_id)
    await _send_quick_rematch_egg(bot, challenger, challenged, match.created_at, h2h_matches)

    for p in (challenger, challenged):
        personal_records = await check_personal_records_on_draw(session, p, match)
        await _send_personal_records(bot, p, personal_records)


async def _award_win_achievements_and_eggs(
    session: AsyncSession, bot: Bot, winner: Player, loser: Player,
    final_sets: list, match: Match, match_id: int,
    old_winner_rating: float, old_loser_rating: float,
    winner_db_id: int, loser_db_id: int,
) -> None:
    """Достижения победителя/проигравшего, пасхалки после победы, уведомление
    о серии побед над одним соперником, кратной 10.

    h2h-история между winner/loser запрашивается один раз и переиспользуется
    в check_win_achievements (revenge/no_rest_win), пасхалках (_collect_egg_context,
    _send_quick_rematch_egg) и ниже для серии побед подряд — раньше эти 4 места
    независимо запрашивали одну и ту же историю по 4 отдельных запроса.
    """
    h2h_matches = await get_h2h_matches(session, winner.id, loser.id, exclude_match_id=match_id)

    new_ach_winner = await check_win_achievements(
        session, winner, loser, match, old_winner_rating, old_loser_rating, h2h_matches,
    )
    new_ach_loser = await check_loss_achievements(session, loser, final_sets)
    await session.commit()
    await _notify_achievements(bot, winner, new_ach_winner)
    await _notify_achievements(bot, loser, new_ach_loser)

    await _send_easter_eggs(
        bot, session, winner, loser, old_winner_rating, old_loser_rating, final_sets, match_id,
        h2h_matches, completed_at=match.completed_at, created_at=match.created_at,
    )

    personal_records_winner = await check_personal_records_on_win(session, winner, match)
    await _send_personal_records(bot, winner, personal_records_winner)
    personal_records_loser = await check_personal_records_on_loss(session, loser, match)
    await _send_personal_records(bot, loser, personal_records_loser)

    # Проверка серии побед над одним соперником — текущий матч (всегда победа
    # winner_db_id) + трейлинг серия побед в уже загруженном h2h_matches.
    consecutive = 1
    for m in h2h_matches:
        if m.winner_id == winner_db_id:
            consecutive += 1
        else:
            break

    if consecutive % 10 == 0:
        try:
            await bot.send_message(
                winner.telegram_id,
                f"💀 <b>То что мертво — умереть не может.</b>\n\n"
                f"Ты победил <b>{h(loser.display_name)}</b> уже {consecutive} раз подряд.\n"
                f"Попробуй выбрать ещё какого-нибудь соперника 😏",
            )
        except Exception:
            pass


async def _notify_challenger_status_change(
    session: AsyncSession, bot: Bot, match: Match,
    challenger_before: Player | None, challenger_before_id: int | None,
) -> None:
    """Уведомления о смене претендента после матча — «обошёл чемпиона» и «Просран
    шанс» / «ПОТРАЧЕНО». Только если личность претендента РЕАЛЬНО изменилась
    (антиспам) — иначе каждый обычный матч претендента слал бы повторные уведомления.
    """
    champion_after = await get_champion(session)
    challenger_after = await get_challenger(session, champion_after)
    challenger_after_id = challenger_after.id if challenger_after else None
    if challenger_after_id is not None and challenger_after_id != challenger_before_id:
        try:
            await bot.send_message(
                challenger_after.telegram_id,
                "⚔️ <b>Ты обошёл чемпиона по очкам!</b>\n"
                "Чтобы занять 1-е место, победи его в босс-файте.",
            )
        except Exception:
            pass
        try:
            await bot.send_message(
                champion_after.telegram_id,
                f"⚔️ Тебя догнал по очкам <b>{h(challenger_after.display_name)}</b> — "
                f"он может вызвать тебя на босс-файт.",
            )
        except Exception:
            pass

    # ── Претендент потерял статус, не дойдя до боссфайта — «Просран шанс» ──────
    # Не для боссфайтов: поражение НЕПОСРЕДСТВЕННО в боссфайте уже даёт
    # throne_denied в отдельной ветке выше — не дублируем два уведомления
    # на одно и то же событие. Независимый if (не elif к блоку выше) — оба
    # случая могут сработать за один матч: старый претендент проиграл, а
    # победитель этим же результатом обогнал чемпиона и стал новым претендентом.
    #
    # Осознанно ловит НЕ ТОЛЬКО «чемпион обогнал обратно» и «претендент
    # проиграл» — переиспользует challenger_before/after, который уже
    # пересчитывается после КАЖДОГО завершённого матча в клубе (это фича
    # уведомления «ты обошёл чемпиона» чуть выше). Раз дорогая часть
    # (пересчёт get_challenger) уже оплачена, честный обгон претендента
    # ТРЕТЬЕЙ стороной (двое посторонних сыграли между собой) тоже ловится —
    # без этого пришлось бы искусственно резать до двух случаев без всякой
    # экономии на запросах. См. test_chance_blown_also_fires_on_third_party_overtake.
    if (
        not match.is_boss_fight
        and challenger_before_id is not None
        and challenger_before_id != challenger_after_id
    ):
        new_ach_blown = await check_chance_blown_achievement(challenger_before)
        if new_ach_blown:
            await session.commit()
            await _notify_achievements(bot, challenger_before, new_ach_blown)
        try:
            await bot.send_message(
                challenger_before.telegram_id,
                "💸 <b>ПОТРАЧЕНО.</b>\n\n"
                "Твой шанс вызвать чемпиона на босс-файт только что испарился — "
                "ты выпал из претендентов, не дойдя до боссфайта.",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("confirm_"), MatchResultStates.confirming)
async def confirm_result(callback: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot):
    try:
        match_id = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    data = await state.get_data()
    sets_data: list = data["sets_data"]
    reporter_player_id: int = data["reporter_player_id"]
    is_draw: bool = data.get("is_draw", False)

    # ── Страховка от ничьей в босс-файте ──────────────────────────────────────
    # Основная блокировка — в finish_sets(); это защита на случай, если её
    # обошли (например, состояние FSM пережило рестарт бота). Проверяем ДО
    # CAS-guard — матч ещё не тронут.
    if is_draw:
        bf_r = await session.execute(select(Match.is_boss_fight).where(Match.id == match_id))
        if bf_r.scalar_one_or_none():
            await callback.answer(
                "⚔️ В босс-файте не может быть ничьей — играйте решающую партию. "
                "В конце останется только один.",
                show_alert=True,
            )
            return

    # ── Атомарный guard от двойной обработки (двойной тап «Всё верно») ──────────
    # CAS: переводим матч accepted → completed одним UPDATE. Если изменено 0 строк —
    # значит другой параллельный обработчик (или быстрый второй тап) уже завершил матч.
    # Сервер (aiosqlite, timeout 5с) сериализует двух писателей: второй ждёт коммита
    # первого и видит уже completed. winner_id/sets_data/rating_change проставляются
    # ниже в этой же транзакции и коммитятся вместе со статусом — промежуточного
    # «битого» состояния (completed без данных) не возникает.
    guard = await session.execute(
        update(Match)
        .where(Match.id == match_id, Match.status == MatchStatus.accepted)
        .values(status=MatchStatus.completed)
    )
    if guard.rowcount == 0:
        await callback.message.edit_text("Матч уже завершён или не найден.", reply_markup=main_menu_kb())
        await state.clear()
        await callback.answer()
        return

    r = await session.execute(select(Match).where(Match.id == match_id))
    match = r.scalar_one()

    rc = await session.execute(select(Player).where(Player.id == match.challenger_id))
    rd = await session.execute(select(Player).where(Player.id == match.challenged_id))
    challenger = rc.scalar_one()
    challenged = rd.scalar_one()

    old_challenger_rating = challenger.rating
    old_challenged_rating = challenged.rating

    # ── Снапшот претендента ДО матча — для анти-спам-сравнения после коммита ───
    champion_snapshot = await get_champion(session)
    challenger_before = await get_challenger(session, champion_snapshot)
    challenger_before_id = challenger_before.id if challenger_before else None

    if is_draw:
        # Нормализуем sets_data в challenger-перспективу для корректного хранения в БД.
        # В stats display используется s["w"]=challenger_score, s["l"]=challenged_score.
        final_sets = [{"w": s["reporter"], "l": s["opponent"]} for s in sets_data]
        sets_str = ", ".join(f"{s['w']}:{s['l']}" for s in final_sets)
        if reporter_player_id != match.challenger_id:
            final_sets = [{"w": s["l"], "l": s["w"]} for s in final_sets]
            sets_str = ", ".join(f"{s['w']}:{s['l']}" for s in final_sets)

        # Полы для ничьей — определяем по кол-ву матчей каждого игрока.
        # Match.id != match_id: CAS-guard выше уже перевёл текущий матч в completed,
        # без исключения он попал бы в подсчёт и сдвинул порог новичок/ветеран на 1.
        ch_count_r = await session.execute(
            select(func.count()).select_from(Match).where(
                or_(Match.challenger_id == challenger.id, Match.challenged_id == challenger.id),
                Match.status == MatchStatus.completed,
                Match.id != match_id,
            )
        )
        cd_count_r = await session.execute(
            select(func.count()).select_from(Match).where(
                or_(Match.challenger_id == challenged.id, Match.challenged_id == challenged.id),
                Match.status == MatchStatus.completed,
                Match.id != match_id,
            )
        )
        challenger_floor = NEWCOMER_FLOOR if ch_count_r.scalar() < NEWCOMER_THRESHOLD else VETERAN_FLOOR
        challenged_floor = NEWCOMER_FLOOR if cd_count_r.scalar() < NEWCOMER_THRESHOLD else VETERAN_FLOOR

        # ELO-ничья: challenger_delta может быть положительным или отрицательным
        challenger_delta = calculate_draw_rating_change(challenger.rating, challenged.rating)
        challenged_delta = -challenger_delta

        new_challenger_rating = round(max(challenger_floor, challenger.rating + challenger_delta), 1)
        new_challenged_rating = round(max(challenged_floor, challenged.rating + challenged_delta), 1)

        # Реальные дельты с учётом динамического пола
        actual_challenger_delta = round(new_challenger_rating - old_challenger_rating, 1)
        actual_challenged_delta = round(new_challenged_rating - old_challenged_rating, 1)

        challenger.rating = new_challenger_rating
        challenged.rating = new_challenged_rating

        # Пик рейтинга обновляем и при ничьей: андердог может вырасти и побить рекорд
        if challenger.peak_rating is None or challenger.rating > challenger.peak_rating:
            challenger.peak_rating = challenger.rating
        if challenged.peak_rating is None or challenged.rating > challenged.peak_rating:
            challenged.peak_rating = challenged.rating

        match.status = MatchStatus.completed
        match.winner_id = None          # ничья
        match.sets_data = final_sets
        match.rating_change = challenger_delta  # знаковый: + или -
        match.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        await session.commit()
        await state.clear()

        # Счёт для репортёра — его очки первыми
        if reporter_player_id == match.challenger_id:
            reporter_sets_str = sets_str
        else:
            reporter_sets_str = ", ".join(f"{s['l']}:{s['w']}" for s in final_sets)

        result_text = (
            f"🤝 <b>Ничья!</b>\n\n"
            f"<b>{h(challenger.display_name)}</b> vs <b>{h(challenged.display_name)}</b>\n"
            f"Счёт партий: {reporter_sets_str}\n\n"
            f"📊 Изменение рейтинга:\n"
            f"  {h(challenger.display_name)}: {round(old_challenger_rating, 1)} → "
            f"<b>{round(challenger.rating, 1)}</b> ({_fmt_delta(actual_challenger_delta)})\n"
            f"  {h(challenged.display_name)}: {round(old_challenged_rating, 1)} → "
            f"<b>{round(challenged.rating, 1)}</b> ({_fmt_delta(actual_challenged_delta)})"
        )
        draw_opponent_id = challenged.id if reporter_player_id == match.challenger_id else challenger.id
        await callback.message.edit_text(result_text, reply_markup=rematch_kb(draw_opponent_id))

        # Уведомляем второго участника (того, кто не вносил результат)
        notify_player = challenged if reporter_player_id == match.challenger_id else challenger
        notify_actual_delta = actual_challenged_delta if reporter_player_id == match.challenger_id else actual_challenger_delta
        notify_old = old_challenged_rating if reporter_player_id == match.challenger_id else old_challenger_rating
        opponent_name = challenger.display_name if notify_player.id == challenged.id else challenged.display_name

        # Счёт с перспективы notify_player: его очки первыми
        if notify_player.id == challenged.id:
            notify_sets_str = ", ".join(f"{s['l']}:{s['w']}" for s in final_sets)
        else:
            notify_sets_str = sets_str

        try:
            await bot.send_message(
                notify_player.telegram_id,
                f"📋 <b>Результат матча внесён</b>\n\n"
                f"🤝 Ничья с <b>{h(opponent_name)}</b>\n"
                f"Счёт партий: {notify_sets_str}\n\n"
                f"Твой рейтинг: {round(notify_old, 1)} → <b>{round(notify_player.rating, 1)}</b> ({_fmt_delta(notify_actual_delta)})",
                reply_markup=main_menu_kb(),
            )
        except Exception:
            pass

        # Достижения обоих участников + пасхалки ничьей
        await _award_draw_achievements_and_eggs(
            session, bot, challenger, challenged, final_sets, match, match_id,
        )

    else:
        # Определяем победителя с учётом инверсии (reporter мог проиграть)
        reporter_sets_won = sum(1 for s in sets_data if s["reporter"] > s["opponent"])
        opponent_sets_won = sum(1 for s in sets_data if s["opponent"] > s["reporter"])

        if reporter_sets_won >= opponent_sets_won:
            # reporter выиграл — классический случай
            winner_db_id = reporter_player_id
            final_sets = [{"w": s["reporter"], "l": s["opponent"]} for s in sets_data]
        else:
            # reporter проиграл — инвертируем перспективу
            winner_db_id = (
                match.challenged_id if reporter_player_id == match.challenger_id
                else match.challenger_id
            )
            final_sets = [{"w": s["opponent"], "l": s["reporter"]} for s in sets_data]

        sets_str = ", ".join(f"{s['w']}:{s['l']}" for s in final_sets)

        loser_db_id = (
            match.challenged_id if winner_db_id == match.challenger_id else match.challenger_id
        )
        winner = challenger if winner_db_id == challenger.id else challenged
        loser = challenged if winner_db_id == challenger.id else challenger
        old_winner_rating = winner.rating
        old_loser_rating = loser.rating

        # ── Все матчи победителя ДО текущего (для стрика и кол-ва матчей) ──────
        # Match.id != match_id: текущий матч уже completed после CAS-guard —
        # иначе порог бонуса новичка и repeat-стрик завышались бы на 1.
        winner_prev_r = await session.execute(
            select(Match)
            .where(
                or_(Match.challenger_id == winner.id, Match.challenged_id == winner.id),
                Match.status == MatchStatus.completed,
                Match.id != match_id,
            )
            .order_by(desc(Match.completed_at))
        )
        winner_prev = winner_prev_r.scalars().all()

        winner_match_count = len(winner_prev)

        # Стрик: сколько последних матчей победителя подряд были против этого же соперника
        prev_streak = 0
        for m in winner_prev:
            opp_id = m.challenged_id if m.challenger_id == winner.id else m.challenger_id
            if opp_id == loser.id:
                prev_streak += 1
            else:
                break

        # Кол-во матчей проигравшего до текущего (для определения пола)
        loser_count_r = await session.execute(
            select(func.count()).select_from(Match).where(
                or_(Match.challenger_id == loser.id, Match.challenged_id == loser.id),
                Match.status == MatchStatus.completed,
                Match.id != match_id,
            )
        )
        loser_match_count = loser_count_r.scalar()

        # ── Множители ─────────────────────────────────────────────────────────
        loser_floor = NEWCOMER_FLOOR if loser_match_count < NEWCOMER_THRESHOLD else VETERAN_FLOOR
        newcomer_bonus = NEWCOMER_BONUS if winner_match_count < NEWCOMER_THRESHOLD else 1.0
        # Стрик 0 (первый матч vs этого соперника) → ×1.0; стрик 1 → ×0.95; и т.д.
        # Формула: max(0.5, 1.0 - 0.05 × streak). Минимум 50% вместо прежних 10%.
        # В босс-файте repeat_multiplier не применяется — это разовое событие
        # за трон, а не фарм одного и того же слабого соперника.
        repeat_multiplier = 1.0 if match.is_boss_fight else max(REPEAT_MIN, 1.0 - 0.05 * prev_streak)
        boss_fight_multiplier = BOSS_FIGHT_MULT if match.is_boss_fight else 1.0

        # ── Расчёт дельты с множителями ───────────────────────────────────────
        delta = calculate_rating_change(winner.rating, loser.rating, final_sets)
        delta = round(delta * newcomer_bonus * repeat_multiplier * boss_fight_multiplier, 1)

        winner.rating = round(winner.rating + delta, 1)
        loser.rating = round(max(loser_floor, loser.rating - delta), 1)
        if winner.peak_rating is None or winner.rating > winner.peak_rating:
            winner.peak_rating = winner.rating

        match.status = MatchStatus.completed
        match.winner_id = winner_db_id
        match.sets_data = final_sets
        match.rating_change = delta
        match.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        await session.commit()
        await state.clear()

        # ── Босс-файт: перенос трона + клубное уведомление об исходе + ачивки ──
        if match.is_boss_fight:
            await _handle_boss_fight_outcome(session, bot, match, challenger, challenged, winner, loser)

        actual_loser_delta = round(old_loser_rating - loser.rating, 1)
        loser_delta_str = f"-{actual_loser_delta}" if actual_loser_delta > 0 else "0.0"
        result_text = (
            f"🏆 <b>Матч завершён!</b>\n\n"
            f"<b>{h(winner.display_name)}</b> победил <b>{h(loser.display_name)}</b>\n"
            f"Счёт партий: {sets_str}\n\n"
            f"📊 Изменение рейтинга:\n"
            f"  {h(winner.display_name)}: {round(old_winner_rating, 1)} → <b>{round(winner.rating, 1)}</b> (+{delta})\n"
            f"  {h(loser.display_name)}: {round(old_loser_rating, 1)} → <b>{round(loser.rating, 1)}</b> ({loser_delta_str})"
        )
        reporter_opponent_id = loser_db_id if reporter_player_id == winner_db_id else winner_db_id
        reporter_is_winner = reporter_player_id == winner_db_id
        await callback.message.edit_text(
            result_text,
            reply_markup=rematch_kb(
                reporter_opponent_id,
                can_rematch=not match.is_boss_fight,
                share_match_id=match_id if reporter_is_winner else None,
            ),
        )

        if reporter_is_winner:
            # Репортёр — победитель: уведомляем проигравшего (карточка победы
            # ему не положена — кнопка только на экране самого победителя выше)
            try:
                await bot.send_message(
                    loser.telegram_id,
                    f"📋 <b>Результат матча внесён</b>\n\n"
                    f"<b>{h(winner.display_name)}</b> победил тебя\n"
                    f"Счёт партий: {sets_str}\n\n"
                    f"Твой рейтинг: {round(old_loser_rating, 1)} → <b>{round(loser.rating, 1)}</b> ({loser_delta_str})",
                    reply_markup=main_menu_kb(),
                )
            except Exception:
                pass
        else:
            # Репортёр — проигравший (инверсия): уведомляем победителя. У него
            # нет интерактивного экрана результата (тот достался репортёру) —
            # кнопку карточки победы прицепляем прямо к этому уведомлению.
            try:
                await bot.send_message(
                    winner.telegram_id,
                    f"📋 <b>Результат матча внесён</b>\n\n"
                    f"Ты победил <b>{h(loser.display_name)}</b>\n"
                    f"Счёт партий: {sets_str}\n\n"
                    f"Твой рейтинг: {round(old_winner_rating, 1)} → <b>{round(winner.rating, 1)}</b> (+{delta})",
                    reply_markup=main_menu_kb(share_match_id=match_id),
                )
            except Exception:
                pass

        # Достижения победителя/проигравшего, пасхалки, серия 10x подряд
        await _award_win_achievements_and_eggs(
            session, bot, winner, loser, final_sets, match, match_id,
            old_winner_rating, old_loser_rating, winner_db_id, loser_db_id,
        )

    # Смена претендента — «обошёл чемпиона» / «Просран шанс» / «ПОТРАЧЕНО»
    await _notify_challenger_status_change(session, bot, match, challenger_before, challenger_before_id)

    await callback.answer()


# ── Карточка «поделиться победой» ────────────────────────────────────────────
# Кнопка на уведомлении о результате — только у победителя (см. confirm_result
# выше, share_match_id прицепляется к обоим местам, где победитель может
# оказаться: его собственный интерактивный экран ИЛИ плоское уведомление,
# если счёт внёс проигравший). По тапу — отдельное сообщение, не трогает
# исходный экран/уведомление. Только для побед, не для ничьих.

@router.callback_query(F.data.startswith("share_card_"))
async def send_share_card(callback: CallbackQuery, session: AsyncSession):
    # Ровно один callback.answer() на путь выполнения — Telegram не даёт
    # ответить на один и тот же callback дважды, второй вызов до пользователя
    # не долетит (алерт «не твоя карточка» иначе никогда бы не показался).
    try:
        match_id = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    r = await session.execute(select(Match).where(Match.id == match_id))
    match = r.scalar_one_or_none()
    if not match or match.status != MatchStatus.completed or match.winner_id is None:
        await callback.answer("Матч не найден.", show_alert=True)
        return

    viewer = await get_player(session, callback.from_user.id)
    if not viewer or viewer.id != match.winner_id:
        await callback.answer("Эта карточка не твоя.", show_alert=True)
        return

    await callback.answer()

    loser_id = match.challenged_id if match.winner_id == match.challenger_id else match.challenger_id
    wr = await session.execute(select(Player).where(Player.id == match.winner_id))
    lr = await session.execute(select(Player).where(Player.id == loser_id))
    winner = wr.scalar_one()
    loser = lr.scalar_one()

    # sets_data для побед уже хранится в перспективе победителя — форматируем как есть.
    sets_str = ", ".join(f"{s['w']}:{s['l']}" for s in (match.sets_data or []))
    delta = match.rating_change or 0.0
    report = match_report(match, winner.display_name)

    card_text = (
        f"🏆 <b>ПОБЕДА</b>\n\n"
        f"<b>{h(winner.display_name)}</b> обыграл <b>{h(loser.display_name)}</b>\n"
        f"{sets_str}\n\n"
        f"📈 +{delta} pts → <b>{round(winner.rating, 1)}</b> pts\n\n"
        f"<i>{report}</i>"
    )
    try:
        await callback.message.answer(card_text)
    except Exception:
        pass
