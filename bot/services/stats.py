"""Вычисление статистики игрока — общее для личной статистики, публичного
профиля (bot/handlers/profile.py) и еженедельного дайджеста (bot/scheduler.py).

Вынесено из profile.py в сервисный модуль: scheduler.py импортировал эти
функции напрямую из хендлера, что смешивало слои (хендлер — не сервис).
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

from bot.services.achievements import ACHIEVEMENTS_MAP, get_achievements
from bot.utils import match_rating_delta


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
