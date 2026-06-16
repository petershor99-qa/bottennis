import os
from html import escape as h

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Match, MatchStatus, Player
from bot.keyboards.inline import back_to_menu_kb, main_menu_kb
from bot.services.achievements import ACHIEVEMENTS_LIST
from bot.utils import env_int, get_player

router = Router()

INVITE_CODE = os.getenv("INVITE_CODE", "")
ADMIN_ID = env_int("ADMIN_ID")


async def _active_matches_for(session: AsyncSession, player: Player) -> list:
    """Возвращает [(match_id, opponent_name), ...] активных матчей игрока."""
    r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
            Match.status == MatchStatus.accepted,
        )
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    result = []
    for m in r.scalars().all():
        opponent = m.challenged if m.challenger_id == player.id else m.challenger
        result.append((m.id, opponent.display_name))
    return result


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, session: AsyncSession, state: FSMContext, bot: Bot):
    await state.clear()
    player = await get_player(session, message.from_user.id)

    if player:
        if player.last_menu_message_id:
            try:
                await bot.delete_message(message.chat.id, player.last_menu_message_id)
            except Exception:
                pass

        rank_r = await session.execute(
            select(func.count()).select_from(Player).where(Player.rating > player.rating)
        )
        rank = rank_r.scalar() + 1
        total_r = await session.execute(select(func.count()).select_from(Player))
        total = total_r.scalar()
        active = await _active_matches_for(session, player)
        active_hint = "\n\n⚔️ <b>Есть активный матч!</b> Внеси результат ниже 👇" if active else ""
        sent = await message.answer(
            f"Привет, <b>{h(player.display_name)}</b>! 🏓\n"
            f"Рейтинг: <b>{round(player.rating, 1)}</b> pts — #{rank} из {total}"
            f"{active_hint}",
            reply_markup=main_menu_kb(active_matches=active),
            parse_mode="HTML",
        )
        player.last_menu_message_id = sent.message_id
        await session.commit()
        return

    if INVITE_CODE:
        provided = (command.args or "").strip()
        if provided != INVITE_CODE:
            await message.answer(
                "⛔ Доступ только по пригласительной ссылке.\n"
                "Попроси администратора прислать ссылку."
            )
            return

    player = Player(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        display_name=message.from_user.full_name or message.from_user.username or "Игрок",
        rating=1000.0,
        peak_rating=1000.0,
    )
    session.add(player)
    await session.commit()
    sent = await message.answer(
        f"👋 Привет, <b>{h(player.display_name)}</b>!\n"
        f"Ты добавлен в список игроков с рейтингом <b>1000</b> pts. 🏓\n\n"
        f"Вызывай соперников и побеждай!",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
    player.last_menu_message_id = sent.message_id
    await session.commit()
    await message.answer("🎮 Choose your destiny")


# ── /cancel ───────────────────────────────────────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, bot: Bot):
    current_state = await state.get_state()
    data = await state.get_data()
    await state.clear()
    if current_state:
        fsm_msg_id = data.get("fsm_bot_message_id")
        fsm_chat_id = data.get("fsm_chat_id")
        if fsm_msg_id and fsm_chat_id:
            try:
                await bot.edit_message_text(
                    "✖ Ввод результата отменён.",
                    chat_id=fsm_chat_id,
                    message_id=fsm_msg_id,
                    reply_markup=back_to_menu_kb(),
                )
            except Exception:
                pass
        await message.answer("Действие отменено.", reply_markup=main_menu_kb())
    else:
        await message.answer("Нечего отменять. 🏓", reply_markup=main_menu_kb())


# ── /help ─────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🏓 <b>Справка bottennis</b>\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/cancel — отменить текущее действие\n"
        "/help — эта справка\n\n"
        "<b>Матчи:</b>\n"
        "• ⚔️ Вызов соперника — матч начинается сразу, оба получают уведомление\n"
        "• 📋 Результат вносит любой участник: пошагово или счётом прямо в чат "
        "(<code>11:7 9:11 11:5</code>)\n"
        "• 🤝 Поддерживается ничья\n"
        "• ❌ Отмена матча любым участником (с подтверждением)\n"
        "• ⚔️ Реванш — кнопка сразу после матча\n\n"
        "<b>Экраны:</b>\n"
        "• 📊 Рейтинг — таблица с ▲▼ за неделю, винрейтом и сериями 🔥\n"
        "• 🏆 Рекорды клуба и ⚔️ Матрица доминирования — кнопки на экране рейтинга\n"
        "• 📅 Сегодня — кто сколько сыграл за день\n"
        "• 🎮 Мои матчи — активные матчи клуба и «С кем сыграть?» с рекомендациями\n"
        "• 📈 Статистика — форма за 7 дней, серии, цель-ачивка, 📊 график рейтинга\n"
        "• 🆚 Личные встречи (H2H) — в профиле игрока\n"
        f"• 🏅 Достижения — {len(ACHIEVEMENTS_LIST)} ачивок с отсылками к играм и мемам\n\n"
        "<b>Автосообщения:</b>\n"
        "• ⏰ Матч не сыгран 24 часа — напоминание обоим\n"
        "• 📅 Итоги дня — каждый вечер в 21:30 МСК (топ дня + «матч дня»)\n"
        "• 📊 Итоги недели — понедельник 9:00, итоги месяца — 1-го числа в 10:00\n\n"
        "<b>Рейтинг:</b> модифицированный ELO.\n"
        "Чем слабее соперник — тем меньше очков за победу.\n"
        "Разгром в партиях даёт больше очков, чем победа 3:2.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


# ── /fix_rating (admin) ───────────────────────────────────────────────────────

@router.message(Command("fix_rating"))
async def cmd_fix_rating(message: Message, session: AsyncSession):
    """Ручная корректировка рейтинга. Только для ADMIN_ID.

    Использование: /fix_rating @username +18.3
    """
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(
            "Использование: <code>/fix_rating @username +18.3</code>\n"
            "Пример: <code>/fix_rating @petya -15.0</code>",
            parse_mode="HTML",
        )
        return

    username = parts[1].lstrip("@")
    try:
        delta = round(float(parts[2]), 1)
    except ValueError:
        await message.answer(
            "Неверный формат дельты. Пример: <code>+18.3</code> или <code>-15.0</code>",
            parse_mode="HTML",
        )
        return

    r = await session.execute(select(Player).where(Player.username == username))
    player = r.scalar_one_or_none()
    if not player:
        await message.answer(f"Игрок @{username} не найден. Проверь username.")
        return

    old_rating = player.rating
    new_rating = round(old_rating + delta, 1)
    player.rating = new_rating
    if player.peak_rating is None or new_rating > player.peak_rating:
        player.peak_rating = new_rating
    await session.commit()

    sign = "+" if delta >= 0 else ""
    await message.answer(
        f"✅ <b>Рейтинг скорректирован</b>\n\n"
        f"👤 {h(player.display_name)} (@{username})\n"
        f"📊 {old_rating} → <b>{new_rating}</b> pts  <i>({sign}{delta})</i>",
        parse_mode="HTML",
    )


# ── Navigation ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("Главное меню 🏓", reply_markup=main_menu_kb())
