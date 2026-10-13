"""
Доставка сводок с учётом Mini App (v2.168.0).

Сводки (итоги дня, недели, месяца, квартала, года) считаются как раньше в
bot/scheduler.py. Здесь только КАК они доходят до игрока:
- клубная часть сохраняется снимком (`DigestSnapshot`) — экран «Итоги» в
  приложении показывает её целиком, красиво и с анимациями;
- игроку, у которого есть приложение, бот присылает короткую версию — начало
  сводки до таблицы лидеров включительно (заголовок, сколько сыграно, топ) — и
  кнопку «📊 Итоги … в приложении»;
- без приложения (бот в полном режиме) — полный текст, как раньше.
"""
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import DigestSnapshot, Player
from bot.utils import safe_send
from bot.webapp.config import webapp_link

logger = logging.getLogger(__name__)

DIGEST_BUTTONS = {
    "day": "📊 Итоги дня в приложении",
    "week": "📊 Итоги недели в приложении",
    "month": "📊 Итоги месяца в приложении",
    "quarter": "📊 Итоги квартала в приложении",
    "year": "📊 Итоги года в приложении",
}
_RANKING_MARK = "🥇"


def short_part(text: str) -> str:
    """Начало сводки до первого абзаца с таблицей лидеров (в нём есть «🥇»)
    включительно. Если таблицы нет — первые два абзаца."""
    paragraphs = text.split("\n\n")
    for i, para in enumerate(paragraphs):
        if _RANKING_MARK in para:
            return "\n\n".join(paragraphs[: i + 1]).strip()
    return "\n\n".join(paragraphs[:2]).strip()


async def save_snapshot(session: AsyncSession, kind: str, club_text: str) -> None:
    """Сохранить клубную часть сводки. Сбой не мешает рассылке."""
    try:
        session.add(DigestSnapshot(kind=kind, text=club_text))
        await session.commit()
    except Exception:
        logger.warning("Не удалось сохранить снимок сводки %s", kind, exc_info=True)
        await session.rollback()


async def deliver_digest(bot: Bot, player: Player, kind: str, full_text: str, short_text: str) -> None:
    """Отправить сводку игроку: короткую с кнопкой в приложение или полную."""
    url = webapp_link(player.telegram_id, f"summary/{kind}")
    if url is None:
        await safe_send(bot, player.telegram_id, full_text)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=DIGEST_BUTTONS[kind], web_app=WebAppInfo(url=url)),
    ]])
    await safe_send(bot, player.telegram_id, short_text, reply_markup=kb)
