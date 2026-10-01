"""Экран «Рассылки» (v2.145.0) — тихий режим: игрок отключает отдельные автосводки.

Личные уведомления (вызовы, результаты матчей, трон), голосование за звания
года и админская рассылка «Что нового» отключению не подлежат — см.
bot/services/digests.py.
"""
from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Player
from bot.keyboards.inline import notifications_kb
from bot.services.digests import muted_digests, toggle_digest
from bot.utils import cb_data, cb_msg, get_player

router = Router()

NOTIFICATIONS_TEXT = (
    "🔔 <b>Рассылки</b>\n\n"
    "Нажми на строку, чтобы отключить 🔕 или снова включить ✅ автосводку.\n\n"
    "<i>Личные уведомления (вызовы, результаты матчей, трон) и голосование за "
    "звания года отключить нельзя.</i>"
)


def _screen(player: Player):
    return NOTIFICATIONS_TEXT, notifications_kb(muted_digests(player))


@router.callback_query(F.data == "menu_notifications")
async def show_notifications(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    text, kb = _screen(player)
    await callback.answer()
    await cb_msg(callback).edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("notif_toggle_"))
async def toggle_notification(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    kind = cb_data(callback).removeprefix("notif_toggle_")
    if toggle_digest(player, kind) is None:
        await callback.answer("Такой рассылки нет.", show_alert=True)
        return
    await session.commit()
    text, kb = _screen(player)
    await callback.answer()
    await cb_msg(callback).edit_text(text, reply_markup=kb)
