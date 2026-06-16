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
    match_rating_delta,
    match_score_challenger_first,
    msk_day_start,
    pluralize_matches,
    pluralize_wins,
)

router = Router()


# ── Leaderboard ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_leaderboard")
async def show_leaderboard(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

    viewer = await get_player(session, callback.from_user.id)
    viewer_id = viewer.id if viewer else None

    r = await session.execute(select(Player).order_by(desc(Player.rating)))
    players = r.scalars().all()

    if not players:
        await callback.message.edit_text("Пока нет игроков.", reply_markup=back_to_menu_kb())
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

    # Игроки без сыгранных матчей в рейтинге не показываются
    players = sorted(
        (p for p in players if match_count.get(p.id, 0) > 0),
        key=lambda p: -p.rating,
    )

    if not players:
        await callback.message.edit_text(
            "Пока нет сыгранных матчей. 🏓", reply_markup=back_to_menu_kb()
        )
        return

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


# ── Today stats ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_today")
async def show_today_stats(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

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
    lines = ["📅 <b>Сегодня</b>\n", f"⚡ Сыграно матчей: <b>{len(matches)}</b>"]

    # Личный мини-итог зрителя — сразу под общим счётчиком
    viewer = await get_player(session, callback.from_user.id)
    if viewer:
        if viewer.id in stats:
            vs = stats[viewer.id]
            v_delta = round(sum(
                match_rating_delta(m, viewer.id)
                for m in matches
                if viewer.id in (m.challenger_id, m.challenged_id)
            ), 1)
            v_draws = f"–{vs['draws']}🤝" if vs["draws"] else ""
            d_icon = " 📈" if v_delta > 0 else (" 📉" if v_delta < 0 else "")
            sign = "+" if v_delta >= 0 else ""
            lines.append(
                f"👤 <b>Ты сегодня:</b> {vs['wins']}–{vs['losses']}{v_draws}, "
                f"{sign}{v_delta} pts{d_icon}"
            )
        else:
            lines.append("👤 <b>Ты сегодня</b> ещё не играл 🏓")
    lines.append("")  # пустая строка-разделитель перед «Топ дня»

    for i, (pid, s) in enumerate(sorted_players):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        draws_str = f"–{s['draws']}🤝" if s["draws"] else ""
        lines.append(
            f"{prefix} <b>{h(names[pid])}</b> — "
            f"{s['wins']}–{s['losses']}{draws_str}  <i>({pluralize_matches(s['total'])})</i>"
        )

    if inactive:
        inactive_names = ", ".join(h(p.display_name) for p in inactive)
        lines.append(f"\n😴 Не играли: {inactive_names}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=back_to_leaderboard_kb(),
        parse_mode="HTML",
    )


# ── Рекорды клуба ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "club_records")
async def show_club_records(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

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
            f"🏓 Больше всего матчей — <b>{h(name_map.get(most_id, '?'))}</b>: "
            f"{pluralize_matches(match_count[most_id])}"
        )

    # Высший рейтинг в истории — пик среди игравших (peak_rating, fallback на текущий)
    peak_pid = None
    peak_val = 0.0
    for p in players:
        if match_count.get(p.id, 0) == 0:
            continue
        pv = p.peak_rating if p.peak_rating is not None else p.rating
        if pv > peak_val:
            peak_val = pv
            peak_pid = p.id
    if peak_pid is not None:
        lines.append(
            f"📈 Высший рейтинг в истории — <b>{h(name_map.get(peak_pid, '?'))}</b>: "
            f"{round(peak_val, 1)} pts"
        )

    # Дерби клуба — самая играющая пара
    pair_count: dict[tuple[int, int], int] = {}
    for m in all_matches:
        key = (min(m.challenger_id, m.challenged_id), max(m.challenger_id, m.challenged_id))
        pair_count[key] = pair_count.get(key, 0) + 1
    if pair_count:
        (a_id, b_id), pair_n = max(pair_count.items(), key=lambda kv: kv[1])
        if pair_n >= 2:
            lines.append(
                f"🤼 Дерби клуба — <b>{h(name_map.get(a_id, '?'))}</b> vs "
                f"<b>{h(name_map.get(b_id, '?'))}</b>: {pluralize_matches(pair_n)}"
            )

    # Нагибатор клуба — самое одностороннее противостояние (победы только)
    pair_wins: dict[tuple[int, int], dict[int, int]] = {}
    for m in all_matches:
        if m.winner_id is None:
            continue
        key = (min(m.challenger_id, m.challenged_id), max(m.challenger_id, m.challenged_id))
        wd = pair_wins.setdefault(key, {})
        wd[m.winner_id] = wd.get(m.winner_id, 0) + 1

    best_dom = None  # (gap, dom_w, dom_id, vic_id, vic_w)
    for (pa_id, pb_id), wd in pair_wins.items():
        a_w, b_w = wd.get(pa_id, 0), wd.get(pb_id, 0)
        if a_w >= b_w:
            dom_id, vic_id, dom_w, vic_w = pa_id, pb_id, a_w, b_w
        else:
            dom_id, vic_id, dom_w, vic_w = pb_id, pa_id, b_w, a_w
        gap = dom_w - vic_w
        # Порог: доминирующий выиграл ≥3 раза и ведёт — иначе не «нагибатор»
        if dom_w >= 3 and gap >= 1:
            cand = (gap, dom_w, dom_id, vic_id, vic_w)
            if best_dom is None or cand[:2] > best_dom[:2]:
                best_dom = cand
    if best_dom:
        _, dom_w, dom_id, vic_id, vic_w = best_dom
        lines.append(
            f"😈 Нагибатор клуба — <b>{h(name_map.get(dom_id, '?'))}</b> над "
            f"<b>{h(name_map.get(vic_id, '?'))}</b>: {dom_w}–{vic_w}"
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
            f"🔥 Лучшая серия побед — <b>{h(name_map.get(best_streak_pid, '?'))}</b>: "
            f"{best_streak_n} подряд"
        )

    # В ударе сейчас — текущая активная серия побед (от последнего матча назад)
    cur_streak_n = 0
    cur_streak_pid = None
    for pid, ms in player_matches_asc.items():
        s = 0
        for m in reversed(ms):
            if m.winner_id == pid:
                s += 1
            else:
                break
        if s > cur_streak_n:
            cur_streak_n = s
            cur_streak_pid = pid
    if cur_streak_pid and cur_streak_n >= 2:
        lines.append(
            f"🚀 В ударе сейчас — <b>{h(name_map.get(cur_streak_pid, '?'))}</b>: "
            f"{pluralize_wins(cur_streak_n)} подряд"
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


# ── Матрица доминирования ─────────────────────────────────────────────────────

@router.callback_query(F.data == "dominance_matrix")
async def show_dominance_matrix(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

    players_r = await session.execute(select(Player).order_by(desc(Player.rating)))
    all_players = players_r.scalars().all()

    matches_r = await session.execute(
        select(Match).where(Match.status == MatchStatus.completed)
    )
    all_matches = matches_r.scalars().all()

    if not all_players:
        await callback.message.edit_text(
            "⚔️ <b>Матрица доминирования</b>\n\nИгроков пока нет.",
            reply_markup=back_to_leaderboard_kb(),
            parse_mode="HTML",
        )
        return

    match_count: dict[int, int] = {}
    for m in all_matches:
        for pid in (m.challenger_id, m.challenged_id):
            match_count[pid] = match_count.get(pid, 0) + 1

    # Игроки без сыгранных матчей в матрице не показываются
    players_sorted = sorted(
        (p for p in all_players if match_count.get(p.id, 0) > 0),
        key=lambda p: -p.rating,
    )

    cap = 8
    capped = len(players_sorted) > cap
    top = players_sorted[:cap]
    n = len(top)

    if n < 2:
        await callback.message.edit_text(
            "⚔️ <b>Матрица доминирования</b>\n\nНедостаточно игроков.",
            reply_markup=back_to_leaderboard_kb(),
            parse_mode="HTML",
        )
        return

    pid_idx = {p.id: i for i, p in enumerate(top)}
    wins = [[0] * n for _ in range(n)]
    for m in all_matches:
        if m.winner_id is None:
            continue
        wi = pid_idx.get(m.winner_id)
        li_id = m.challenged_id if m.winner_id == m.challenger_id else m.challenger_id
        li = pid_idx.get(li_id)
        if wi is not None and li is not None:
            wins[wi][li] += 1

    # Имена до 4 символов и один пробел между колонками: на телефоне <code>-блок
    # переносится примерно после 30-35 символов, при 5 игроках строка должна влезть.
    names = [p.display_name[:4] for p in top]
    max_cell_len = max(
        len(f"{wins[i][j]}-{wins[j][i]}")
        for i in range(n) for j in range(n) if i != j
    )
    col_w = max(max(len(nm) for nm in names), max_cell_len, 3)
    row_w = max(len(nm) for nm in names)

    header = " " * (row_w + 1) + " ".join(nm.center(col_w) for nm in names)
    rows = [header]
    for i in range(n):
        cells = []
        for j in range(n):
            cell = "—" if i == j else f"{wins[i][j]}-{wins[j][i]}"
            cells.append(cell.center(col_w))
        rows.append(names[i].ljust(row_w) + " " + " ".join(cells))

    table = "\n".join(rows)
    cap_note = "\n<i>Показаны топ-8 по рейтингу</i>" if capped else ""
    text = (
        f"⚔️ <b>Матрица доминирования</b>{cap_note}\n\n"
        f"<code>{table}</code>\n\n"
        f"<i>Строка: сколько раз победил соперника из столбца (победы-поражения).</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_to_leaderboard_kb(),
        parse_mode="HTML",
    )
