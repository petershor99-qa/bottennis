from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape as h

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Match, MatchStatus, Player
from bot.keyboards.inline import (
    achievements_kb,
    player_achievements_kb,
    player_profile_kb,
    stats_kb,
)
from bot.services.achievements import (
    ACHIEVEMENTS_LIST,
    ACHIEVEMENTS_MAP,
    CATEGORY_ORDER,
    get_achievements,
)
from bot.utils import (
    NEWCOMER_THRESHOLD,
    _match_line,
    boss_fight_rematch_blocked,
    compute_ranks,
    format_rank,
    get_active_match,
    get_champion_and_challenger,
    get_match_counts,
    get_player,
    match_rating_delta,
)

router = Router()


# ── Вычисление статистики игрока ──────────────────────────────────────────────

def _compute_player_stats(player, all_matches: list) -> dict:
    """Вычисляет полный набор статистических показателей игрока.

    all_matches — список завершённых матчей, sorted desc(completed_at).
    Возвращает словарь, используемый в show_my_stats и show_player_profile.
    """
    wins = sum(1 for m in all_matches if m.winner_id == player.id)
    draws = sum(1 for m in all_matches if m.winner_id is None)
    losses = len(all_matches) - wins - draws

    boss_fights = [m for m in all_matches if m.is_boss_fight]
    boss_fight_wins = sum(1 for m in boss_fights if m.winner_id == player.id)

    streak = 0
    for m in all_matches:
        if m.winner_id == player.id:
            streak += 1
        else:
            break

    loss_streak = 0
    for m in all_matches:
        if m.winner_id is not None and m.winner_id != player.id:
            loss_streak += 1
        else:
            break

    sets_won = sets_total = 0
    deuce_total = deuce_won = 0
    for m in all_matches:
        if m.sets_data:
            i_am_ch = m.challenger_id == player.id
            i_am_winner = m.winner_id == player.id
            for s in m.sets_data:
                sets_total += 1
                won_this_set = (
                    (i_am_ch and s["w"] > s["l"]) or (not i_am_ch and s["l"] > s["w"])
                    if m.winner_id is None else
                    (i_am_winner and s["w"] > s["l"]) or (not i_am_winner and s["l"] > s["w"])
                )
                if won_this_set:
                    sets_won += 1
                # Дьюс: партия дошла до 10:10+ и выиграна с отрывом ровно 2
                if max(s["w"], s["l"]) >= 12 and abs(s["w"] - s["l"]) == 2:
                    deuce_total += 1
                    if won_this_set:
                        deuce_won += 1

    opp_stats: dict[int, dict] = {}
    for m in all_matches:
        opp = m.challenged if m.challenger_id == player.id else m.challenger
        if opp.id not in opp_stats:
            opp_stats[opp.id] = {"name": opp.display_name, "wins": 0, "losses": 0, "draws": 0, "total": 0}
        opp_stats[opp.id]["total"] += 1
        if m.winner_id == player.id:
            opp_stats[opp.id]["wins"] += 1
        elif m.winner_id is None:
            opp_stats[opp.id]["draws"] += 1
        else:
            opp_stats[opp.id]["losses"] += 1

    rated = [m for m in all_matches if m.rating_change is not None]
    avg_delta = best_win = None
    total_earned = total_lost = 0.0
    if rated:
        deltas = [match_rating_delta(m, player.id) for m in rated]
        avg_delta = round(sum(deltas) / len(deltas), 1)
        win_deltas = [match_rating_delta(m, player.id) for m in rated if m.winner_id == player.id]
        best_win = max(win_deltas) if win_deltas else None
        total_earned = round(sum(d for d in deltas if d > 0), 1)
        total_lost = round(abs(sum(d for d in deltas if d < 0)), 1)

    week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    recent_7 = sorted(
        [m for m in all_matches if m.completed_at and m.completed_at >= week_ago],
        key=lambda m: m.completed_at,
    )

    # Тренд за 30 дней — отдельно от «за карьеру»: тот копится бесконечно и
    # не показывает, как дела ПРЯМО СЕЙЧАС.
    month_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    recent_30 = [m for m in all_matches if m.completed_at and m.completed_at >= month_ago]
    trend_30d = round(sum(match_rating_delta(m, player.id) for m in recent_30), 1) if recent_30 else None

    best_streak = cur_s = 0
    for m in reversed(all_matches):
        if m.winner_id == player.id:
            cur_s += 1
            best_streak = max(best_streak, cur_s)
        else:
            cur_s = 0

    total_sets_played = sum(len(m.sets_data) for m in all_matches if m.sets_data)

    first_set_wins = first_set_then_match_wins = 0
    for m in all_matches:
        if not m.sets_data:
            continue
        s0 = m.sets_data[0]
        i_am_winner = m.winner_id == player.id
        i_am_ch = m.challenger_id == player.id
        if m.winner_id is None:
            my_s0 = s0["w"] if i_am_ch else s0["l"]
            op_s0 = s0["l"] if i_am_ch else s0["w"]
        else:
            my_s0 = s0["w"] if i_am_winner else s0["l"]
            op_s0 = s0["l"] if i_am_winner else s0["w"]
        if my_s0 > op_s0:
            first_set_wins += 1
            if m.winner_id == player.id:
                first_set_then_match_wins += 1

    beaten_opponents_count = sum(1 for v in opp_stats.values() if v["wins"] > 0)

    format_counter = Counter(len(m.sets_data) for m in all_matches if m.sets_data)
    fav_format = format_counter.most_common(1)[0] if format_counter else None

    _day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    day_counter = Counter(m.completed_at.weekday() for m in all_matches if m.completed_at)
    if day_counter:
        _idx, best_day_count = day_counter.most_common(1)[0]
        best_day = _day_names[_idx]
    else:
        best_day, best_day_count = None, 0

    return {
        "wins": wins, "draws": draws, "losses": losses,
        "win_rate": int(wins / len(all_matches) * 100) if all_matches else 0,
        "streak": streak, "loss_streak": loss_streak,
        "sets_win_rate": int(sets_won / sets_total * 100) if sets_total else 0,
        "best_opp": max((v for v in opp_stats.values() if v["wins"] > 0), key=lambda x: x["wins"], default=None),
        "nemesis": max((v for v in opp_stats.values() if v["losses"] > 0), key=lambda x: x["losses"], default=None),
        "top_opp": max(opp_stats.values(), key=lambda x: x["total"], default=None),
        "avg_delta": avg_delta, "best_win": best_win,
        "total_earned": total_earned, "total_lost": total_lost,
        "recent_7": recent_7,
        "best_streak": best_streak,
        "total_sets_played": total_sets_played,
        "first_set_conv": int(first_set_then_match_wins / first_set_wins * 100) if first_set_wins else None,
        "fav_format": fav_format,
        "best_day": best_day, "best_day_count": best_day_count,
        "beaten_opponents_count": beaten_opponents_count,
        "boss_fights_played": len(boss_fights), "boss_fights_won": boss_fight_wins,
        "trend_30d": trend_30d, "trend_30d_matches": len(recent_30),
        "deuce_total": deuce_total, "deuce_won": deuce_won,
    }


# ── Общий рендер строк статистики ─────────────────────────────────────────────

def _render_stats_lines(player, s: dict) -> list[str]:
    """Формирует общие строки статистики (форма, серии, соперники, рекорды и т.д.).

    Используется и в личной статистике, и в публичном профиле. Возвращает список
    строк без заголовка и без блока «Последние матчи» — их добавляет вызывающий.

    Строки сгруппированы по смыслу (форма/серии → соперники → рейтинг/рекорды →
    разное), каждая непустая группа отделена пустой строкой — те же принципы, что
    и у группировки «Рекорды клуба» (bot/handlers/leaderboard.py, v2.73.0): плоский
    список из 10+ разнородных пунктов подряд читается как нечитаемая простыня.
    """
    form_lines: list[str] = []
    opponent_lines: list[str] = []
    rating_lines: list[str] = []
    misc_lines: list[str] = []

    recent_7 = s["recent_7"]
    if recent_7:
        form_icons = []
        for m in recent_7:
            if m.winner_id is None:
                form_icons.append("🟡")
            elif m.winner_id == player.id:
                form_icons.append("🟢")
            else:
                form_icons.append("🔴")
        total_recent = len(form_icons)
        display_icons = form_icons[-10:]
        suffix = f"  <i>({total_recent} матчей)</i>" if total_recent > 10 else ""
        form_lines.append(f"🗓 Форма (7 дней): {''.join(display_icons)}{suffix}")

    streak = s["streak"]
    if streak >= 2:
        form_lines.append(f"🔥 Серия: <b>{streak} побед подряд</b>")
    if s["loss_streak"] >= 2:
        form_lines.append(f"😬 Серия: <b>{s['loss_streak']} поражений подряд</b>")
    if s["best_streak"] >= 2 and s["best_streak"] != streak:
        form_lines.append(f"🎖 Рекорд серии: <b>{s['best_streak']} побед подряд</b>")

    if s["best_opp"]:
        opponent_lines.append(f"🎁 Подарок: <b>{h(s['best_opp']['name'])}</b> ({s['best_opp']['wins']} побед)")
    if s["nemesis"]:
        opponent_lines.append(f"😱 Кошмар: <b>{h(s['nemesis']['name'])}</b> ({s['nemesis']['losses']} поражений)")
    top_opp = s["top_opp"]
    if top_opp and top_opp["total"] >= 2:
        top_draws_str = f" 🤝{top_opp['draws']}" if top_opp["draws"] else ""
        opponent_lines.append(
            f"⚔️ Чаще всего: <b>{h(top_opp['name'])}</b> "
            f"({top_opp['total']} матчей, {top_opp['wins']}–{top_opp['losses']}{top_draws_str})"
        )

    if player.peak_rating and player.peak_rating > player.rating:
        rating_lines.append(f"📈 Пик рейтинга: <b>{round(player.peak_rating, 1)}</b> pts")
    if s["trend_30d"] is not None:
        sign = "+" if s["trend_30d"] >= 0 else ""
        rating_lines.append(
            f"📅 За 30 дней: <b>{sign}{s['trend_30d']} pts</b> ({s['trend_30d_matches']} матчей)"
        )
    avg_delta = s["avg_delta"]
    if avg_delta is not None:
        sign = "+" if avg_delta >= 0 else ""
        rating_lines.append(f"〽️ В среднем за матч: <b>{sign}{avg_delta} pts</b>")
    if s["best_win"] is not None:
        rating_lines.append(f"🏅 Лучший матч: <b>+{s['best_win']} pts</b>")
    if s["total_earned"] > 0 or s["total_lost"] > 0:
        rating_lines.append(f"💰 За карьеру: <b>+{s['total_earned']}</b> / <b>-{s['total_lost']}</b> pts")
    if s["boss_fights_played"] > 0:
        rating_lines.append(
            f"⚔️ Боссфайты: <b>{s['boss_fights_won']}/{s['boss_fights_played']}</b>"
        )

    if s["total_sets_played"] > 0:
        misc_lines.append(f"🎮 Партий сыграно: <b>{s['total_sets_played']}</b>")
    if s["deuce_total"] > 0:
        misc_lines.append(f"🎢 Партий на дьюсе: <b>{s['deuce_total']}</b> (выиграно {s['deuce_won']})")
    if s["first_set_conv"] is not None:
        misc_lines.append(f"⚡ После 1-й партии: <b>{s['first_set_conv']}%</b> побед")
    if s["fav_format"]:
        n = s["fav_format"][0]
        word = "партия" if n == 1 else "партии" if 2 <= n <= 4 else "партий"
        misc_lines.append(f"❤️ Любимый формат: <b>{n} {word}</b>")
    if s["best_day"]:
        misc_lines.append(f"📅 Активный день: <b>{s['best_day']}</b> ({s['best_day_count']} матчей)")

    lines: list[str] = []
    for group in (form_lines, opponent_lines, rating_lines, misc_lines):
        if group:
            if lines:
                lines.append("")
            lines.extend(group)
    return lines


# ── Разрыв до соседей по таблице / до трона ───────────────────────────────────

def _rank_gap_line(player: Player, players_all: list, ranks: dict[int, int]) -> str | None:
    """«До следующего места» — разрыв в рейтинге до игрока рангом выше.

    При пиннинге чемпиона (боссфайт — #1 не пересчитывается на лету) ранг НЕ
    всегда соответствует сырому рейтингу: игрок рангом выше может иметь более
    низкий сырой рейтинг, чем ты (см. фикс сортировки списка вызова, v2.93.0).
    Если разрыв получается <= 0 — не показываем строку, чтобы не путать
    отрицательным «разрывом» (тебя обгоняют по позиции не по очкам, а по трону).
    """
    my_rank = ranks.get(player.id)
    if not my_rank or my_rank <= 1:
        return None
    prev = next((p for p in players_all if ranks.get(p.id) == my_rank - 1), None)
    if not prev:
        return None
    gap = round(prev.rating - player.rating, 1)
    if gap <= 0:
        return None
    return f"📶 До #{my_rank - 1} (<b>{h(prev.display_name)}</b>): −{gap} pts"


async def _throne_distance_line(session: AsyncSession, player: Player, total_matches: int) -> str | None:
    """«До трона» — статус игрока в боссфайт-механике: сколько не хватает
    рейтинга/матчей до претендентства, либо призыв вызвать чемпиона, если
    претендент — уже сам игрок. None, если фича боссфайта не активирована
    (чемпион не назначен) или игрок сам чемпион (highlander уже это отражает).
    """
    champion, challenger_player = await get_champion_and_challenger(session)
    if champion is None or champion.id == player.id:
        return None
    if challenger_player is not None and challenger_player.id == player.id:
        return "🗡 Ты претендент — вызови чемпиона на босс-файт!"
    if player.rating <= champion.rating:
        gap = round(champion.rating - player.rating, 1)
        return f"👑 До трона: −{gap} pts"
    # Рейтинг уже выше чемпиона — дело за порогом матчей или за тем, что
    # претендентское место сейчас занято кем-то ещё с рейтингом выше.
    if total_matches < NEWCOMER_THRESHOLD:
        left = NEWCOMER_THRESHOLD - total_matches
        return f"🗡 До статуса претендента: рейтинг уже выше чемпиона, не хватает матчей ({left})"
    if challenger_player is not None:
        gap = round(challenger_player.rating - player.rating, 1)
        if gap > 0:
            return (
                f"🗡 До статуса претендента: −{gap} pts "
                f"(сейчас впереди <b>{h(challenger_player.display_name)}</b>)"
            )
    return None


# ── Achievement progress ──────────────────────────────────────────────────────

def _nearest_achievement_progress(player, s: dict, total_players: int) -> str | None:
    """Возвращает строку прогресса до ближайшей незаработанной счётной ачивки или None."""
    earned = set(get_achievements(player))
    total_matches = s["wins"] + s["draws"] + s["losses"]
    if total_matches == 0:
        return None
    streak = s["streak"]
    candidates: list[tuple[float, str]] = []

    def _add(ach_id: str, current: int, target: int, unit: str, ratio: float | None = None) -> None:
        if ach_id in earned:
            return
        a = ACHIEVEMENTS_MAP.get(ach_id)
        if not a:
            return
        if ratio is None:
            ratio = current / target
        candidates.append((ratio, f"{a.emoji} {a.name}: {current}/{target} {unit}"))

    if streak > 0:
        _add("hat_trick", streak, 3, "побед подряд")
        _add("im_on_fire", streak, 5, "побед подряд")
        _add("god_mode", streak, 10, "побед подряд")
    _add("fifty", total_matches, 50, "матчей")
    _add("veteran", total_matches, 100, "матчей")
    _add("legend", total_matches, 200, "матчей")
    if s["draws"] > 0:
        _add("diplomat", s["draws"], 5, "ничьих")
    opp_count = max(total_players - 1, 1)
    _add("collector", s["beaten_opponents_count"], opp_count, "соперников")
    # Прогресс рейтинга считаем от стартовой 1000, а не от нуля: иначе ratio
    # 1000/1200 = 0.83 почти всегда побеждает и цель «Рейтинг 1200» вытесняет все остальные.
    _add(
        "rating_1200", int(player.rating), 1200, "pts рейтинга",
        ratio=(player.rating - 1000.0) / 200.0,
    )

    valid = [(r, t) for r, t in candidates if r < 1.0]
    if not valid:
        return None
    _, text = max(valid, key=lambda x: x[0])
    return f"⏳ Цель: {text}"


# ── My stats ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_stats")
async def show_my_stats(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return

    await callback.answer()

    players_all = (await session.execute(select(Player))).scalars().all()
    champion = next((p for p in players_all if p.is_champion), None)
    ranks = compute_ranks(
        players_all, await get_match_counts(session),
        champion_id=champion.id if champion else None,
    )
    rank_str = format_rank(ranks, player.id)

    all_r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
            Match.status == MatchStatus.completed,
        )
        .order_by(desc(Match.completed_at))
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    all_matches = all_r.scalars().all()

    if not all_matches:
        await callback.message.edit_text(
            f"📈 <b>Статистика — {h(player.display_name)}</b>\n\n"
            f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — {rank_str}\n\n"
            f"Ты ещё не сыграл ни одного матча.\nВызови кого-нибудь! 🏓",
            reply_markup=stats_kb(),
        )
        return

    matches = all_matches[:5]
    s = _compute_player_stats(player, all_matches)

    draws_part = f"  |  🤝 Ничьих: <b>{s['draws']}</b>" if s["draws"] > 0 else ""
    lines = [
        f"📈 <b>Статистика — {h(player.display_name)}</b>\n",
        f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — {rank_str}",
        f"🏆 Побед: <b>{s['wins']}</b>{draws_part}  |  💔 Поражений: <b>{s['losses']}</b>",
        f"📊 Матчи: <b>{s['win_rate']}%</b>  |  🎯 Партии: <b>{s['sets_win_rate']}%</b>",
    ]

    lines.extend(_render_stats_lines(player, s))

    rank_gap = _rank_gap_line(player, players_all, ranks)
    if rank_gap:
        lines.append(rank_gap)
    throne_line = await _throne_distance_line(session, player, s["wins"] + s["draws"] + s["losses"])
    if throne_line:
        lines.append(throne_line)

    progress = _nearest_achievement_progress(player, s, len(players_all))
    if progress:
        lines.append(progress)

    if matches:
        lines.append("\n<b>Последние матчи:</b>")
        for m in matches:
            lines.append(_match_line(m, player.id))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=stats_kb(),
    )


# ── Player profile (public view) ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("player_profile_"))
async def show_player_profile(callback: CallbackQuery, session: AsyncSession):
    try:
        target_id = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    tp_r = await session.execute(select(Player).where(Player.id == target_id))
    player = tp_r.scalar_one_or_none()
    if not player:
        await callback.answer("Игрок не найден.", show_alert=True)
        return

    await callback.answer()

    viewer = await get_player(session, callback.from_user.id)
    viewer_id = viewer.id if viewer else None

    # Кнопку «Вызвать» скрываем, если занят зритель ИЛИ владелец профиля —
    # у игрока может быть только один активный матч одновременно (не только
    # именно с этим человеком). Кнопка «Личные встречи» (read-only) показывается
    # всегда для чужого профиля.
    can_challenge = True
    if viewer and viewer.id != player.id:
        if (
            await get_active_match(session, viewer.id)
            or await get_active_match(session, player.id)
            or await boss_fight_rematch_blocked(session, viewer.id, player.id)
        ):
            can_challenge = False

    players_all = (await session.execute(select(Player))).scalars().all()
    champion = next((p for p in players_all if p.is_champion), None)
    ranks = compute_ranks(
        players_all, await get_match_counts(session),
        champion_id=champion.id if champion else None,
    )
    rank_str = format_rank(ranks, player.id)

    all_r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
            Match.status == MatchStatus.completed,
        )
        .order_by(desc(Match.completed_at))
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    all_matches = all_r.scalars().all()
    matches = all_matches[:5]

    s = _compute_player_stats(player, all_matches)

    draws_part = f"  |  🤝 Ничьих: <b>{s['draws']}</b>" if s["draws"] > 0 else ""
    lines = [
        f"👤 <b>{h(player.display_name)}</b>\n",
        f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — {rank_str}",
        f"🏆 Побед: <b>{s['wins']}</b>{draws_part}  |  💔 Поражений: <b>{s['losses']}</b>",
        f"📊 Винрейт: <b>{s['win_rate']}%</b>",
    ]

    lines.extend(_render_stats_lines(player, s))

    rank_gap = _rank_gap_line(player, players_all, ranks)
    if rank_gap:
        lines.append(rank_gap)
    throne_line = await _throne_distance_line(session, player, s["wins"] + s["draws"] + s["losses"])
    if throne_line:
        lines.append(throne_line)

    if matches:
        lines.append("\n<b>Последние матчи:</b>")
        for m in matches:
            lines.append(_match_line(m, player.id))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=player_profile_kb(player.id, viewer_id=viewer_id, can_challenge=can_challenge),
    )


# ── Achievements ──────────────────────────────────────────────────────────────

def _render_achievements(earned_ids: list[str], title: str) -> str:
    """Формирует текст экрана достижений, сгруппированный по категориям.

    Внутри каждой категории — сначала полученные (✅, в порядке ACHIEVEMENTS_LIST),
    потом невыполненные (🔒). Плоский список 30+ пунктов подряд читается как
    нечитаемая простыня — тот же принцип, что уже применён к «Статистике»
    (_render_stats_lines) и «Рекордам клуба» (leaderboard.py, v2.73.0).
    """
    total = len(ACHIEVEMENTS_LIST)
    earned_set = set(earned_ids)
    count = len([a for a in ACHIEVEMENTS_LIST if a.id in earned_set])
    lines = [f"🏅 <b>{title}</b>  ({count} из {total})"]

    by_category: dict[str, list] = {}
    for a in ACHIEVEMENTS_LIST:
        by_category.setdefault(a.category, []).append(a)

    for category in CATEGORY_ORDER:
        achs = by_category.get(category, [])
        if not achs:
            continue
        lines.append(f"\n<b>{category}</b>")
        for a in sorted(achs, key=lambda a: a.id not in earned_set):
            if a.id in earned_set:
                lines.append(f"✅ {a.emoji} <b>{a.name}</b> — <i>{a.desc}</i>")
            elif a.hidden:
                lines.append("🔒 ???")
            else:
                lines.append(f"🔒 {a.emoji} {a.name} — <i>{a.desc}</i>")
    return "\n".join(lines)


@router.callback_query(F.data == "my_achievements")
async def show_my_achievements(callback: CallbackQuery, session: AsyncSession):
    player = await get_player(session, callback.from_user.id)
    if not player:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return
    await callback.answer()
    earned = get_achievements(player)
    text = _render_achievements(earned, "Мои достижения")
    await callback.message.edit_text(text, reply_markup=achievements_kb())


@router.callback_query(F.data.startswith("player_achievements_"))
async def show_player_achievements(callback: CallbackQuery, session: AsyncSession):
    try:
        target_id = int(callback.data.removeprefix("player_achievements_"))
    except ValueError:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    tp_r = await session.execute(select(Player).where(Player.id == target_id))
    player = tp_r.scalar_one_or_none()
    if not player:
        await callback.answer("Игрок не найден.", show_alert=True)
        return
    await callback.answer()
    earned = get_achievements(player)
    text = _render_achievements(earned, f"Достижения — {h(player.display_name)}")
    await callback.message.edit_text(
        text,
        reply_markup=player_achievements_kb(target_id),
    )
