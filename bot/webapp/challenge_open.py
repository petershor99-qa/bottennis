"""
«Вызвать на матч» из приложения (v2.179.0).

Вызов по-прежнему делается в боте — приложение его не дублирует. Кнопка в
приложении просит бота прислать игроку в чат экран вызова: с выбранным
соперником — подтверждение «Вызвать … на матч?» с кнопкой, без соперника —
обычный экран «Кого вызвать?». Сообщение тихое: это ответ на действие самого
игрока, а не уведомление. Страница после этого закрывается, и игрок оказывается
в чате прямо у нужного экрана: два нажатия вместо «выйти из приложения → найти
клавиатуру → вызвать → выбрать».
"""
import logging
import time
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Player
from bot.handlers.challenge import _build_challenge_screen
from bot.utils import get_active_match

logger = logging.getLogger(__name__)

# Не чаще раза в несколько секунд — двойной тап не присылает два экрана
COOLDOWN_S = 5.0
_last_open: dict[int, float] = {}

CONFIRM_TEXT = "⚔️ Вызвать <b>{name}</b> на матч?"
YES_TEXT = "⚔️ Вызвать"
OTHER_TEXT = "👥 Выбрать другого"


def confirm_kb(opponent_id: int) -> InlineKeyboardMarkup:
    """«Вызвать» — тот же callback, что у экрана «Кого вызвать?» (все проверки —
    в send_challenge), «Выбрать другого» — сам этот экран."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=YES_TEXT, callback_data=f"challenge_{opponent_id}")],
        [InlineKeyboardButton(text=OTHER_TEXT, callback_data="menu_play")],
    ])


async def open_challenge(bot, session: AsyncSession, viewer: Player, opponent_id: int | None) -> str:
    """Прислать игроку экран вызова. ok | wait (только что присылали) | bad
    (нет такого соперника или это он сам) | fail (Telegram не принял)."""
    now = time.monotonic()
    if now - _last_open.get(viewer.telegram_id, -COOLDOWN_S) < COOLDOWN_S:
        return "wait"
    if opponent_id is not None:
        opponent = await session.get(Player, opponent_id)
        if opponent is None or opponent.id == viewer.id:
            return "bad"
    if opponent_id is not None and await get_active_match(session, viewer.id) is None:
        text, kb = CONFIRM_TEXT.format(name=escape(opponent.display_name)), confirm_kb(opponent.id)
    else:
        # Без соперника — обычный экран; занят матчем — тот же экран покажет его матч
        text, kb = await _build_challenge_screen(session, viewer.telegram_id)
    try:
        await bot.send_message(viewer.telegram_id, text, reply_markup=kb, disable_notification=True)
    except Exception:
        logger.warning("Не удалось прислать экран вызова игроку %s", viewer.telegram_id, exc_info=True)
        return "fail"
    _last_open[viewer.telegram_id] = now
    return "ok"
