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
    check_draw_achievements,
    check_loss_achievements,
    check_win_achievements,
)
from bot.services.rating import calculate_draw_rating_change, calculate_rating_change
from bot.services.validation import validate_set_score
from bot.states.states import MatchResultStates
from bot.utils import get_player

router = Router()

# ── Константы рейтинговой системы ─────────────────────────────────────────────
NEWCOMER_THRESHOLD = 15   # матчей — порог новичок / ветеран
NEWCOMER_FLOOR = 1000.0   # пол рейтинга для новичков (<15 матчей)
VETERAN_FLOOR = 900.0     # пол рейтинга для ветеранов (15+ матчей)
NEWCOMER_BONUS = 1.2      # бонус к победам новичка
REPEAT_MIN = 0.5          # минимальный множитель за повтор
MAX_SETS = 10             # максимальное число партий в матче


def _fmt_delta(d: float) -> str:
    """Форматирует дельту рейтинга: +8.5 или -3.2"""
    return f"+{d}" if d >= 0 else str(d)


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
        await bot.send_message(player.telegram_id, text, parse_mode="HTML")
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
) -> dict:
    """Собирает все данные для пасхалок из БД. Возвращает контекст."""

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
    h2h_cond = or_(
        and_(Match.challenger_id == winner.id, Match.challenged_id == loser.id),
        and_(Match.challenger_id == loser.id, Match.challenged_id == winner.id),
    )
    fb_r = await session.execute(
        select(func.count()).select_from(Match).where(
            Match.status == MatchStatus.completed,
            Match.id != match_id,
            Match.winner_id == winner.id,
            h2h_cond,
        )
    )
    first_blood = fb_r.scalar() == 0

    h2h_r = await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed, Match.id != match_id, h2h_cond)
        .order_by(desc(Match.completed_at))
        .limit(1)
    )
    last_h2h = h2h_r.scalar_one_or_none()
    revenge = last_h2h is not None and last_h2h.winner_id == loser.id

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
        await _msg(f"🩸 Первая кровь — <b>{h(loser.display_name)}</b>", parse_mode="HTML")
    if ctx["loss_streak_before"] >= 3:
        await _msg("💪 Вылез из жопы")

    total = ctx["winner_total"]
    if total in range(25, 501, 25):
        milestone = (
            f"🤯 {total}-й матч. Дальше уже считать бессмысленно."
            if total == 500
            else f"🎯 Юбилей! Это твой <b>{total}-й</b> матч!"
        )
        await _msg(milestone, parse_mode="HTML")


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
        await _msg(milestone, parse_mode="HTML")


async def _send_easter_eggs(
    bot: Bot,
    session: AsyncSession,
    winner: Player,
    loser: Player,
    old_winner_rating: float,
    old_loser_rating: float,
    final_sets: list[dict],
    match_id: int,
) -> None:
    """Пасхалки после матча — победителю, проигравшему и обоим."""
    ctx = await _collect_egg_context(
        session, winner, loser, final_sets, match_id, old_winner_rating, old_loser_rating
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

    today_start = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
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
    await state.clear()
    await callback.message.edit_text("Отменено.", reply_markup=back_to_menu_kb())
    await callback.answer()


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
        parse_mode="HTML",
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
            parse_mode="HTML",
        )
        await callback.answer()
        return

    reporter_sets_won = sum(1 for s in sets_data if s["reporter"] > s["opponent"])
    opponent_sets_won = sum(1 for s in sets_data if s["opponent"] > s["reporter"])
    is_draw = reporter_sets_won == opponent_sets_won

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
        parse_mode="HTML",
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
        parse_mode="HTML",
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
            parse_mode="HTML",
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
        await message.answer("Введи счёт текстом, например <code>11:7</code>:", parse_mode="HTML")
        return

    # Принимаем и двоеточие, и дефис как разделитель: 11:7 или 11-7
    tokens = message.text.strip().replace("-", ":").split()

    # ── Пакетный ввод: несколько счётов через пробел ("11:7 9:11 11:8") ──────
    if len(tokens) > 1:
        if len(sets_data) + len(tokens) > MAX_SETS:
            await message.answer(
                f"⚠️ Максимум {MAX_SETS} партий в матче.",
                parse_mode="HTML",
            )
            return
        new_sets = []
        for token in tokens:
            if ":" not in token:
                await message.answer(
                    f"⚠️ Не могу прочитать <code>{token}</code>.\n"
                    f"Формат: <code>11:7 9:11 11:8</code>",
                    parse_mode="HTML",
                )
                return
            try:
                my_s, op_s = map(int, token.split(":", 1))
            except ValueError:
                await message.answer(
                    f"⚠️ Только цифры. Проблема в <code>{token}</code>",
                    parse_mode="HTML",
                )
                return
            err = validate_set_score(my_s, op_s)
            if err == "negative":
                await message.answer(f"⚠️ Отрицательный счёт: <code>{token}</code>", parse_mode="HTML")
                return
            if err == "draw":
                await message.answer(f"⚠️ В партии не может быть ничьей: <code>{token}</code>", parse_mode="HTML")
                return
            if err == "invalid":
                await message.answer(
                    f"⚠️ Некорректный счёт <code>{token}</code>\n"
                    f"Партия — до 11 с отрывом ≥2 (дьюс: 12:10, 13:11…)",
                    parse_mode="HTML",
                )
                return
            new_sets.append({"reporter": my_s, "opponent": op_s})

        sets_data.extend(new_sets)
        sent = await message.answer(
            _sets_progress_text(sets_data),
            reply_markup=after_set_kb(match_id, has_sets=True),
            parse_mode="HTML",
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
            parse_mode="HTML",
        )
        return

    try:
        my_score, opp_score = map(int, raw.split(":", 1))
    except ValueError:
        await message.answer("Только цифры, например <code>11:7</code>:", parse_mode="HTML")
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
            parse_mode="HTML",
        )
        return

    if len(sets_data) >= MAX_SETS:
        await message.answer(f"⚠️ Максимум {MAX_SETS} партий в матче.")
        return

    sets_data.append({"reporter": my_score, "opponent": opp_score})
    sent = await message.answer(
        _sets_progress_text(sets_data),
        reply_markup=after_set_kb(match_id, has_sets=True),
        parse_mode="HTML",
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
        parse_mode="HTML",
    )
    await callback.answer()


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

    if is_draw:
        # Нормализуем sets_data в challenger-перспективу для корректного хранения в БД.
        # В stats display используется s["w"]=challenger_score, s["l"]=challenged_score.
        final_sets = [{"w": s["reporter"], "l": s["opponent"]} for s in sets_data]
        sets_str = ", ".join(f"{s['w']}:{s['l']}" for s in final_sets)
        if reporter_player_id != match.challenger_id:
            final_sets = [{"w": s["l"], "l": s["w"]} for s in final_sets]
            sets_str = ", ".join(f"{s['w']}:{s['l']}" for s in final_sets)

        # Полы для ничьей — определяем по кол-ву матчей каждого игрока
        ch_count_r = await session.execute(
            select(func.count()).select_from(Match).where(
                or_(Match.challenger_id == challenger.id, Match.challenged_id == challenger.id),
                Match.status == MatchStatus.completed,
            )
        )
        cd_count_r = await session.execute(
            select(func.count()).select_from(Match).where(
                or_(Match.challenger_id == challenged.id, Match.challenged_id == challenged.id),
                Match.status == MatchStatus.completed,
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
        await callback.message.edit_text(result_text, reply_markup=rematch_kb(draw_opponent_id), parse_mode="HTML")

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
                parse_mode="HTML",
            )
        except Exception:
            pass

        # Достижения — ничья
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
        today_start = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
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

        # ── Все матчи победителя до коммита (для стрика и кол-ва матчей) ──────
        winner_prev_r = await session.execute(
            select(Match)
            .where(
                or_(Match.challenger_id == winner.id, Match.challenged_id == winner.id),
                Match.status == MatchStatus.completed,
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

        # Кол-во матчей проигравшего (для определения пола)
        loser_count_r = await session.execute(
            select(func.count()).select_from(Match).where(
                or_(Match.challenger_id == loser.id, Match.challenged_id == loser.id),
                Match.status == MatchStatus.completed,
            )
        )
        loser_match_count = loser_count_r.scalar()

        # ── Множители ─────────────────────────────────────────────────────────
        loser_floor = NEWCOMER_FLOOR if loser_match_count < NEWCOMER_THRESHOLD else VETERAN_FLOOR
        newcomer_bonus = NEWCOMER_BONUS if winner_match_count < NEWCOMER_THRESHOLD else 1.0
        # Стрик 0 (первый матч vs этого соперника) → ×1.0; стрик 1 → ×0.95; и т.д.
        # Формула: max(0.5, 1.0 - 0.05 × streak). Минимум 50% вместо прежних 10%.
        repeat_multiplier = max(REPEAT_MIN, 1.0 - 0.05 * prev_streak)

        # ── Расчёт дельты с множителями ───────────────────────────────────────
        delta = calculate_rating_change(winner.rating, loser.rating, final_sets)
        delta = round(delta * newcomer_bonus * repeat_multiplier, 1)

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
        await callback.message.edit_text(result_text, reply_markup=rematch_kb(reporter_opponent_id), parse_mode="HTML")

        if reporter_player_id == winner_db_id:
            # Репортёр — победитель: уведомляем проигравшего
            try:
                await bot.send_message(
                    loser.telegram_id,
                    f"📋 <b>Результат матча внесён</b>\n\n"
                    f"<b>{h(winner.display_name)}</b> победил тебя\n"
                    f"Счёт партий: {sets_str}\n\n"
                    f"Твой рейтинг: {round(old_loser_rating, 1)} → <b>{round(loser.rating, 1)}</b> ({loser_delta_str})",
                    reply_markup=main_menu_kb(),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        else:
            # Репортёр — проигравший (инверсия): уведомляем победителя
            try:
                await bot.send_message(
                    winner.telegram_id,
                    f"📋 <b>Результат матча внесён</b>\n\n"
                    f"Ты победил <b>{h(loser.display_name)}</b>\n"
                    f"Счёт партий: {sets_str}\n\n"
                    f"Твой рейтинг: {round(old_winner_rating, 1)} → <b>{round(winner.rating, 1)}</b> (+{delta})",
                    reply_markup=main_menu_kb(),
                    parse_mode="HTML",
                )
            except Exception:
                pass

        # Достижения победителя и проигравшего
        new_ach_winner = await check_win_achievements(
            session, winner, loser, final_sets, match_id, old_winner_rating, old_loser_rating,
        )
        new_ach_loser = await check_loss_achievements(session, loser, final_sets)
        await session.commit()
        await _notify_achievements(bot, winner, new_ach_winner)
        await _notify_achievements(bot, loser, new_ach_loser)

        # Пасхалки после победы
        await _send_easter_eggs(
            bot, session, winner, loser, old_winner_rating, old_loser_rating, final_sets, match_id
        )

        # Проверка серии побед над одним соперником
        r_series = await session.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.completed,
                or_(
                    and_(Match.challenger_id == winner_db_id, Match.challenged_id == loser_db_id),
                    and_(Match.challenger_id == loser_db_id, Match.challenged_id == winner_db_id),
                ),
            )
            .order_by(desc(Match.completed_at))
        )
        series_matches = r_series.scalars().all()

        consecutive = 0
        for m in series_matches:
            if m.winner_id == winner_db_id:
                consecutive += 1
            else:
                break

        if consecutive > 0 and consecutive % 10 == 0:
            try:
                await bot.send_message(
                    winner.telegram_id,
                    f"💀 <b>То что мертво — умереть не может.</b>\n\n"
                    f"Ты победил <b>{h(loser.display_name)}</b> уже {consecutive} раз подряд.\n"
                    f"Попробуй выбрать ещё какого-нибудь соперника 😏",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await callback.answer()
