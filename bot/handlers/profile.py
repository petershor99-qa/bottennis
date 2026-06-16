from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape as h

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Match, MatchStatus, Player
from bot.keyboards.inline import (
    achievements_kb,
    player_achievements_kb,
    player_profile_kb,
    stats_kb,
)
from bot.services.achievements import ACHIEVEMENTS_LIST, ACHIEVEMENTS_MAP, get_achievements
from bot.utils import _match_line, get_player, match_rating_delta

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
    for m in all_matches:
        if m.sets_data:
            i_am_ch = m.challenger_id == player.id
            i_am_winner = m.winner_id == player.id
            for s in m.sets_data:
                sets_total += 1
                if m.winner_id is None:
                    if (i_am_ch and s["w"] > s["l"]) or (not i_am_ch and s["l"] > s["w"]):
                        sets_won += 1
                else:
                    if (i_am_winner and s["w"] > s["l"]) or (not i_am_winner and s["l"] > s["w"]):
                        sets_won += 1

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
    }


# ── Общий рендер строк статистики ─────────────────────────────────────────────

def _render_stats_lines(player, s: dict) -> list[str]:
    """Формирует общие строки статистики (форма, серии, соперники, рекорды и т.д.).

    Используется и в личной статистике, и в публичном профиле. Возвращает список
    строк без заголовка и без блока «Последние матчи» — их добавляет вызывающий.
    """
    lines: list[str] = []

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
        lines.append(f"🗓 Форма (7 дней): {''.join(display_icons)}{suffix}")

    streak = s["streak"]
    if streak >= 2:
        lines.append(f"🔥 Серия: <b>{streak} побед подряд</b>")
    if s["loss_streak"] >= 2:
        lines.append(f"😬 Серия: <b>{s['loss_streak']} поражений подряд</b>")
    if s["best_opp"]:
        lines.append(f"🎁 Подарок: <b>{h(s['best_opp']['name'])}</b> ({s['best_opp']['wins']} побед)")
    if s["nemesis"]:
        lines.append(f"😱 Кошмар: <b>{h(s['nemesis']['name'])}</b> ({s['nemesis']['losses']} поражений)")
    top_opp = s["top_opp"]
    if top_opp and top_opp["total"] >= 2:
        top_draws_str = f" 🤝{top_opp['draws']}" if top_opp["draws"] else ""
        lines.append(
            f"⚔️ Чаще всего: <b>{h(top_opp['name'])}</b> "
            f"({top_opp['total']} матчей, {top_opp['wins']}–{top_opp['losses']}{top_draws_str})"
        )
    avg_delta = s["avg_delta"]
    if avg_delta is not None:
        sign = "+" if avg_delta >= 0 else ""
        lines.append(f"〽️ В среднем за матч: <b>{sign}{avg_delta} pts</b>")
    if s["best_win"] is not None:
        lines.append(f"🏅 Лучший матч: <b>+{s['best_win']} pts</b>")
    if s["total_earned"] > 0 or s["total_lost"] > 0:
        lines.append(f"💰 За карьеру: <b>+{s['total_earned']}</b> / <b>-{s['total_lost']}</b> pts")
    if s["best_streak"] >= 2 and s["best_streak"] != streak:
        lines.append(f"🎖 Рекорд серии: <b>{s['best_streak']} побед подряд</b>")
    if s["total_sets_played"] > 0:
        lines.append(f"🎮 Партий сыграно: <b>{s['total_sets_played']}</b>")
    if s["first_set_conv"] is not None:
        lines.append(f"⚡ После 1-й партии: <b>{s['first_set_conv']}%</b> побед")
    if s["fav_format"]:
        n = s["fav_format"][0]
        word = "партия" if n == 1 else "партии" if 2 <= n <= 4 else "партий"
        lines.append(f"❤️ Любимый формат: <b>{n} {word}</b>")
    if s["best_day"]:
        lines.append(f"📅 Активный день: <b>{s['best_day']}</b> ({s['best_day_count']} матчей)")

    return lines


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

    rank_r = await session.execute(
        select(func.count()).select_from(Player).where(Player.rating > player.rating)
    )
    rank = rank_r.scalar() + 1
    total_r = await session.execute(select(func.count()).select_from(Player))
    total = total_r.scalar()

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
            f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — #{rank} из {total}\n\n"
            f"Ты ещё не сыграл ни одного матча.\nВызови кого-нибудь! 🏓",
            reply_markup=stats_kb(),
            parse_mode="HTML",
        )
        return

    matches = all_matches[:5]
    s = _compute_player_stats(player, all_matches)

    draws_part = f"  |  🤝 Ничьих: <b>{s['draws']}</b>" if s["draws"] > 0 else ""
    lines = [
        f"📈 <b>Статистика — {h(player.display_name)}</b>\n",
        f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — #{rank} из {total}",
        f"🏆 Побед: <b>{s['wins']}</b>{draws_part}  |  💔 Поражений: <b>{s['losses']}</b>",
        f"📊 Матчи: <b>{s['win_rate']}%</b>  |  🎯 Партии: <b>{s['sets_win_rate']}%</b>",
    ]

    lines.extend(_render_stats_lines(player, s))

    peak = player.peak_rating
    if peak and peak > player.rating:
        lines.append(f"📈 Пик рейтинга: <b>{round(peak, 1)}</b> pts")

    progress = _nearest_achievement_progress(player, s, total)
    if progress:
        lines.append(progress)

    if matches:
        lines.append("\n<b>Последние матчи:</b>")
        for m in matches:
            lines.append(_match_line(m, player.id))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=stats_kb(),
        parse_mode="HTML",
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

    # Кнопку «Вызвать» скрываем, если уже есть активный матч с этим игроком.
    # Кнопка «Личные встречи» (read-only) показывается всегда для чужого профиля.
    can_challenge = True
    if viewer and viewer.id != player.id:
        active_r = await session.execute(
            select(Match.id).where(
                or_(
                    and_(Match.challenger_id == viewer.id, Match.challenged_id == player.id),
                    and_(Match.challenger_id == player.id, Match.challenged_id == viewer.id),
                ),
                Match.status == MatchStatus.accepted,
            ).limit(1)
        )
        if active_r.scalar():
            can_challenge = False

    rank_r = await session.execute(
        select(func.count()).select_from(Player).where(Player.rating > player.rating)
    )
    rank = rank_r.scalar() + 1
    total_r = await session.execute(select(func.count()).select_from(Player))
    total = total_r.scalar()

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
        f"⭐ Рейтинг: <b>{round(player.rating, 1)}</b> pts — #{rank} из {total}",
        f"🏆 Побед: <b>{s['wins']}</b>{draws_part}  |  💔 Поражений: <b>{s['losses']}</b>",
        f"📊 Винрейт: <b>{s['win_rate']}%</b>",
    ]
    if player.peak_rating and player.peak_rating > player.rating:
        lines.append(f"📈 Пик рейтинга: <b>{round(player.peak_rating, 1)}</b> pts")

    lines.extend(_render_stats_lines(player, s))

    if matches:
        lines.append("\n<b>Последние матчи:</b>")
        for m in matches:
            lines.append(_match_line(m, player.id))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=player_profile_kb(player.id, viewer_id=viewer_id, can_challenge=can_challenge),
        parse_mode="HTML",
    )


# ── Achievements ──────────────────────────────────────────────────────────────

def _render_achievements(earned_ids: list[str], title: str) -> str:
    """Формирует текст экрана достижений."""
    total = len(ACHIEVEMENTS_LIST)
    count = len([a for a in ACHIEVEMENTS_LIST if a.id in earned_ids])
    lines = [f"🏅 <b>{title}</b>  ({count} из {total})\n"]
    earned_set = set(earned_ids)
    earned_achs = [a for a in ACHIEVEMENTS_LIST if a.id in earned_set]
    locked_achs = [a for a in ACHIEVEMENTS_LIST if a.id not in earned_set]
    for a in earned_achs:
        lines.append(f"✅ {a.emoji} <b>{a.name}</b> — <i>{a.desc}</i>")
    if locked_achs:
        lines.append("")
    for a in locked_achs:
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
    await callback.message.edit_text(text, reply_markup=achievements_kb(), parse_mode="HTML")


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
        parse_mode="HTML",
    )
