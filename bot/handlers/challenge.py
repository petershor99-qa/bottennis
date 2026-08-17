from datetime import datetime, timedelta, timezone
from html import escape as h

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, MatchStatus, Player
from bot.keyboards.inline import (
    active_match_kb,
    back_to_menu_kb,
    busy_with_match_kb,
    cancel_match_confirm_kb,
    main_menu_kb,
    players_list_kb,
)
from bot.services.achievements import ACHIEVEMENTS_MAP, check_cancel_achievements
from bot.services.rating import win_probability
from bot.utils import (
    boss_fight_rematch_blocked,
    compute_ranks,
    get_active_match,
    get_champion_and_challenger,
    get_player,
    match_phrase,
    notify_all_players,
)

router = Router()


# ── Show player list ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_play")
async def show_players_for_challenge(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

    current_player = await get_player(session, callback.from_user.id)

    # У игрока может быть только ОДИН активный матч одновременно (стол один,
    # матчи строго последовательные) — если он уже занят, вместо списка
    # соперников показываем его текущий матч с быстрым путём его завершить.
    if current_player:
        my_active = await get_active_match(session, current_player.id)
        if my_active:
            opp_id = (
                my_active.challenged_id if my_active.challenger_id == current_player.id
                else my_active.challenger_id
            )
            opp_r = await session.execute(select(Player).where(Player.id == opp_id))
            opp = opp_r.scalar_one()
            await callback.message.edit_text(
                f"⚔️ У тебя уже есть активный матч с <b>{h(opp.display_name)}</b>.\n"
                f"Заверши его, чтобы вызвать нового соперника.",
                reply_markup=busy_with_match_kb(my_active.id),
            )
            return

    r = await session.execute(select(Player).order_by(Player.rating.desc()))
    players = r.scalars().all()

    # Игроки, занятые активным матчем (с кем угодно) — не показываем их в списке
    busy_ids: set[int] = set()
    active_r = await session.execute(
        select(Match).where(Match.status == MatchStatus.accepted)
    )
    for m in active_r.scalars().all():
        busy_ids.add(m.challenger_id)
        busy_ids.add(m.challenged_id)

    others = [
        p for p in players
        if p.telegram_id != callback.from_user.id and p.id not in busy_ids
    ]
    if not others:
        any_others = any(p.telegram_id != callback.from_user.id for p in players)
        if any_others:
            msg = "Все игроки сейчас заняты активными матчами. Попробуй позже! 🏓"
        else:
            msg = "Пока нет других игроков. Позови друзей! 😅"
        await callback.message.edit_text(msg, reply_markup=back_to_menu_kb())
        return

    my_rating = current_player.rating if current_player else None

    # Серии побед для 🔥
    all_matches_r = await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed)
        .order_by(Match.completed_at.desc())
    )
    all_completed = all_matches_r.scalars().all()
    player_matches_map: dict[int, list] = {}
    for m in all_completed:
        for pid in (m.challenger_id, m.challenged_id):
            player_matches_map.setdefault(pid, []).append(m)
    streak_map: dict[int, int] = {}
    for pid, ms in player_matches_map.items():
        s = 0
        for m in ms:
            if m.winner_id == pid:
                s += 1
            else:
                break
        streak_map[pid] = s

    # Неактивные игроки (не играли 7+ дней) → ❄️, включая тех у кого 0 матчей
    week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    active_7day: set[int] = {
        pid
        for m in all_completed
        if m.completed_at and m.completed_at >= week_ago
        for pid in (m.challenger_id, m.challenged_id)
    }
    inactive_ids: set[int] = {p.id for p in players} - active_7day

    # Число матчей для сортировки: игроки с 0 матчей — в конец
    match_count_map: dict[int, int] = {}
    for m in all_completed:
        for pid in (m.challenger_id, m.challenged_id):
            match_count_map[pid] = match_count_map.get(pid, 0) + 1
    others = sorted(others, key=lambda p: (match_count_map.get(p.id, 0) == 0, -p.rating))

    champion, challenger_player = await get_champion_and_challenger(session)
    champion_id = champion.id if champion else None
    challenger_id = challenger_player.id if challenger_player else None

    # Ранг — единый для всех экранов: только среди игравших (как на лидерборде)
    rank_map = compute_ranks(players, match_count_map, champion_id=champion_id)

    if current_player and current_player.id in rank_map:
        my_rank = rank_map[current_player.id]
        header = (
            f"Кого хочешь вызвать? 🏓\n"
            f"Твой рейтинг: <b>{round(current_player.rating, 1)}</b> pts "
            f"(#{my_rank} из {len(rank_map)})"
        )
    else:
        header = "Кого хочешь вызвать на матч? 🏓"

    # Ярлык прямого вызова на босс-файт — только на экране самого претендента, и
    # только если пара реально может сыграть (не под блоком реванша после
    # прошлого боссфайта) — иначе тап вёл бы в тупик с алертом.
    boss_fight_target = None
    viewer_champion_blocked = False
    if champion is not None and current_player:
        viewer_champion_blocked = await boss_fight_rematch_blocked(
            session, current_player.id, champion.id
        )
        if current_player.id == challenger_id and not viewer_champion_blocked:
            boss_fight_target = (champion.id, champion.display_name)

    # Та же блокировка реванша прячет чемпиона и из обычного списка ниже —
    # иначе кнопка «Вызвать» на его строке вела бы в тот же тупик.
    if viewer_champion_blocked:
        others = [p for p in others if p.id != champion.id]

    await callback.message.edit_text(
        header,
        reply_markup=players_list_kb(
            others, callback.from_user.id,
            my_rating=my_rating, rank_map=rank_map,
            streak_map=streak_map, inactive_ids=inactive_ids,
            champion_id=champion_id, challenger_id=challenger_id,
            boss_fight_target=boss_fight_target,
        ),
    )


# ── Send challenge → match immediately active ─────────────────────────────────

@router.callback_query(
    F.data.startswith("challenge_") | F.data.startswith("rematch_")
)
async def send_challenge(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    try:
        opponent_db_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    challenger = await get_player(session, callback.from_user.id)
    if not challenger:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return

    r = await session.execute(select(Player).where(Player.id == opponent_db_id))
    opponent = r.scalar_one_or_none()
    if not opponent:
        await callback.answer("Игрок не найден.", show_alert=True)
        return

    if challenger.id == opponent.id:
        await callback.answer("Нельзя вызвать самого себя 🙂", show_alert=True)
        return

    if await boss_fight_rematch_blocked(session, challenger.id, opponent.id):
        await callback.answer("Сначала сыграй с кем-нибудь другим.", show_alert=True)
        return

    # Игрок может иметь только ОДИН активный матч одновременно (стол один,
    # матчи строго последовательные) — перекрывает и старый кейс «уже есть
    # активный матч именно с этим соперником» (это частный случай «занят»).
    my_active = await get_active_match(session, challenger.id)
    if my_active:
        busy_opp_id = (
            my_active.challenged_id if my_active.challenger_id == challenger.id
            else my_active.challenger_id
        )
        busy_opp_r = await session.execute(select(Player).where(Player.id == busy_opp_id))
        busy_opp = busy_opp_r.scalar_one()
        await callback.answer()
        await callback.message.edit_text(
            f"⚔️ У тебя уже есть активный матч с <b>{h(busy_opp.display_name)}</b>.\n"
            f"Заверши его, чтобы вызвать нового соперника.",
            reply_markup=busy_with_match_kb(my_active.id),
        )
        return

    # Пара (чемпион, текущий претендент) в любом направлении — босс-файт,
    # независимо от того, через ярлык «БОСС-ФАЙТ» или обычный вызов пришли.
    champion, current_challenger = await get_champion_and_challenger(session)
    is_boss_fight = (
        champion is not None and current_challenger is not None
        and {challenger.id, opponent.id} == {champion.id, current_challenger.id}
    )

    if await get_active_match(session, opponent.id):
        if champion is not None and opponent.id == champion.id:
            await callback.answer(
                "Чемпион сейчас занят другим матчем, попробуй позже.",
                show_alert=True,
            )
        else:
            await callback.answer(
                f"{opponent.display_name} сейчас занят другим матчем. Попробуй позже.",
                show_alert=True,
            )
        return

    # Счёт личных встреч
    h2h_r = await session.execute(
        select(Match).where(
            Match.status == MatchStatus.completed,
            or_(
                and_(Match.challenger_id == challenger.id, Match.challenged_id == opponent.id),
                and_(Match.challenger_id == opponent.id, Match.challenged_id == challenger.id),
            ),
        )
    )
    h2h = h2h_r.scalars().all()
    if h2h:
        ch_wins = sum(1 for m in h2h if m.winner_id == challenger.id)
        op_wins = sum(1 for m in h2h if m.winner_id == opponent.id)
        draws = sum(1 for m in h2h if m.winner_id is None)
        draws_str = f" (+{draws} 🤝)" if draws else ""
        h2h_ch = f"\n⚔️ Встречи: <b>{ch_wins}–{op_wins}</b>{draws_str}"
        h2h_op = f"\n⚔️ Встречи: <b>{op_wins}–{ch_wins}</b>{draws_str}"
    else:
        h2h_ch = h2h_op = ""

    # Матч сразу активен — без шага принятия
    match = Match(
        challenger_id=challenger.id,
        challenged_id=opponent.id,
        status=MatchStatus.accepted,
        accepted_at=datetime.now(timezone.utc).replace(tzinfo=None),
        is_boss_fight=is_boss_fight,
    )
    session.add(match)
    await session.commit()
    await session.refresh(match)

    opponent_chance = round(win_probability(opponent.rating, challenger.rating) * 100)
    opponent_phrase = match_phrase(opponent_chance, match.id)

    boss_fight_note = (
        "\n\n⚔️ <b>Это босс-файт за 1-е место.</b> Ничья невозможна — в конце останется только один."
        if is_boss_fight else ""
    )

    try:
        await bot.send_message(
            opponent.telegram_id,
            f"⚔️ <b>{h(challenger.display_name)}</b> вызывает тебя на матч!\n"
            f"Рейтинг соперника: <b>{round(challenger.rating, 1)}</b> pts\n"
            f"Твой рейтинг: <b>{round(opponent.rating, 1)}</b> pts"
            f"{h2h_op}\n\n"
            f"⚡ Твои шансы на победу: <b>~{opponent_chance}%</b>\n"
            f"<i>«{opponent_phrase}»</i>"
            f"{boss_fight_note}\n\n"
            f"📋 После игры просто напиши счёт сюда: <code>11:7 9:11 11:5</code>",
            reply_markup=active_match_kb(match.id),
        )
    except Exception:
        await session.delete(match)
        await session.commit()
        await callback.answer()
        await callback.message.edit_text(
            "❗ Не удалось отправить уведомление.\n"
            "Попроси соперника написать боту /start хотя бы раз.",
            reply_markup=back_to_menu_kb(),
        )
        return

    # Клубная рассылка о старте боссфайта — только теперь, когда обе стороны
    # уже точно уведомлены и матч не откатится (иначе клуб узнал бы о боссфайте,
    # который тут же исчез из-за недоступного оппонента).
    if is_boss_fight:
        challenger_p = challenger if challenger.id != champion.id else opponent
        await notify_all_players(
            bot, session,
            f"⚔️ <b>Грядёт БОСС-ФАЙТ!</b>\n\n"
            f"<b>{h(challenger_p.display_name)}</b> бросает вызов чемпиону "
            f"<b>{h(champion.display_name)}</b>.\n"
            f"Ничья невозможна — в конце останется только один.",
        )

    win_chance = round(win_probability(challenger.rating, opponent.rating) * 100)
    win_phrase = match_phrase(win_chance, match.id)

    await callback.message.edit_text(
        f"🏓 Матч с <b>{h(opponent.display_name)}</b> начат!\n"
        f"{h2h_ch}\n\n"
        f"⚡ Твои шансы на победу: <b>~{win_chance}%</b>\n"
        f"<i>«{win_phrase}»</i>"
        f"{boss_fight_note}\n\n"
        f"📋 После игры просто напиши счёт сюда: <code>11:7 9:11 11:5</code>",
        reply_markup=active_match_kb(match.id),
    )

    # Пасхалка: вызвал текущего лидера рейтинга. Раньше текст был "Похоже это
    # босс-файт" — путал с настоящей механикой боссфайта (Player.is_champion,
    # ×2 к рейтингу, запрет ничьей), т.к. это просто топ-1 по сырому рейтингу
    # без привязки к реальному чемпиону — переименовано в v2.89.0.
    top_r = await session.execute(
        select(func.count()).select_from(Player).where(Player.rating > opponent.rating)
    )
    if top_r.scalar() == 0:
        try:
            await bot.send_message(challenger.telegram_id, "💀 О, местную легенду вызвал. Смело.")
        except Exception:
            pass

    await callback.answer()


# ── Cancel match — шаг 1: подтверждение ──────────────────────────────────────

@router.callback_query(F.data.startswith("cancel_match_"))
async def cancel_match(callback: CallbackQuery, session: AsyncSession):
    try:
        match_id = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    r = await session.execute(select(Match).where(Match.id == match_id))
    match = r.scalar_one_or_none()

    if not match or match.status != MatchStatus.accepted:
        await callback.answer("Матч уже завершён или не найден.", show_alert=True)
        return

    player = await get_player(session, callback.from_user.id)
    if not player or player.id not in (match.challenger_id, match.challenged_id):
        await callback.answer("Это не твой матч.", show_alert=True)
        return

    await callback.answer()

    opponent_id = match.challenged_id if player.id == match.challenger_id else match.challenger_id
    r2 = await session.execute(select(Player).where(Player.id == opponent_id))
    opponent = r2.scalar_one()

    await callback.message.edit_text(
        f"❓ Точно отменить матч с <b>{h(opponent.display_name)}</b>?",
        reply_markup=cancel_match_confirm_kb(match_id),
    )


# ── Cancel match — шаг 2: фактическая отмена ─────────────────────────────────

@router.callback_query(F.data.startswith("cancel_yes_"))
async def do_cancel_match(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    try:
        match_id = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    r = await session.execute(select(Match).where(Match.id == match_id))
    match = r.scalar_one_or_none()

    if not match or match.status != MatchStatus.accepted:
        await callback.answer("Матч уже завершён или не найден.", show_alert=True)
        return

    player = await get_player(session, callback.from_user.id)
    if not player or player.id not in (match.challenger_id, match.challenged_id):
        await callback.answer("Это не твой матч.", show_alert=True)
        return

    opponent_id = match.challenged_id if player.id == match.challenger_id else match.challenger_id
    r2 = await session.execute(select(Player).where(Player.id == opponent_id))
    opponent = r2.scalar_one()

    # Атомарный guard (CAS) — как при внесении результата: если соперник успел
    # завершить матч между проверкой выше и этой строкой, отмена не затрёт completed.
    guard = await session.execute(
        update(Match)
        .where(Match.id == match_id, Match.status == MatchStatus.accepted)
        .values(status=MatchStatus.declined)
    )
    if guard.rowcount == 0:
        await callback.answer("Матч уже завершён или не найден.", show_alert=True)
        return
    await session.commit()

    await callback.message.edit_text(
        f"❌ Матч с <b>{h(opponent.display_name)}</b> отменён.",
        reply_markup=back_to_menu_kb(),
    )

    try:
        await bot.send_message(
            opponent.telegram_id,
            f"❌ <b>{h(player.display_name)}</b> отменил матч с тобой.",
            reply_markup=main_menu_kb(),
        )
    except Exception:
        pass

    # Достижение «Дух Анкориджа» — обоим участникам отменённого матча
    new_p = await check_cancel_achievements(session, player)
    new_o = await check_cancel_achievements(session, opponent)
    if new_p or new_o:
        await session.commit()
    for pl, ach_ids in ((player, new_p), (opponent, new_o)):
        for aid in ach_ids:
            a = ACHIEVEMENTS_MAP.get(aid)
            if not a:
                continue
            try:
                await bot.send_message(
                    pl.telegram_id,
                    f"🏅 <b>Новое достижение!</b>\n\n{a.emoji} <b>{a.name}</b>\n<i>{a.desc}</i>",
                )
            except Exception:
                pass

    await callback.answer()
