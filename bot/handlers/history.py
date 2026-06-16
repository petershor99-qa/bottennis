from datetime import datetime, timezone
from html import escape as h

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Match, MatchStatus, Player
from bot.keyboards.inline import (
    back_to_menu_kb,
    h2h_kb,
    history_kb,
    player_history_kb,
    player_profile_kb,
    rating_history_kb,
)
from bot.utils import (
    _match_line,
    build_rating_series,
    compute_h2h,
    get_player,
    get_rec_signal,
    match_rating_delta,
    rating_chart_url,
)

router = Router()

PAGE_SIZE = 20


# ── История рейтинга ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "rating_history")
async def show_rating_history(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return

    r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
            Match.status == MatchStatus.completed,
            Match.rating_change.isnot(None),
        )
        .order_by(desc(Match.completed_at))
        .limit(20)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    matches = r.scalars().all()

    if not matches:
        await callback.message.edit_text(
            "У тебя пока нет сыгранных матчей. 🏓",
            reply_markup=rating_history_kb(),
        )
        await callback.answer()
        return

    lines = [f"📈 <b>История рейтинга</b>  <i>(последние {len(matches)} матчей)</i>\n"]
    lines.append(f"Сейчас: <b>{round(player.rating, 1)} pts</b>\n")

    for m in matches:
        opponent = m.challenged if m.challenger_id == player.id else m.challenger
        is_draw = m.winner_id is None
        won = m.winner_id == player.id
        icon = "🤝" if is_draw else ("✅" if won else "❌")
        date_str = m.completed_at.strftime("%d.%m") if m.completed_at else ""
        delta = match_rating_delta(m, player.id)
        sign = "+" if delta > 0 else ""
        delta_str = f"{sign}{round(delta, 1)}"
        lines.append(f"{icon} {delta_str}  {date_str}  vs {h(opponent.display_name)}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=rating_history_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ── График рейтинга ───────────────────────────────────────────────────────────

# Последнее отправленное сообщение-график на чат — чтобы удалять предыдущее при
# построении нового. В памяти (как FSM): сбрасывается при рестарте, это норм.
_last_chart_msg: dict[int, int] = {}


async def _send_rating_chart(
    target: Player, session: AsyncSession, callback: CallbackQuery, bot: Bot
) -> None:
    """Строит и отправляет график динамики рейтинга для указанного игрока."""
    r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == target.id, Match.challenged_id == target.id),
            Match.status == MatchStatus.completed,
            Match.rating_change.isnot(None),
        )
        .order_by(Match.completed_at)  # старые первыми — для ряда слева направо
    )
    matches = r.scalars().all()

    if len(matches) < 2:
        await callback.answer("Нужно минимум 2 матча для графика 🏓", show_alert=True)
        return

    labels, values = build_rating_series(matches, target.id, target.rating)
    url = rating_chart_url(target.display_name, labels, values)
    chat_id = callback.message.chat.id

    # Удаляем предыдущий график в этом чате, чтобы они не копились в переписке.
    prev_id = _last_chart_msg.get(chat_id)
    if prev_id is not None:
        try:
            await bot.delete_message(chat_id, prev_id)
        except Exception:
            pass

    # Картинку скачивает сам Telegram по URL. Исходное текстовое сообщение с
    # навигацией не трогаем — график приходит отдельным сообщением ниже.
    try:
        sent = await bot.send_photo(
            chat_id,
            url,
            caption=(
                f"📊 Динамика рейтинга — <b>{h(target.display_name)}</b>\n"
                f"Сейчас: <b>{round(target.rating, 1)} pts</b>  "
                f"<i>(последние {len(values)} матчей)</i>"
            ),
            parse_mode="HTML",
        )
        _last_chart_msg[chat_id] = sent.message_id
        await callback.answer()
    except Exception:
        await callback.answer("Не удалось построить график, попробуй позже 🙁", show_alert=True)


@router.callback_query(F.data == "rating_chart")
async def show_rating_chart(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    await _send_rating_chart(player, session, callback, bot)


@router.callback_query(F.data.startswith("player_chart_"))
async def show_player_rating_chart(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    try:
        target_id = int(callback.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    tp_r = await session.execute(select(Player).where(Player.id == target_id))
    target = tp_r.scalar_one_or_none()
    if not target:
        await callback.answer("Игрок не найден.", show_alert=True)
        return
    await _send_rating_chart(target, session, callback, bot)


# ── Полная история матчей (своя) ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("history_"))
async def show_match_history(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return

    try:
        page = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
            Match.status == MatchStatus.completed,
        )
        .order_by(desc(Match.completed_at))
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    all_matches = r.scalars().all()

    if not all_matches:
        await callback.message.edit_text(
            "У тебя пока нет сыгранных матчей. 🏓",
            reply_markup=back_to_menu_kb(),
        )
        await callback.answer()
        return

    total_pages = max(1, (len(all_matches) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = all_matches[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [
        f"📜 <b>История матчей</b>  "
        f"<i>(стр. {page + 1}/{total_pages}, всего {len(all_matches)})</i>\n"
    ]
    for m in chunk:
        lines.append(_match_line(m, player.id))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=history_kb(page, total_pages),
        parse_mode="HTML",
    )
    await callback.answer()


# ── История матчей другого игрока ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("player_history_"))
async def show_player_match_history(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split("_")
    # format: player_history_{player_id}_{page}
    try:
        target_id = int(parts[2])
        page = int(parts[3])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    tp_r = await session.execute(select(Player).where(Player.id == target_id))
    player = tp_r.scalar_one_or_none()
    if not player:
        await callback.answer("Игрок не найден.", show_alert=True)
        return

    viewer = await get_player(session, callback.from_user.id)
    viewer_id = viewer.id if viewer else None

    r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
            Match.status == MatchStatus.completed,
        )
        .order_by(desc(Match.completed_at))
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    all_matches = r.scalars().all()

    if not all_matches:
        await callback.message.edit_text(
            f"У <b>{h(player.display_name)}</b> пока нет сыгранных матчей. 🏓",
            reply_markup=player_profile_kb(target_id, viewer_id=viewer_id),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    total_pages = max(1, (len(all_matches) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = all_matches[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [
        f"📜 <b>История матчей — {h(player.display_name)}</b>  "
        f"<i>(стр. {page + 1}/{total_pages}, всего {len(all_matches)})</i>\n"
    ]
    for m in chunk:
        lines.append(_match_line(m, player.id))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=player_history_kb(target_id, page, total_pages),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Личные встречи (H2H) ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("h2h_"))
async def show_h2h(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split("_")
    try:
        target_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    viewer = await get_player(session, callback.from_user.id)
    if not viewer:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return

    tp_r = await session.execute(select(Player).where(Player.id == target_id))
    opponent = tp_r.scalar_one_or_none()
    if not opponent:
        await callback.answer("Игрок не найден.", show_alert=True)
        return
    if opponent.id == viewer.id:
        await callback.answer("С собой не сыграешь 🙂", show_alert=True)
        return

    r = await session.execute(
        select(Match)
        .where(
            Match.status == MatchStatus.completed,
            or_(
                and_(Match.challenger_id == viewer.id, Match.challenged_id == opponent.id),
                and_(Match.challenger_id == opponent.id, Match.challenged_id == viewer.id),
            ),
        )
        .order_by(desc(Match.completed_at))
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    matches = r.scalars().all()

    title = f"⚔️ <b>Личные встречи</b>\nТы 🆚 <b>{h(opponent.display_name)}</b>\n"

    if not matches:
        await callback.message.edit_text(
            f"{title}\nВы ещё не встречались за столом 🏓",
            reply_markup=h2h_kb(target_id),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    s = compute_h2h(matches, viewer.id, opponent.id)

    draws_str = f"  (+{s['draws']} 🤝)" if s["draws"] else ""
    delta = s["rating_delta"]
    delta_sign = "+" if delta >= 0 else ""

    lines = [
        title,
        f"📊 Счёт встреч: <b>{s['wins']}–{s['losses']}</b>{draws_str}",
        f"🏓 По партиям: <b>{s['my_sets']}–{s['opp_sets']}</b>",
    ]
    if s["streak_desc"]:
        lines.append(f"🔥 Сейчас: <b>{s['streak_desc']}</b>")
    lines.append(f"💰 Рейтинг в противостоянии: <b>{delta_sign}{delta} pts</b>")
    if s["best_win"] is not None and s["best_win"] > 0:
        lines.append(f"🏅 Лучшая победа: <b>+{s['best_win']} pts</b>")
    if s["first_date"]:
        lines.append(f"🗓 Первая встреча: {s['first_date'].strftime('%d.%m')}")

    total_pages = max(1, (len(matches) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = matches[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    if total_pages > 1:
        lines.append(f"\n<b>Встречи</b> <i>(стр. {page + 1}/{total_pages}, всего {len(matches)}):</i>")
    else:
        lines.append("\n<b>Все встречи:</b>")
    for m in chunk:
        lines.append(_match_line(m, viewer.id))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=h2h_kb(target_id, page, total_pages),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Матчи клуба + рекомендации ────────────────────────────────────────────────

@router.callback_query(F.data == "menu_matches")
async def show_my_matches(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Все активные матчи клуба
    all_active_r = await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.accepted)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    all_active = all_active_r.scalars().all()

    my_matches = [m for m in all_active if player.id in (m.challenger_id, m.challenged_id)]
    busy_ids: set[int] = {pid for m in all_active for pid in (m.challenger_id, m.challenged_id)}

    # Все соперники
    others_r = await session.execute(
        select(Player).where(Player.id != player.id).order_by(desc(Player.rating))
    )
    opponents = others_r.scalars().all()

    # Завершённые матчи viewer'а для H2H-сигналов
    h2h_r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
            Match.status == MatchStatus.completed,
        )
        .order_by(desc(Match.completed_at))
    )
    my_completed = h2h_r.scalars().all()
    h2h_by_opp: dict[int, list] = {}
    for m in my_completed:
        opp_id = m.challenged_id if m.challenger_id == player.id else m.challenger_id
        h2h_by_opp.setdefault(opp_id, []).append(m)

    # Игроки, у которых есть хотя бы один сыгранный матч (для пометки новичков)
    played_r = await session.execute(
        select(Match.challenger_id, Match.challenged_id).where(
            Match.status == MatchStatus.completed
        )
    )
    played_ids: set[int] = set()
    for a, b in played_r.all():
        played_ids.add(a)
        played_ids.add(b)

    # ── Текст ─────────────────────────────────────────────────────────────────
    lines: list[str] = ["🏓 <b>Активные матчи клуба</b>\n"]

    if all_active:
        for m in all_active:
            since = m.accepted_at or m.created_at
            if since:
                total_h = int((now - since).total_seconds() / 3600)
                if total_h == 0:
                    time_str = "до 1ч"   # без '<' — иначе ломается HTML-парсинг Telegram
                elif total_h < 24:
                    time_str = f"{total_h}ч"
                else:
                    time_str = f"{total_h // 24}д {total_h % 24}ч"
                warn = "⚠ " if total_h >= 24 else ""
            else:
                time_str, warn = "?", ""
            lines.append(
                f"{warn}{h(m.challenger.display_name)} vs {h(m.challenged.display_name)} — {time_str}"
            )
    else:
        lines.append("Активных матчей нет")

    lines.append("\n🎯 <b>С кем сыграть?</b>\n")

    builder = InlineKeyboardBuilder()

    for m in my_matches:
        opp = m.challenged if m.challenger_id == player.id else m.challenger
        builder.row(InlineKeyboardButton(
            text=f"📋 Внести результат — vs {opp.display_name}",
            callback_data=f"report_{m.id}",
        ))
        builder.row(InlineKeyboardButton(
            text=f"❌ Отменить — vs {opp.display_name}",
            callback_data=f"cancel_match_{m.id}",
        ))

    for opp in opponents:
        h2h = h2h_by_opp.get(opp.id, [])
        signal = get_rec_signal(player.rating, player.id, opp.rating, opp.id, h2h, now)
        diff = opp.rating - player.rating
        diff_str = f"+{int(diff)}" if diff >= 0 else str(int(diff))

        if opp.id in busy_ids:
            lines.append(f"{h(opp.display_name)}  — сейчас играет 🔒  ({diff_str} pts)")
        else:
            if opp.id not in played_ids:
                signal_part = "  — 🆕 ещё не играл"
            else:
                signal_part = f"  — {signal}" if signal else ""
            lines.append(f"{h(opp.display_name)}{signal_part}  ({diff_str} pts)")
            builder.row(InlineKeyboardButton(
                text=f"Вызвать {opp.display_name}",
                callback_data=f"challenge_{opp.id}",
            ))

    builder.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()
