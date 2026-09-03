from datetime import datetime, timedelta, timezone
from html import escape as h

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import ChampionReign, Match, MatchStatus, Player
from bot.keyboards.inline import (
    back_to_leaderboard_kb,
    back_to_menu_kb,
    back_to_stats_kb,
    hall_of_fame_kb,
    leaderboard_kb,
)
from bot.utils import (
    MSK_OFFSET,
    _pin_champion,
    compute_alltime_streak,
    get_champion_and_challenger,
    get_player,
    longest_awaited_revenge,
    longest_champion_reign,
    match_drama_reason,
    match_drama_score,
    match_rating_delta,
    match_report,
    match_score_challenger_first,
    most_boss_fight_defenses,
    most_throne_ascensions,
    msk_day_start,
    pluralize_days,
    pluralize_defenses,
    pluralize_matches,
    pluralize_times,
    pluralize_wins,
    shortest_champion_reign,
    steadiest_career,
)

router = Router()

# 8, не больше — даже в худшем случае (все правления с максимально
# драматичным нарративом от match_report) страница комфортно укладывается в
# лимит Telegram на сообщение (см. hall_of_fame_kb и регресс-тест на лимит).
HALL_OF_FAME_PAGE_SIZE = 8


# ── Leaderboard ───────────────────────────────────────────────────────────────

async def _build_leaderboard_screen(session: AsyncSession, telegram_id: int):
    """Строит (текст, клавиатуру) экрана «Рейтинг» — общая часть для
    инлайн-кнопки меню (edit_text) и постоянной клавиатуры снизу (answer)."""
    viewer = await get_player(session, telegram_id)
    viewer_id = viewer.id if viewer else None

    r = await session.execute(select(Player).order_by(desc(Player.rating)))
    players = r.scalars().all()

    if not players:
        return "Пока нет игроков.", back_to_menu_kb()

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
    played = [p for p in players if match_count.get(p.id, 0) > 0]

    if not played:
        return "Пока нет сыгранных матчей. 🏓", back_to_menu_kb()

    # Место #1 занимается только через босс-файт, не по очкам — leaderboard не
    # использует compute_ranks (своя сортировка), поэтому пиннинг чемпиона
    # дублируется здесь же. Если чемпион не назначен (фича выключена) — обычная
    # сортировка по рейтингу, как раньше.
    champion, challenger_player = await get_champion_and_challenger(session)
    champion_id = champion.id if champion else None
    challenger_id = challenger_player.id if challenger_player else None

    players = _pin_champion(sorted(played, key=lambda p: -p.rating), champion_id)

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

    prev_order = _pin_champion(
        sorted(players, key=lambda p: (old_count.get(p.id, 0) == 0, -snap.get(p.id, p.rating))),
        champion_id,
    )
    prev_pos = {p.id: i for i, p in enumerate(prev_order)}

    medals = ["🥇", "🥈", "🥉"]
    lines = ["📊 <b>Рейтинг игроков:</b>\n"]
    for i, p in enumerate(players):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        count = match_count.get(p.id, 0)
        wins = win_count.get(p.id, 0)
        wr = int(wins / count * 100) if count else 0
        # 👑/🗡 приоритетнее ❄️/🔥 (босс-файт важнее формы), ❄️ приоритетнее 🔥
        if champion_id is not None and p.id == champion_id:
            badge = " 👑"
        elif challenger_id is not None and p.id == challenger_id:
            badge = " 🗡"
        elif p.id not in active_7day:
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

    return "\n".join(lines), leaderboard_kb(players)


@router.callback_query(F.data == "menu_leaderboard")
async def show_leaderboard(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    text, kb = await _build_leaderboard_screen(session, callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)


@router.message(F.text == "📊 Рейтинг")
async def show_leaderboard_from_reply_kb(message: Message, session: AsyncSession):
    """Тот же экран, что и menu_leaderboard, но с постоянной клавиатуры снизу."""
    text, kb = await _build_leaderboard_screen(session, message.from_user.id)
    await message.answer(text, reply_markup=kb)


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
            reply_markup=back_to_stats_kb(),
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
        reply_markup=back_to_stats_kb(),
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
        )
        return

    players_r = await session.execute(select(Player))
    players = players_r.scalars().all()
    name_map = {p.id: p.display_name for p in players}

    # Рекорды сгруппированы по смыслу (объёмы / противостояния / серии / топ-моменты),
    # каждая непустая группа отделена пустой строкой — иначе длинные двухстрочные
    # записи сливаются в нечитаемую простыню без явных границ между рекордами.
    volume_lines: list[str] = []
    rivalry_lines: list[str] = []
    streak_lines: list[str] = []
    highlight_lines: list[str] = []

    # Больше всего матчей
    match_count: dict[int, int] = {}
    for m in all_matches:
        for pid in (m.challenger_id, m.challenged_id):
            match_count[pid] = match_count.get(pid, 0) + 1
    if match_count:
        most_id = max(match_count, key=match_count.get)
        volume_lines.append(
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
        volume_lines.append(
            f"📈 Высший рейтинг в истории — <b>{h(name_map.get(peak_pid, '?'))}</b>: "
            f"{round(peak_val, 1)} pts"
        )

    # Дольше всех лидировал — самое долгое непрерывное правление на #1
    # (боссфайт). Нет данных, если фича ни разу не бутстрапилась — тогда
    # ChampionReign пуст и longest_champion_reign вернёт None.
    reign = await longest_champion_reign(session)
    if reign is not None:
        reign_player_id, reign_days = reign
        reign_days_str = "меньше дня" if reign_days == 0 else pluralize_days(reign_days)
        volume_lines.append(
            f"👑 Дольше всех лидировал — <b>{h(name_map.get(reign_player_id, '?'))}</b>: "
            f"{reign_days_str}"
        )

    # Больше всего защит трона подряд — за одно правление (боссфайт)
    defenses = await most_boss_fight_defenses(session)
    if defenses is not None:
        defense_player_id, defense_count = defenses
        if defense_count >= 1:
            volume_lines.append(
                f"🛡 Больше всего защит трона подряд — <b>{h(name_map.get(defense_player_id, '?'))}</b>: "
                f"{pluralize_defenses(defense_count)}"
            )

    # Самое короткое правление на #1 — антипод «Дольше всех лидировал»
    short_reign = await shortest_champion_reign(session)
    if short_reign is not None:
        short_reign_pid, short_reign_days = short_reign
        short_reign_str = "меньше дня" if short_reign_days == 0 else pluralize_days(short_reign_days)
        volume_lines.append(
            f"⏳ Самое короткое правление — <b>{h(name_map.get(short_reign_pid, '?'))}</b>: "
            f"{short_reign_str}"
        )

    # Больше всего восхождений на трон — число ОТДЕЛЬНЫХ правлений, не длительность
    ascensions = await most_throne_ascensions(session)
    if ascensions is not None:
        asc_pid, asc_count = ascensions
        volume_lines.append(
            f"🪜 Больше всего восхождений на трон — <b>{h(name_map.get(asc_pid, '?'))}</b>: "
            f"{pluralize_times(asc_count)}"
        )

    # Железные нервы — наименьший разброс рейтинга за всю карьеру (антипод
    # «Американских горок» из периодических дайджестов, но за карьеру целиком)
    steadiest = await steadiest_career(session)
    if steadiest is not None:
        steady_pid, steady_val = steadiest
        volume_lines.append(
            f"🧘 Железные нервы — <b>{h(name_map.get(steady_pid, '?'))}</b>: "
            f"рейтинг качает всего на {steady_val} pts/матч"
        )

    # Долгожданная месть — самый большой разрыв по времени между поражением и
    # следующей победой над тем же соперником
    revenge = await longest_awaited_revenge(session)
    if revenge is not None:
        avenger_id, opp_id, revenge_days = revenge
        rivalry_lines.append(
            f"⏰ Долгожданная месть — <b>{h(name_map.get(avenger_id, '?'))}</b> "
            f"взял реванш у <b>{h(name_map.get(opp_id, '?'))}</b> спустя "
            f"{pluralize_days(revenge_days)}"
        )

    # Больше всего ничьих
    draw_count: dict[int, int] = {}
    for m in all_matches:
        if m.winner_id is None:
            draw_count[m.challenger_id] = draw_count.get(m.challenger_id, 0) + 1
            draw_count[m.challenged_id] = draw_count.get(m.challenged_id, 0) + 1
    if draw_count:
        most_draws_id = max(draw_count, key=draw_count.get)
        if draw_count[most_draws_id] >= 3:
            volume_lines.append(
                f"🤝 Больше всего ничьих — <b>{h(name_map.get(most_draws_id, '?'))}</b>: "
                f"{draw_count[most_draws_id]} ничьих"
            )

    # Самый жаркий день клуба — сумма матчей всех игроков за один день (не одного игрока)
    day_totals: dict = {}
    for m in all_matches:
        if m.completed_at:
            day = (m.completed_at + MSK_OFFSET).date()
            day_totals[day] = day_totals.get(day, 0) + 1
    if day_totals:
        hottest_day, hottest_n = max(day_totals.items(), key=lambda kv: kv[1])
        if hottest_n >= 3:
            volume_lines.append(
                f"🌡 Самый жаркий день клуба — <b>{hottest_day.strftime('%d.%m.%y')}</b>: "
                f"{pluralize_matches(hottest_n)}"
            )

    # Дерби клуба — самая играющая пара
    pair_count: dict[tuple[int, int], int] = {}
    for m in all_matches:
        key = (min(m.challenger_id, m.challenged_id), max(m.challenger_id, m.challenged_id))
        pair_count[key] = pair_count.get(key, 0) + 1
    if pair_count:
        (a_id, b_id), pair_n = max(pair_count.items(), key=lambda kv: kv[1])
        if pair_n >= 2:
            rivalry_lines.append(
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
        rivalry_lines.append(
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
        streak_lines.append(
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
        streak_lines.append(
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
        streak_lines.append(
            f"🎯 Самый длинный матч — <b>{h(ch)}</b> vs <b>{h(cd)}</b>: "
            f"{len(longest.sets_data)} партий  <i>{score_str}  {date_str}</i>"
        )

    # Самый долгий боссфайт (больше всего партий среди боссфайтов) — та же
    # метрика, что у «Самого длинного матча», просто отфильтрована по is_boss_fight.
    bf_with_sets = [m for m in all_matches if m.sets_data and m.is_boss_fight]
    if bf_with_sets:
        longest_bf = max(bf_with_sets, key=lambda m: len(m.sets_data))
        ch = name_map.get(longest_bf.challenger_id, "?")
        cd = name_map.get(longest_bf.challenged_id, "?")
        score_str = match_score_challenger_first(longest_bf)
        date_str = longest_bf.completed_at.strftime("%d.%m.%y") if longest_bf.completed_at else ""
        streak_lines.append(
            f"⚔️ Самый долгий боссфайт — <b>{h(ch)}</b> vs <b>{h(cd)}</b>: "
            f"{len(longest_bf.sets_data)} партий  <i>{score_str}  {date_str}</i>"
        )

    # Самый быстрый матч — от принятия вызова до внесения результата
    timed = [
        m for m in all_matches
        if m.accepted_at and m.completed_at and m.completed_at > m.accepted_at
    ]
    if timed:
        fastest = min(timed, key=lambda m: m.completed_at - m.accepted_at)
        duration_min = int((fastest.completed_at - fastest.accepted_at).total_seconds() // 60)
        duration_str = "меньше минуты" if duration_min < 1 else f"{duration_min} мин"
        ch = name_map.get(fastest.challenger_id, "?")
        cd = name_map.get(fastest.challenged_id, "?")
        streak_lines.append(
            f"🏃 Самый быстрый матч — <b>{h(ch)}</b> vs <b>{h(cd)}</b>: {duration_str}"
        )

    # Крупнейший апсет (наибольшая дельта рейтинга). Боссфайты исключены — их
    # дельта безусловно ×2 (BOSS_FIGHT_MULT), поэтому даже предсказуемая победа
    # фаворита может обойти по цифре реальный апсет и исказить рекорд.
    upsets = [
        m for m in all_matches
        if m.rating_change is not None and m.winner_id is not None and not m.is_boss_fight
    ]
    if upsets:
        biggest = max(upsets, key=lambda m: m.rating_change)
        if biggest.rating_change >= 15:
            w_name = name_map.get(biggest.winner_id, "?")
            l_id = biggest.challenged_id if biggest.winner_id == biggest.challenger_id else biggest.challenger_id
            l_name = name_map.get(l_id, "?")
            score_str = match_score_challenger_first(biggest)
            highlight_lines.append(
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
            highlight_lines.append(
                f"🌟 <b>Самый эпичный матч</b>\n"
                f"<b>{h(ch)}</b> vs <b>{h(cd)}</b> — {score_str}  <i>{date_str}</i>\n"
                f"<i>{reason}</i>"
            )

    # Пустая строка перед КАЖДОЙ записью (не только между группами) — иначе
    # длинные записи (счёт марафона на 10 партий и т.п.) визуально сливаются
    # со следующей строкой в той же группе, границу между рекордами не видно.
    lines = ["🏆 <b>Рекорды клуба</b>"]
    for group in (volume_lines, rivalry_lines, streak_lines, highlight_lines):
        for record in group:
            lines.append("")
            lines.append(record)

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=back_to_leaderboard_kb(),
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

    # Подписи игроков — обрезанные имена. Длину обрезки подбираем минимальной, при
    # которой все подписи остаются уникальными (обычно 4 символа; больше — если есть
    # тёзки по началу имени). Сетка идёт в <pre>: это настоящий моноширинный блок, в
    # нём буквы (в т.ч. кириллица) выравниваются так же ровно, как цифры, и длинная
    # строка скроллится вбок, а не переносится (в инлайн-<code> и то и другое ломало
    # столбцы на мобильном).
    full = [p.display_name for p in top]
    label_len = 4
    while label_len < 12 and len({nm[:label_len] for nm in full}) < n:
        label_len += 1
    labels = [nm[:label_len] for nm in full]

    max_cell_len = max(
        len(f"{wins[i][j]}-{wins[j][i]}")
        for i in range(n) for j in range(n) if i != j
    )
    col_w = max(max_cell_len, max(len(lbl) for lbl in labels))
    row_w = max(len(lbl) for lbl in labels)

    header = " " * (row_w + 1) + " ".join(lbl.rjust(col_w) for lbl in labels)
    rows = [header]
    for i in range(n):
        cells = []
        for j in range(n):
            cell = "—" if i == j else f"{wins[i][j]}-{wins[j][i]}"
            cells.append(cell.rjust(col_w))
        rows.append(labels[i].ljust(row_w) + " " + " ".join(cells))

    table = "\n".join(rows)
    cap_note = "\n<i>Показаны топ-8 по рейтингу</i>" if capped else ""
    text = (
        f"⚔️ <b>Матрица доминирования</b>{cap_note}\n\n"
        f"<pre>{h(table)}</pre>\n"
        f"<i>Строка — игрок, столбец — соперник: сколько раз обыграл его "
        f"(победы-поражения).</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_to_leaderboard_kb(),
    )


# ── Индекс формы ──────────────────────────────────────────────────────────────
# Отдельный ранжированный список от общего лидерборда: не вся карьера, а
# последние FORM_WINDOW матчей каждого игрока — «кто горячий прямо сейчас»,
# а не «кто вообще сильнее». Слабый по общему рейтингу игрок на удачной
# полосе здесь виден в топе, хотя на основном лидерборде так не увидеть —
# тот считает по всей карьере. Место в UI — рядом с «Рекордами клуба»/
# «Матрицей доминирования»: та же кнопка-полка на экране рейтинга, тот же
# паттерн (кнопка → отдельный экран).

FORM_WINDOW = 10  # сколько последних матчей на игрока учитывает индекс формы


@router.callback_query(F.data == "form_index")
async def show_form_index(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

    players_r = await session.execute(select(Player))
    players = players_r.scalars().all()

    matches_r = await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed)
        .order_by(desc(Match.completed_at))
    )
    all_matches = matches_r.scalars().all()

    player_matches: dict[int, list] = {}
    for m in all_matches:
        for pid in (m.challenger_id, m.challenged_id):
            player_matches.setdefault(pid, []).append(m)

    rows = []
    for p in players:
        # all_matches уже отсортирован desc(completed_at) — срез с начала это
        # и есть последние FORM_WINDOW матчей игрока.
        recent = player_matches.get(p.id, [])[:FORM_WINDOW]
        if not recent:
            continue
        wins = sum(1 for m in recent if m.winner_id == p.id)
        draws = sum(1 for m in recent if m.winner_id is None)
        losses = len(recent) - wins - draws
        delta = round(sum(match_rating_delta(m, p.id) for m in recent), 1)
        rows.append((p, wins, losses, draws, delta, len(recent)))

    if not rows:
        await callback.message.edit_text(
            "🌡 <b>Индекс формы</b>\n\nМатчей ещё не было.",
            reply_markup=back_to_leaderboard_kb(),
        )
        return

    rows.sort(key=lambda r: -r[4])

    lines = [f"🌡 <b>Индекс формы</b>  <i>(последние {FORM_WINDOW} матчей)</i>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (p, wins, losses, draws, delta, total) in enumerate(rows):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        if delta > 15:
            icon = "🔥"
        elif delta < -15:
            icon = "🥶"
        else:
            icon = "⚡"
        draws_str = f"–{draws}🤝" if draws else ""
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"{prefix} {icon} <b>{h(p.display_name)}</b> — "
            f"{wins}–{losses}{draws_str}  <i>({sign}{delta} pts за {total})</i>"
        )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=back_to_leaderboard_kb(),
    )


# ── Зал славы ─────────────────────────────────────────────────────────────────
# История всех правлений на месте #1 (ChampionReign) — до сих пор эти записи
# использовались только для одного числа в рекордах клуба («Дольше всех
# лидировал»), сам список правлений целиком нигде не показывался.

async def _reign_end_narrative(session: AsyncSession, reign: ChampionReign, name_map: dict) -> str | None:
    """Короткий нарратив «как закончилось правление» — кто сверг и насколько
    драматично, переиспользует уже готовый match_report() на матче смены
    трона. Матч ищем по совпадению completed_at с ended_at правления — их
    связывает try_transfer_champion(), который передаёт at=match.completed_at
    ровно в этот момент. Если такого матча нет — трон освободился
    автоматически (scheduler.py, check_champion_auto_release, без боя)."""
    if reign.ended_at is None:
        return None
    m_r = await session.execute(
        select(Match).where(
            Match.is_boss_fight == True,  # noqa: E712
            Match.status == MatchStatus.completed,
            Match.completed_at == reign.ended_at,
            or_(Match.challenger_id == reign.player_id, Match.challenged_id == reign.player_id),
            Match.winner_id != reign.player_id,
        ).limit(1)
    )
    m = m_r.scalar_one_or_none()
    if m is None:
        return "Трон отошёл без боя — чемпион давно не защищался."
    winner_name = name_map.get(m.winner_id, "?")
    return f"Сверг {h(winner_name)}: {match_report(m, winner_name)}"


@router.callback_query(F.data.startswith("hall_of_fame"))
async def show_hall_of_fame(callback: CallbackQuery, session: AsyncSession):
    try:
        page = int(callback.data.rsplit("_", 1)[-1])
    except ValueError:
        page = 0  # старая кнопка без номера страницы (до пагинации, v2.116.0)

    await callback.answer()

    reigns_r = await session.execute(
        select(ChampionReign).order_by(desc(ChampionReign.started_at))
    )
    reigns = reigns_r.scalars().all()

    if not reigns:
        await callback.message.edit_text(
            "🏛 <b>Зал славы</b>\n\nБоссфайт за 1-е место ещё ни разу не активировался.",
            reply_markup=back_to_leaderboard_kb(),
        )
        return

    players_r = await session.execute(select(Player))
    name_map = {p.id: p.display_name for p in players_r.scalars().all()}

    total_pages = max(1, (len(reigns) + HALL_OF_FAME_PAGE_SIZE - 1) // HALL_OF_FAME_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = reigns[page * HALL_OF_FAME_PAGE_SIZE:(page + 1) * HALL_OF_FAME_PAGE_SIZE]

    # Шапка с яркими фактами — сколько всего было смен трона + самое короткое
    # правление (та же метрика, что и в «Рекордах клуба», здесь — для контекста
    # прямо над списком, чтобы не заставлять читателя листать список самому).
    # Считается по ПОЛНОМУ списку правлений, не по странице.
    header_facts = [f"Смен трона: <b>{len(reigns)}</b>"]
    short_reign = await shortest_champion_reign(session)
    if short_reign is not None:
        short_pid, short_days = short_reign
        short_str = "меньше дня" if short_days == 0 else pluralize_days(short_days)
        header_facts.append(
            f"Самое короткое правление: <b>{h(name_map.get(short_pid, '?'))}</b> ({short_str})"
        )

    lines = [
        f"🏛 <b>Зал славы</b>  <i>(стр. {page + 1}/{total_pages})</i>",
        "  •  ".join(header_facts), "",
    ]
    for reign in chunk:
        name = h(name_map.get(reign.player_id, "?"))
        start_str = reign.started_at.strftime("%d.%m.%y")
        if reign.ended_at is None:
            lines.append(f"👑 <b>{name}</b> — сейчас  <i>(с {start_str})</i>")
        else:
            end_str = reign.ended_at.strftime("%d.%m.%y")
            days = (reign.ended_at - reign.started_at).days
            days_str = "меньше дня" if days == 0 else pluralize_days(days)
            lines.append(f"👑 <b>{name}</b> — {start_str} – {end_str}  <i>({days_str})</i>")
            narrative = await _reign_end_narrative(session, reign, name_map)
            if narrative:
                lines.append(f"<i>{narrative}</i>")
        lines.append("")

    await callback.message.edit_text(
        "\n".join(lines).rstrip(),
        reply_markup=hall_of_fame_kb(page, total_pages),
    )
