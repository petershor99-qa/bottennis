from datetime import datetime, timedelta, timezone
from html import escape as h

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Match, MatchStatus, Player
from bot.keyboards.inline import back_to_leaderboard_kb, back_to_menu_kb, leaderboard_kb
from bot.utils import (
    compute_alltime_streak,
    get_player,
    match_drama_reason,
    match_drama_score,
    match_score_challenger_first,
    msk_day_start,
    pluralize_matches,
)

router = Router()


# ── Leaderboard ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_leaderboard")
async def show_leaderboard(callback: CallbackQuery, session: AsyncSession):
    viewer = await get_player(session, callback.from_user.id)
    viewer_id = viewer.id if viewer else None

    r = await session.execute(select(Player).order_by(desc(Player.rating)))
    players = r.scalars().all()

    if not players:
        await callback.message.edit_text("Пока нет игроков.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    matches_r = await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed)
        .order_by(desc(Match.completed_at))
    )
    all_matches = matches_r.scalars().all()

    match_count: dict[int, int] = {}
    win_count: dict[int, int] = {}
    player_matches: dict[int, list] = {}
    for m in all_matches:
        for pid in (m.challenger_id, m.challenged_id):
            match_count[pid] = match_count.get(pid, 0) + 1
            if pid not in player_matches:
                player_matches[pid] = []
            player_matches[pid].append(m)
        if m.winner_id:
            win_count[m.winner_id] = win_count.get(m.winner_id, 0) + 1

    streak_map: dict[int, int] = {}
    for pid, ms in player_matches.items():
        s = 0
        for m in ms:
            if m.winner_id == pid:
                s += 1
            else:
                break
        streak_map[pid] = s

    # Игроки без матчей — в конец таблицы
    players = sorted(players, key=lambda p: (match_count.get(p.id, 0) == 0, -p.rating))

    week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    active_7day: set[int] = {
        pid
        for m in all_matches
        if m.completed_at and m.completed_at >= week_ago
        for pid in (m.challenger_id, m.challenged_id)
    }

    # ── Изменение позиции за неделю (▲▼) ────────────────────────────────────────
    # Восстанавливаем рейтинги «неделю назад», откатывая дельты матчей за 7 дней.
    # Пол рейтинга при откате игнорируется — это приблизительный индикатор.
    snap = {p.id: p.rating for p in players}
    for m in all_matches:
        if not (m.completed_at and m.completed_at >= week_ago) or m.rating_change is None:
            continue
        d = m.rating_change
        if m.winner_id is None:
            snap[m.challenger_id] = round(snap.get(m.challenger_id, 1000.0) - d, 1)
            snap[m.challenged_id] = round(snap.get(m.challenged_id, 1000.0) + d, 1)
        else:
            wid = m.winner_id
            lid = m.challenged_id if wid == m.challenger_id else m.challenger_id
            snap[wid] = round(snap.get(wid, 1000.0) - d, 1)
            snap[lid] = round(snap.get(lid, 1000.0) + d, 1)

    old_count: dict[int, int] = {}
    for m in all_matches:
        if m.completed_at and m.completed_at < week_ago:
            for pid in (m.challenger_id, m.challenged_id):
                old_count[pid] = old_count.get(pid, 0) + 1

    prev_order = sorted(
        players, key=lambda p: (old_count.get(p.id, 0) == 0, -snap.get(p.id, p.rating))
    )
    prev_pos = {p.id: i for i, p in enumerate(prev_order)}

    medals = ["🥇", "🥈", "🥉"]
    lines = ["📊 <b>Рейтинг игроков:</b>\n"]
    for i, p in enumerate(players):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        count = match_count.get(p.id, 0)
        wins = win_count.get(p.id, 0)
        wr = int(wins / count * 100) if count else 0
        if p.id not in active_7day:
            badge = " ❄️"
        elif streak_map.get(p.id, 0) >= 3:
            badge = " 🔥"
        else:
            badge = ""
        # Стрелка изменения позиции (только для игравших игроков)
        change = prev_pos.get(p.id, i) - i
        if count > 0 and change > 0:
            pos_str = f"  ▲{change}"
        elif count > 0 and change < 0:
            pos_str = f"  ▼{-change}"
        else:
            pos_str = ""
        name = f"<b>{h(p.display_name)}</b>" if p.id == viewer_id else h(p.display_name)
        lines.append(
            f"{prefix} {name}{badge} — <b>{round(p.rating, 1)}</b> pts"
            f"  <i>({pluralize_matches(count)}, {wr}%)</i>{pos_str}"
        )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=leaderboard_kb(players),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Today stats ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_today")
async def show_today_stats(callback: CallbackQuery, session: AsyncSession):
    today_start = msk_day_start()   # день по МСК — как в итогах дня

    matches_r = await session.execute(
        select(Match)
        .where(
            Match.status == MatchStatus.completed,
            Match.completed_at >= today_start,
        )
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
        .order_by(desc(Match.completed_at))
    )
    matches = matches_r.scalars().all()

    if not matches:
        await callback.message.edit_text(
            "📅 <b>Сегодня</b>\n\nМатчей пока не было. Первым сделай ход! 🏓",
            reply_markup=back_to_leaderboard_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    stats: dict[int, dict] = {}
    names: dict[int, str] = {}

    for m in matches:
        for p in (m.challenger, m.challenged):
            if p.id not in stats:
                stats[p.id] = {"wins": 0, "losses": 0, "draws": 0, "total": 0}
                names[p.id] = p.display_name
            stats[p.id]["total"] += 1

        if m.winner_id is None:
            stats[m.challenger_id]["draws"] += 1
            stats[m.challenged_id]["draws"] += 1
        else:
            stats[m.winner_id]["wins"] += 1
            loser_id = m.challenged_id if m.winner_id == m.challenger_id else m.challenger_id
            stats[loser_id]["losses"] += 1

    sorted_players = sorted(
        stats.items(),
        key=lambda x: (x[1]["wins"], x[1]["total"]),
        reverse=True,
    )

    all_r = await session.execute(select(Player))
    all_players = all_r.scalars().all()
    inactive = [p for p in all_players if p.id not in stats]

    medals = ["🥇", "🥈", "🥉"]
    lines = ["📅 <b>Сегодня</b>\n", f"⚡ Сыграно матчей: <b>{len(matches)}</b>\n"]

    for i, (pid, s) in enumerate(sorted_players):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        draws_str = f"–{s['draws']}🤝" if s["draws"] else ""
        lines.append(
            f"{prefix} <b>{h(names[pid])}</b> — "
            f"{s['wins']}–{s['losses']}{draws_str}  <i>({s['total']} матчей)</i>"
        )

    if inactive:
        inactive_names = ", ".join(h(p.display_name) for p in inactive)
        lines.append(f"\n😴 Не играли: {inactive_names}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=back_to_leaderboard_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Рекорды клуба ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "club_records")
async def show_club_records(callback: CallbackQuery, session: AsyncSession):
    matches_r = await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
        .order_by(Match.completed_at)
    )
    all_matches = matches_r.scalars().all()

    if not all_matches:
        await callback.message.edit_text(
            "🏆 <b>Рекорды клуба</b>\n\nМатчей ещё не было.",
            reply_markup=back_to_leaderboard_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    players_r = await session.execute(select(Player))
    players = players_r.scalars().all()
    name_map = {p.id: p.display_name for p in players}

    lines = ["🏆 <b>Рекорды клуба</b>\n"]

    # Больше всего матчей
    match_count: dict[int, int] = {}
    for m in all_matches:
        for pid in (m.challenger_id, m.challenged_id):
            match_count[pid] = match_count.get(pid, 0) + 1
    if match_count:
        most_id = max(match_count, key=match_count.get)
        lines.append(
            f"🏓 Больше всего матчей — <b>{h(name_map[most_id])}</b>: "
            f"{pluralize_matches(match_count[most_id])}"
        )

    # Лучшая серия побед за всё время
    player_matches_asc: dict[int, list] = {}
    for m in all_matches:
        for pid in (m.challenger_id, m.challenged_id):
            player_matches_asc.setdefault(pid, []).append(m)

    best_streak_n = 0
    best_streak_pid = None
    for pid, ms in player_matches_asc.items():
        s = compute_alltime_streak(ms, pid)
        if s > best_streak_n:
            best_streak_n = s
            best_streak_pid = pid

    if best_streak_pid and best_streak_n >= 2:
        lines.append(
            f"🔥 Лучшая серия побед — <b>{h(name_map[best_streak_pid])}</b>: "
            f"{best_streak_n} подряд"
        )

    # Самый длинный матч (больше всего партий)
    with_sets = [m for m in all_matches if m.sets_data]
    if with_sets:
        longest = max(with_sets, key=lambda m: len(m.sets_data))
        ch = name_map.get(longest.challenger_id, "?")
        cd = name_map.get(longest.challenged_id, "?")
        score_str = match_score_challenger_first(longest)
        date_str = longest.completed_at.strftime("%d.%m.%y") if longest.completed_at else ""
        lines.append(
            f"🎯 Самый длинный матч — <b>{h(ch)}</b> vs <b>{h(cd)}</b>: "
            f"{len(longest.sets_data)} партий  <i>{score_str}  {date_str}</i>"
        )

    # Крупнейший апсет (наибольшая дельта рейтинга)
    upsets = [m for m in all_matches if m.rating_change is not None and m.winner_id is not None]
    if upsets:
        biggest = max(upsets, key=lambda m: m.rating_change)
        if biggest.rating_change >= 15:
            w_name = name_map.get(biggest.winner_id, "?")
            l_id = biggest.challenged_id if biggest.winner_id == biggest.challenger_id else biggest.challenger_id
            l_name = name_map.get(l_id, "?")
            score_str = match_score_challenger_first(biggest)
            lines.append(
                f"💥 Крупнейший апсет — <b>{h(w_name)}</b> победил <b>{h(l_name)}</b>: "
                f"+{biggest.rating_change} pts  <i>{score_str}</i>"
            )

    # Самый эпичный матч (максимальный drama score)
    if with_sets:
        best_drama = max(with_sets, key=match_drama_score)
        if match_drama_score(best_drama) >= 4.0:
            ch = name_map.get(best_drama.challenger_id, "?")
            cd = name_map.get(best_drama.challenged_id, "?")
            score_str = match_score_challenger_first(best_drama)
            reason = match_drama_reason(best_drama)
            date_str = best_drama.completed_at.strftime("%d.%m.%y") if best_drama.completed_at else ""
            lines.append(
                f"\n🌟 <b>Самый эпичный матч</b>\n"
                f"<b>{h(ch)}</b> vs <b>{h(cd)}</b> — {score_str}  <i>{date_str}</i>\n"
                f"<i>{reason}</i>"
            )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=back_to_leaderboard_kb(),
        parse_mode="HTML",
    )
    await callback.answer()
