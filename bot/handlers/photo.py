"""Своё фото для Mini App (v2.174.0).

Telegram отдаёт боту аватар игрока, только если он не скрыт настройкой
приватности, — у большинства игроков клуба в приложении была буква. Игрок
присылает боту фото (`/photo`, кнопка «📷 Фото в приложении» на «Моём рейтинге»
или ссылка «Поставить фото» в своём профиле приложения), бот хранит его
`file_id` в `Player.photo_file_id`, приложение показывает его вместо аватара
Telegram (bot/webapp/photos.py).
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Player
from bot.states.states import PhotoStates
from bot.utils import cb_msg, get_player, msg_user
from bot.webapp.config import webapp_link
from bot.webapp.photos import pick_photo_size

router = Router()

PHOTO_ASK_TEXT = "📷 Пришли фото для своего профиля в приложении. Лучше портрет, где видно лицо."
PHOTO_DONE_TEXT = "Готово, фото стоит в профиле приложения."
PHOTO_NOT_PHOTO_TEXT = "Нужна именно фотография, не файл и не стикер. Пришли ещё раз или нажми «Отмена»."
PHOTO_REMOVED_TEXT = "Фото убрано. В приложении снова фото из Telegram или буква."
SCORE_RE = r'^\d+[:\-]\d+(\s+\d+[:\-]\d+)*$'


def _ask_kb(has_photo: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_photo:
        rows.append([InlineKeyboardButton(text="Убрать фото", callback_data="photo_remove")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="photo_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _done_kb(player: Player) -> InlineKeyboardMarkup | None:
    url = webapp_link(player.telegram_id, f"player/{player.id}")
    if url is None:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть профиль", web_app=WebAppInfo(url=url))],
    ])


async def ask_photo(message: Message, player: Player, state: FSMContext) -> None:
    """Начало: просьба прислать фото. Общая для /photo, /start photo и кнопки."""
    await state.set_state(PhotoStates.waiting)
    await message.answer(PHOTO_ASK_TEXT, reply_markup=_ask_kb(bool(player.photo_file_id)))


@router.message(Command("photo"))
async def cmd_photo(message: Message, session: AsyncSession, state: FSMContext):
    player = await get_player(session, msg_user(message).id)
    if not player:
        await message.answer("Сначала напиши /start")
        return
    await ask_photo(message, player, state)


@router.callback_query(F.data == "photo_start")
async def photo_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    await callback.answer()
    await ask_photo(cb_msg(callback), player, state)


@router.message(PhotoStates.waiting, F.photo)
async def got_photo(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    player = await get_player(session, msg_user(message).id)
    if not player:
        return
    player.photo_file_id = pick_photo_size(list(message.photo or []))
    await session.commit()
    await message.answer(PHOTO_DONE_TEXT, reply_markup=_done_kb(player))


@router.message(PhotoStates.waiting, F.text.regexp(SCORE_RE))
async def score_while_waiting(message: Message, session: AsyncSession, state: FSMContext):
    """Игрок забыл про фото и пишет счёт матча — счёт важнее: выходим из
    ожидания фото и передаём сообщение обычному вводу счёта."""
    from bot.handlers.match_result import handle_direct_score
    await state.clear()
    await handle_direct_score(message, session, state)


@router.message(PhotoStates.waiting)
async def not_a_photo(message: Message):
    await message.answer(PHOTO_NOT_PHOTO_TEXT, reply_markup=_ask_kb(False))


@router.callback_query(F.data == "photo_cancel")
async def photo_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    await cb_msg(callback).edit_reply_markup(reply_markup=None)


@router.callback_query(F.data == "photo_remove")
async def photo_remove(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    player.photo_file_id = None
    await session.commit()
    await callback.answer()
    await cb_msg(callback).edit_text(PHOTO_REMOVED_TEXT)
