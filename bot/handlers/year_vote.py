"""
Флоу голосования «Итоги года: неформальные звания» (v2.134.0, этап 3
дорожной карты) — кнопка из приглашения/напоминания открывает бюллетень,
дальше всё через edit_text одного сообщения: бюллетень → номинация → выбор
кандидата → назад к бюллетеню. Домен (номинации, окно, допуск, хранение,
рендер текста) — bot/services/year_vote.py.
"""
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Player
from bot.keyboards.inline import year_vote_bulletin_kb, year_vote_nomination_kb
from bot.services.year_vote import (
    get_eligible_player_ids,
    get_nomination,
    get_nominees,
    get_voter_choices,
    is_voting_open,
    render_bulletin,
    render_nomination_screen,
    set_vote,
)
from bot.utils import MSK_OFFSET, cb_data, cb_msg, get_player

router = Router()


async def _guard(
    callback: CallbackQuery, session: AsyncSession,
) -> tuple[Player, int, set[int]] | None:
    """(игрок, год, допущенные id года), если голосование сейчас открыто и
    игрок допущен — иначе сама отвечает алертом на callback и возвращает None.
    Допущенные id отдаются вызывающему, чтобы show_bulletin/show_nomination/
    pick_nominee не пересчитывали их ещё раз через get_nominees на каждый тап."""
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return None

    now_msk = datetime.now(timezone.utc).replace(tzinfo=None) + MSK_OFFSET
    if not is_voting_open(now_msk):
        await callback.answer(
            "Голосование уже закрыто. Увидимся в следующем декабре 👋", show_alert=True,
        )
        return None

    year = now_msk.year
    eligible_ids = await get_eligible_player_ids(session, year)
    if player.id not in eligible_ids:
        await callback.answer(
            "В этом году ты не сыграл ни одного матча — голосовать не получится.",
            show_alert=True,
        )
        return None

    return player, year, eligible_ids


@router.callback_query(F.data == "yv_open")
async def show_bulletin(callback: CallbackQuery, session: AsyncSession):
    guard = await _guard(callback, session)
    if guard is None:
        return
    player, year, eligible_ids = guard
    await callback.answer()

    candidates = await get_nominees(session, year, exclude_id=player.id, eligible_ids=eligible_ids)
    name_map = {p.id: p.display_name for p in candidates}
    choices = await get_voter_choices(session, year, player.id)
    text = render_bulletin(year, choices, name_map)
    await cb_msg(callback).edit_text(text, reply_markup=year_vote_bulletin_kb())


@router.callback_query(F.data.startswith("yv_nom_"))
async def show_nomination(callback: CallbackQuery, session: AsyncSession):
    guard = await _guard(callback, session)
    if guard is None:
        return
    player, year, eligible_ids = guard

    nomination_id = cb_data(callback).removeprefix("yv_nom_")
    nomination = get_nomination(nomination_id)
    if nomination is None:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    await callback.answer()

    candidates = await get_nominees(session, year, exclude_id=player.id, eligible_ids=eligible_ids)
    choices = await get_voter_choices(session, year, player.id)
    text = render_nomination_screen(nomination)
    await cb_msg(callback).edit_text(
        text,
        reply_markup=year_vote_nomination_kb(nomination.id, candidates, choices.get(nomination.id)),
    )


@router.callback_query(F.data.startswith("yv_pick_"))
async def pick_nominee(callback: CallbackQuery, session: AsyncSession):
    guard = await _guard(callback, session)
    if guard is None:
        return
    player, year, eligible_ids = guard

    raw = cb_data(callback).removeprefix("yv_pick_")
    try:
        nomination_id, nominee_id_str = raw.rsplit("_", 1)
        nominee_id = int(nominee_id_str)
    except ValueError:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    nomination = get_nomination(nomination_id)
    if nomination is None:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    candidates = await get_nominees(session, year, exclude_id=player.id, eligible_ids=eligible_ids)
    if nominee_id not in {p.id for p in candidates}:
        # За себя (нет своей кнопки) или устаревшая клавиатура — не записываем.
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    await set_vote(session, year, nomination.id, player.id, nominee_id)
    await callback.answer("Голос учтён ✅")

    name_map = {p.id: p.display_name for p in candidates}
    choices = await get_voter_choices(session, year, player.id)
    text = render_bulletin(year, choices, name_map)
    await cb_msg(callback).edit_text(text, reply_markup=year_vote_bulletin_kb())
