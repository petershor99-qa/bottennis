import os
from datetime import datetime, timezone
from html import escape as h

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Player
from bot.keyboards.inline import (
    back_to_menu_kb,
    help_section_kb,
    help_toc_kb,
    main_menu_kb,
    main_reply_kb,
)
from bot.services.achievements import ACHIEVEMENTS_LIST
from bot.utils import (
    MSK_OFFSET,
    cb_data,
    cb_msg,
    compute_ranks,
    env_int,
    format_rank,
    get_active_match,
    get_match_counts,
    get_player,
    msg_user,
)

router = Router()

INVITE_CODE = os.getenv("INVITE_CODE", "")
ADMIN_ID = env_int("ADMIN_ID")


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, session: AsyncSession, state: FSMContext, bot: Bot):
    await state.clear()
    player = await get_player(session, msg_user(message).id)

    if player:
        if player.last_menu_message_id:
            try:
                await bot.delete_message(message.chat.id, player.last_menu_message_id)
            except Exception:
                pass

        players_all = (await session.execute(select(Player))).scalars().all()
        champion = next((p for p in players_all if p.is_champion), None)
        ranks = compute_ranks(
            players_all, await get_match_counts(session),
            champion_id=champion.id if champion else None,
        )
        active = await get_active_match(session, player.id)
        active_hint = (
            "\n\n⚔️ <b>Есть активный матч!</b> Просто напиши счёт сюда: <code>11:7 9:11</code>"
            if active else ""
        )
        sent = await message.answer(
            f"Привет, <b>{h(player.display_name)}</b>! 🏓\n"
            f"Рейтинг: <b>{round(player.rating, 1)}</b> pts — {format_rank(ranks, player.id)}"
            f"{active_hint}",
            reply_markup=main_menu_kb(),
        )
        player.last_menu_message_id = sent.message_id
        await session.commit()
        await message.answer("👇 Быстрый доступ", reply_markup=main_reply_kb())
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
        telegram_id=msg_user(message).id,
        username=msg_user(message).username,
        display_name=msg_user(message).full_name or msg_user(message).username or "Игрок",
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
    )
    player.last_menu_message_id = sent.message_id
    await session.commit()
    await message.answer("🎮 Choose your destiny")
    await message.answer("👇 Быстрый доступ", reply_markup=main_reply_kb())


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

# Как считается рейтинг (v2.146.0) — цифры строго из формулы (services/rating.py,
# CLAUDE.md «Рейтинговая формула»): при изменении формулы обновить и этот текст.
RATING_HELP_TEXT = (
    "<b>Рейтинг: модифицированный ELO</b>\n"
    "• Победа над более сильным соперником даёт больше очков, чем над слабым\n"
    "• Чем увереннее счёт по партиям и очкам, тем больше награда: 2:0 весит больше, чем 2:1\n"
    "• Новичкам (меньше 15 матчей) очки за победу ×1,2\n"
    "• Матч в одну партию весит на 25% меньше\n"
    "• Много побед подряд над одним и тем же соперником дают всё меньше очков (до половины)\n"
    "• Босс-файт за 1-е место: очки ×2\n"
    "• Рейтинг не опускается ниже 1000 у новичков и 900 у ветеранов"
)

HELP_INTRO = "🏓 <b>Справка bottennis</b>\n\nВыбери раздел:"

# Разделы справки (v2.152.0 — раньше одна длинная простыня): (ключ, подпись кнопки).
# Ключ — стабильная строка в callback_data (help_sec_{ключ}), как у рекордов и статистики.
HELP_SECTIONS: list[tuple[str, str]] = [
    ("play", "⚔️ Как играть"),
    ("rating", "🧮 Как считается рейтинг"),
    ("screens", "🗺 Карта экранов"),
    ("icons", "🔣 Значки и иконки"),
    ("digests", "⏰ Когда приходят сводки"),
    ("commands", "⌨️ Команды"),
]


def _help_section_text(key: str) -> str | None:
    """Текст одного раздела справки; None — раздела с таким ключом нет."""
    if key == "play":
        return (
            "⚔️ <b>Как играть</b>\n\n"
            "• ⚔️ Вызов соперника — матч начинается сразу, оба получают уведомление\n"
            "• 📋 Результат вносит любой участник: просто напиши счёт в чат "
            "(<code>11:7 9:11 11:5</code>), первым идёт твой счёт\n"
            "• 🤝 Поддерживается ничья\n"
            "• ❌ Отмена матча любым участником (с подтверждением)\n"
            "• ⚔️ Реванш — кнопка сразу после матча\n\n"
            "<b>Быстрый доступ:</b> кнопки под строкой ввода (🏓 Вызвать на матч / "
            "📊 Рейтинг / 📈 Статистика) — доступны всегда, не нужно открывать меню"
        )
    if key == "rating":
        return RATING_HELP_TEXT
    if key == "screens":
        return (
            "🗺 <b>Карта экранов</b>\n\n"
            "• 📊 Рейтинг — таблица с ▲▼ за неделю, винрейтом и сериями 🔥\n"
            "• 🏆 Рекорды клуба и ⚔️ Кто кого бьёт — кнопки на экране рейтинга\n"
            "• 📅 Сегодня в клубе — кто сколько сыграл за день и все матчи со счётом\n"
            "• 🎯 С кем сыграть? — активные матчи клуба и рекомендации соперников\n"
            "• 📈 Статистика — форма за 7 дней, серии, цель-ачивка, 📊 график рейтинга\n"
            "• 🆚 Личные встречи (H2H) — в профиле игрока\n"
            f"• 🏅 Достижения — {len(ACHIEVEMENTS_LIST)} ачивок с отсылками к играм и мемам"
        )
    if key == "icons":
        return (
            "🔣 <b>Значки и иконки</b>\n\n"
            "<b>В списках игроков:</b>\n"
            "• 💀 сильнее тебя на 120+ pts · 💪 сильнее на 35+ pts · ⚡ примерно равны\n"
            "• 😊 слабее на 35+ pts · 🤣 слабее тебя на 120+ pts\n\n"
            "<b>В рейтинге:</b>\n"
            "• 🔥 серия 3+ побед подряд · ❄️ не играл 7+ дней\n"
            "• 👑 текущий чемпион · 🗡 претендент на трон · 🌟 MVP месяца\n"
            "• ▲▼ — изменение места за неделю"
        )
    if key == "digests":
        return (
            "⏰ <b>Когда приходят сводки</b>\n\n"
            "• 📅 Итоги дня — каждый вечер в 21:30 МСК (топ дня + «матч дня»)\n"
            "• 📆 Итоги недели — понедельник в 9:00\n"
            "• 🗓 Итоги месяца — 1-го числа в 10:00\n"
            "• 📊 Итоги квартала — 1 января, апреля, июля и октября в 10:30\n"
            "• 🎆 Итоги года — 30 декабря в 12:00\n"
            "• 🗳 Голосование за неформальные звания года — с 21 по 30 декабря\n\n"
            "Любую из сводок можно отключить: кнопка «Настроить рассылки» в справке."
        )
    if key == "commands":
        return (
            "⌨️ <b>Команды</b>\n\n"
            "/start — главное меню\n"
            "/cancel — отменить текущее действие\n"
            "/help — эта справка\n"
            "/name — сменить имя в боте (например: /name Пётр)\n"
            "/feedback — отправить идею или баг напрямую разработчику"
        )
    return None


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_INTRO, reply_markup=help_toc_kb(HELP_SECTIONS))


@router.callback_query(F.data == "menu_help")
async def show_help_from_menu(callback: CallbackQuery):
    """Справка по кнопке «❓ Справка» главного меню (v2.148.0) и возврат «« К справке»
    из разделов (v2.152.0) — /help никто не открывал, поэтому вход из меню."""
    await callback.answer()
    await cb_msg(callback).edit_text(HELP_INTRO, reply_markup=help_toc_kb(HELP_SECTIONS))


@router.callback_query(F.data.startswith("help_sec_"))
async def show_help_section(callback: CallbackQuery):
    key = cb_data(callback).removeprefix("help_sec_")
    text = _help_section_text(key)
    if text is None:
        await callback.answer("Раздел не найден.", show_alert=True)
        return
    await callback.answer()
    await cb_msg(callback).edit_text(text, reply_markup=help_section_kb(key))


# ── /name ─────────────────────────────────────────────────────────────────────

NAME_MAX_LEN = 32


@router.message(Command("name"))
async def cmd_name(message: Message, command: CommandObject, session: AsyncSession):
    """Смена имени в боте (v2.148.0). display_name берётся из Телеграма один раз при
    регистрации и дальше не обновлялся — игрок, сменивший имя, оставался в рейтинге
    под старым. Имя экранируется при показе (h()), поэтому HTML в нём безвреден."""
    player = await get_player(session, msg_user(message).id)
    if not player:
        await message.answer("Сначала напиши /start")
        return
    new_name = " ".join((command.args or "").split())   # схлопываем пробелы/переводы строк
    if not new_name:
        await message.answer(
            "Напиши новое имя прямо в команде:\n<code>/name Пётр</code>"
        )
        return
    if len(new_name) > NAME_MAX_LEN:
        await message.answer(f"Имя слишком длинное — не больше {NAME_MAX_LEN} символов.")
        return
    # Одинаковые имена (без учёта регистра) сделали бы рейтинг, матрицу и логи
    # матчей неоднозначными — своё же имя в другом регистре менять можно.
    # Сравнение в Python, не в SQL: lower() в SQLite не понимает кириллицу.
    others = await session.execute(select(Player.display_name).where(Player.id != player.id))
    if any(n.casefold() == new_name.casefold() for (n,) in others.all()):
        await message.answer("Такое имя уже занято — выбери другое.")
        return
    player.display_name = new_name
    await session.commit()
    await message.answer(f"✅ Имя изменено: <b>{h(new_name)}</b>")


# ── /feedback ──────────────────────────────────────────────────────────────────

@router.message(Command("feedback"))
async def cmd_feedback(message: Message, command: CommandObject, bot: Bot) -> None:
    """Пересылает отзыв/идею/баг от ЛЮБОГО игрока админу с контекстом (кто, когда).

    В отличие от /fix_rating ниже — доступна всем, не только ADMIN_ID.
    """
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "Напиши отзыв прямо в команде:\n"
            "<code>/feedback хочу радар-диаграмму для личных встреч</code>",
        )
        return

    if not ADMIN_ID:
        await message.answer("⚠️ Обратная связь пока не настроена — сообщи разработчику лично.")
        return

    sender = message.from_user
    sender_name = sender.full_name or (f"@{sender.username}" if sender.username else str(sender.id))
    date_str = (datetime.now(timezone.utc) + MSK_OFFSET).strftime("%d.%m %H:%M")
    try:
        await bot.send_message(
            ADMIN_ID,
            f"💬 <b>Обратная связь</b>\n"
            f"От: <b>{h(sender_name)}</b> ({date_str} МСК)\n\n"
            f"{h(text)}",
        )
        await message.answer("Спасибо! Передал 🙏")
    except Exception:
        await message.answer("⚠️ Не получилось отправить — попробуй ещё раз позже.")


# ── /fix_rating (admin) ───────────────────────────────────────────────────────

@router.message(Command("fix_rating"))
async def cmd_fix_rating(message: Message, session: AsyncSession):
    """Ручная корректировка рейтинга. Только для ADMIN_ID.

    Использование: /fix_rating @username +18.3
    """
    if not ADMIN_ID or msg_user(message).id != ADMIN_ID:
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(
            "Использование: <code>/fix_rating @username +18.3</code>\n"
            "Пример: <code>/fix_rating @petya -15.0</code>",
        )
        return

    username = parts[1].lstrip("@")
    try:
        delta = round(float(parts[2]), 1)
    except ValueError:
        await message.answer(
            "Неверный формат дельты. Пример: <code>+18.3</code> или <code>-15.0</code>",
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
    )


# ── Navigation ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await cb_msg(callback).edit_text("Главное меню 🏓", reply_markup=main_menu_kb())
