"""Вычисление статистики игрока — общее для личной статистики, публичного
профиля (bot/handlers/profile.py) и еженедельного дайджеста (bot/scheduler.py).

Вынесено из profile.py в сервисный модуль: scheduler.py импортировал эти
функции напрямую из хендлера, что смешивало слои (хендлер — не сервис).
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape as h

from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.achievements import ACHIEVEMENTS_MAP, get_achievements
from bot.services.personal_records import get_personal_records_count
from bot.utils import (
    activity_counts_by_day,
    get_career_matches,
    match_rating_delta,
    pluralize_losses,
    pluralize_matches,
    pluralize_times,
)


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
    career_points = 0
    comeback_wins = 0
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
                # Очки, набранные в этой партии — с перспективы игрока (для
                # вех «Копил по очку» и т.д., см. _career_points_and_sets в achievements.py)
                i_am_favored = i_am_ch if m.winner_id is None else i_am_winner
                career_points += s["w"] if i_am_favored else s["l"]
                # Дьюс: партия дошла до 10:10+ и выиграна с отрывом ровно 2
                if max(s["w"], s["l"]) >= 12 and abs(s["w"] - s["l"]) == 2:
                    deuce_total += 1
                    if won_this_set:
                        deuce_won += 1
            # Камбэк (для радара стиля, v2.121.0) — победил, проиграв первые
            # ДВЕ партии. sets_data уже в перспективе победителя, а победитель
            # тут — сам игрок, поэтому s["w"]/s["l"] читаются напрямую, без
            # инверсии (тот же критерий, что у match_report() в utils.py).
            if i_am_winner and len(m.sets_data) >= 2:
                s0, s1 = m.sets_data[0], m.sets_data[1]
                if s0["w"] < s0["l"] and s1["w"] < s1["l"]:
                    comeback_wins += 1

    # Незакрытые долги (v2.121.0) — соперники, чей САМЫЙ ПОСЛЕДНИЙ матч против
    # игрока был поражением (и с тех пор реванша не было). all_matches уже
    # отсортирован desc(completed_at), поэтому первое попадание каждого
    # соперника в цикле — это и есть его самый недавний матч с игроком.
    opp_stats: dict[int, dict] = {}
    unresolved_debts: list[str] = []
    seen_recent: set[int] = set()
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
        if opp.id not in seen_recent:
            seen_recent.add(opp.id)
            if m.winner_id is not None and m.winner_id != player.id:
                unresolved_debts.append(opp.display_name)

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

    # Стабильность (для радара стиля, v2.121.0) — тот же принцип, что у
    # steadiest_career() в utils.py (клубный рекорд), но за карьеру ОДНОГО
    # игрока и уже переведён в шкалу 0-100: средняя «холостая» амплитуда
    # рейтинга за матч (abs_total - abs(net)) / count — чем ближе к 0, тем
    # ровнее игра (колебания туда-сюда гасят друг друга меньше). Эмпирический
    # коэффициент 3 подобран так, чтобы типичный разброс (~15-20 pts) не
    # сразу обнулял шкалу — не более точная наука, чем другие пороги в файле.
    stability_score = 100.0
    if rated:
        abs_total = sum(abs(d) for d in deltas)
        net = abs(sum(deltas))
        volatility = (abs_total - net) / len(deltas)
        stability_score = max(0.0, 100.0 - volatility * 3)

    # Стрик активности (v2.121.0) — сколько дней ПОДРЯД (МСК) заканчивая
    # последним днём с матчем игрок выходил к столу. Не про победы (это уже
    # streak/best_streak выше) — про сам факт игры изо дня в день.
    activity_streak_days = 0
    day_counts = activity_counts_by_day(all_matches)
    if day_counts:
        d = max(day_counts)
        while d in day_counts:
            activity_streak_days += 1
            d -= timedelta(days=1)

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

    # ── «Умные советы» v2.108.0 — персональные инсайты из уже собранной
    # истории, без новой инфраструктуры. Каждый показывается только при
    # достаточной выборке — тот же принцип порогов, что у остальных пунктов
    # этой функции (top_opp>=2, диплом от 5 ничьих и т.д.).

    # Счастливый день — винрейт по дню недели (НЕ путать с best_day выше,
    # тот про самый ИГРАЕМЫЙ день, этот — про самый ПОБЕДНЫЙ)
    day_wins: dict[int, int] = {}
    day_totals: dict[int, int] = {}
    for m in all_matches:
        if not m.completed_at:
            continue
        wd = m.completed_at.weekday()
        day_totals[wd] = day_totals.get(wd, 0) + 1
        if m.winner_id == player.id:
            day_wins[wd] = day_wins.get(wd, 0) + 1
    lucky_day = None
    best_wr = -1.0
    for wd, total in day_totals.items():
        if total < 3:
            continue
        wr = day_wins.get(wd, 0) / total
        if wr > best_wr:
            best_wr = wr
            lucky_day = (_day_names[wd], int(round(wr * 100)))

    # Момент-анализ — как играешь в матче СРАЗУ ПОСЛЕ поражения
    matches_chrono = list(reversed(all_matches))  # all_matches — desc(completed_at)
    after_loss_wins = after_loss_total = 0
    for i in range(1, len(matches_chrono)):
        prev, cur = matches_chrono[i - 1], matches_chrono[i]
        if prev.winner_id is not None and prev.winner_id != player.id:
            after_loss_total += 1
            if cur.winner_id == player.id:
                after_loss_wins += 1
    post_loss = (
        (int(round(after_loss_wins / after_loss_total * 100)), after_loss_total)
        if after_loss_total >= 3 else None
    )

    # Любимый счёт — самый частый точный счёт ВЫИГРАННОЙ партии (с перспективы игрока)
    won_set_scores: Counter = Counter()
    for m in all_matches:
        if not m.sets_data:
            continue
        i_am_ch = m.challenger_id == player.id
        i_am_winner = m.winner_id == player.id
        i_am_favored = i_am_ch if m.winner_id is None else i_am_winner
        for s in m.sets_data:
            won_this_set = (
                (i_am_ch and s["w"] > s["l"]) or (not i_am_ch and s["l"] > s["w"])
                if m.winner_id is None else
                (i_am_winner and s["w"] > s["l"]) or (not i_am_winner and s["l"] > s["w"])
            )
            if won_this_set:
                my_score = (s["w"], s["l"]) if i_am_favored else (s["l"], s["w"])
                won_set_scores[my_score] += 1
    favorite_score = None
    if won_set_scores:
        (fw, fl), cnt = won_set_scores.most_common(1)[0]
        if cnt >= 2:
            favorite_score = (f"{fw}:{fl}", cnt)

    # Спринтер vs марафонец — винрейт в коротких (1 партия) vs длинных (3+) матчах
    short_wins = short_total = long_wins = long_total = 0
    for m in all_matches:
        if not m.sets_data:
            continue
        n = len(m.sets_data)
        won = m.winner_id == player.id
        if n == 1:
            short_total += 1
            short_wins += won
        elif n >= 3:
            long_total += 1
            long_wins += won
    style_insight = None
    if short_total >= 3 and long_total >= 3:
        short_wr = short_wins / short_total * 100
        long_wr = long_wins / long_total * 100
        if abs(short_wr - long_wr) >= 15:
            style_insight = (
                ("sprinter", round(short_wr), round(long_wr)) if short_wr > long_wr
                else ("marathoner", round(long_wr), round(short_wr))
            )

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
        "first_set_wins": first_set_wins,
        "fav_format": fav_format,
        "best_day": best_day, "best_day_count": best_day_count,
        "beaten_opponents_count": beaten_opponents_count,
        "boss_fights_played": len(boss_fights), "boss_fights_won": boss_fight_wins,
        "trend_30d": trend_30d, "trend_30d_matches": len(recent_30),
        "deuce_total": deuce_total, "deuce_won": deuce_won,
        "sets_won": sets_won, "career_points": career_points,
        "lucky_day": lucky_day, "post_loss": post_loss,
        "favorite_score": favorite_score, "style_insight": style_insight,
        "comeback_wins": comeback_wins, "unresolved_debts": unresolved_debts,
        "stability_score": round(stability_score), "activity_streak_days": activity_streak_days,
    }


# ── Радар личного стиля (v2.121.0) ──────────────────────────────────────────────

def _build_style_radar(s: dict) -> dict[str, float] | None:
    """4 оси личного стиля для радар-графика: винрейт, доля партий на дьюсе,
    доля побед-камбэков среди всех побед, стабильность. None, если ещё не
    сыграно ни одной партии — рисовать радар не из чего."""
    if s["total_sets_played"] == 0:
        return None
    deuce_rate = round(s["deuce_total"] / s["total_sets_played"] * 100)
    comeback_rate = round(s["comeback_wins"] / s["wins"] * 100) if s["wins"] else 0
    return {
        "Винрейт": float(s["win_rate"]),
        "На дьюсе": float(deuce_rate),
        "Камбэки": float(comeback_rate),
        "Стабильность": float(s["stability_score"]),
    }


def _build_style_narrative(radar: dict[str, float]) -> str | None:
    """Абзац-репортаж, переводящий 4 сухих процента радара в понятный текст —
    по прямой просьбе пользователя после живого скриншота (проценты сами по
    себе ничего не объясняют, особенно «Стабильность» — не доля чего-то
    конкретного, а синтетический балл). Показывает только заметные сигналы,
    как «Моя история» — не все 4 оси разом, если нечего сказать."""
    parts = []

    wr = radar["Винрейт"]
    if wr >= 60:
        parts.append(f"Ты чаще побеждаешь, чем проигрываешь — винрейт {round(wr)}%.")
    elif wr < 40:
        parts.append(f"Победы даются нелегко — винрейт {round(wr)}%.")
    else:
        parts.append(f"Играешь примерно на равных — винрейт {round(wr)}%.")

    deuce = radar["На дьюсе"]
    if deuce >= 25:
        parts.append("Почти каждая четвёртая партия — на нервах, до дьюса.")
    elif deuce < 10:
        parts.append("Партии редко доходят до дьюса — либо разгром, либо тебя разгромили.")

    comeback = radar["Камбэки"]
    if comeback >= 20:
        parts.append("Каждая пятая победа — камбэк с 0:2, характер не подводит.")
    elif comeback == 0:
        parts.append("Камбэков пока не случалось — если ведут 0:2, обычно так и остаётся.")

    stability = radar["Стабильность"]
    if stability >= 80:
        parts.append("Рейтинг двигается ровно, без резких качелей.")
    elif stability < 40:
        parts.append("Рейтинг штормит — взлёты и падения через игру.")

    return " ".join(parts) if parts else None


# Правила архетипа — (ось, порог, направление "low", название). Направление
# "low" (True) означает, что архетип срабатывает при значении НИЖЕ порога
# (сейчас только «Американские горки» — низкая стабильность), иначе выше.
# Название взято от игрока, эмодзи не добавлены намеренно: у «Американские
# горки» уже есть устоявшийся 🎢 в дайджестах (bot/scheduler.py) — используем
# тот же термин для узнаваемости; у остальных четырёх готового эмодзи в
# проекте нет, придумывать новый не стали, чтобы не плодить несогласованные
# иконки.
_ARCHETYPE_RULES: list[tuple[str, float, bool, str]] = [
    ("Винрейт", 60.0, False, "Терминатор"),
    ("На дьюсе", 25.0, False, "Нервы стальные"),
    ("Камбэки", 20.0, False, "Феникс"),
    ("Стабильность", 80.0, False, "Метроном"),
    ("Стабильность", 40.0, True, "🎢 Американские горки"),
]


def _style_archetype(radar: dict[str, float]) -> str | None:
    """Титул по самой ярко выраженной оси радара — та, что дальше всех ушла
    за свой порог. None, если ни одна ось не выделяется (все в среднем
    диапазоне) — тишина лучше натянутого ярлыка."""
    best_label: str | None = None
    best_margin = -1.0
    for axis, threshold, is_low, label in _ARCHETYPE_RULES:
        value = radar[axis]
        margin = (threshold - value) if is_low else (value - threshold)
        if margin >= 0 and margin > best_margin:
            best_margin = margin
            best_label = label
    return best_label


# ── «Есть над чем поработать» (v2.125.0) ────────────────────────────────────────
# Зеркало _style_archetype выше — там ищем самую сильную черту, здесь самую
# слабую среди трёх кандидатов (дьюс/конвертация лидерства/стабильность).
# Показывается ОДНА строка — самая выраженная слабость, только если сэмпл
# достаточен и показатель заметно хуже нормы. Тишина, если ничего не проседает
# (тот же принцип, что у архетипа — не натягиваем вывод на пустом месте).

def _growth_area(s: dict) -> str | None:
    candidates: list[tuple[float, str]] = []

    if s["deuce_total"] >= 3:
        deuce_wr = s["deuce_won"] / s["deuce_total"] * 100
        if deuce_wr < 40:
            candidates.append((
                40 - deuce_wr,
                f"🎯 На дьюсе выигрываешь только {round(deuce_wr)}% партий — "
                f"можно потренировать нервы в концовке.",
            ))

    if s["first_set_wins"] >= 3 and s["first_set_conv"] is not None and s["first_set_conv"] < 50:
        candidates.append((
            50 - s["first_set_conv"],
            f"🎯 Выиграв первую партию, дожимаешь матч только в {s['first_set_conv']}% "
            f"случаев — есть над чем поработать.",
        ))

    if s["stability_score"] < 40:
        candidates.append((
            40 - s["stability_score"],
            "🎯 Рейтинг у тебя скачет сильнее обычного — стабильность обычно приходит с объёмом игр.",
        ))

    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]


# ── Индекс легенды (v2.127.0) ────────────────────────────────────────────────────
# Композитный балл декорированности карьеры — НЕ мера силы игрока (та уже видна
# отдельно как рейтинг): сколько трофеев накопил. Личные рекорды дороже всего
# (их всего 7 на игрока, ачивок 52+ — дешевле поштучно). Серия считается только
# от 2 побед подряд (max(0, best_streak-1)) — единичная «серия» не бонус.
# Показывается и на личной статистике, и на публичном профиле — в отличие от
# _growth_area (слабость показываем только себе), это чисто хвастовской стат,
# показать другим не стыдно.

def _legend_index(s: dict, achievements_count: int, personal_records_count: int, is_champion: bool) -> int:
    return (
        achievements_count * 3
        + personal_records_count * 5
        + s["boss_fights_won"] * 4
        + max(0, s["best_streak"] - 1) * 2
        + (10 if is_champion else 0)
    )


async def _legend_indices(session: AsyncSession, players: list) -> dict[int, int]:
    """Индекс легенды для ВСЕХ переданных игроков разом — нужен для позиции в
    клубе («#2 из 6») рядом с личным значением. Тот же класс стоимости, что у
    «Матрицы доминирования»/«Индекса формы» (полный скан клуба на каждом
    рендере) — на масштабе 5-7 игроков не проблема."""
    result: dict[int, int] = {}
    for p in players:
        # with_opponents=True: _compute_player_stats читает m.challenger/m.challenged
        # (opp.display_name для best_opp/nemesis) — без eager load это лишняя
        # неявная зависимость от того, что игроки клуба уже в identity map.
        matches = await get_career_matches(session, p.id, with_opponents=True)
        s = _compute_player_stats(p, matches)
        pr_count = await get_personal_records_count(session, p.id)
        result[p.id] = _legend_index(s, len(get_achievements(p)), pr_count, p.is_champion)
    return result


async def _legend_index_with_rank(session: AsyncSession, player, players_all: list) -> tuple[int, int, int]:
    """(индекс, место в клубе, всего игроков) для ОДНОГО игрока — обёртка над
    _legend_indices(), чтобы вызывающему (profile.py) не считать ранжирование
    самому."""
    indices = await _legend_indices(session, players_all)
    my_index = indices[player.id]
    ranked = sorted(indices.items(), key=lambda kv: -kv[1])
    my_rank = next(i for i, (pid, _) in enumerate(ranked, start=1) if pid == player.id)
    return my_index, my_rank, len(players_all)


# ── Career narrative (v2.112.0) ─────────────────────────────────────────────────
# «Моя история» показывала сухой список цифр — по просьбе пользователя добавлен
# абзац-репортаж перед ним, той же идеей, что match_report() (bot/utils.py):
# склейка готовых фраз по доступным сигналам, а не генерация с нуля. Сигналы —
# те же «умные советы» (lucky_day/post_loss/favorite_score/style_insight), что
# уже показываются на «Статистике» отдельными строками; здесь — связным текстом.

_DAY_LOCATIVE = {
    "Пн": "понедельникам", "Вт": "вторникам", "Ср": "средам", "Чт": "четвергам",
    "Пт": "пятницам", "Сб": "субботам", "Вс": "воскресеньям",
}

_OPENING_DOMINANT = [
    "На столе тебе почти нет равных: винрейт держится на {wr}%.",
    "Ты один из тех, с кем в клубе стараются не пересекаться лишний раз — {wr}% побед говорят сами за себя.",
    "{wr}% побед — ты играешь в клубе на своей волне, редко кто может ответить.",
]
_OPENING_EVEN = [
    "Ты играешь ровно — {wr}% побед, без резких взлётов и провалов.",
    "На столе примерно фифти-фифти: {wr}% побед, борьба идёт на равных.",
    "{wr}% побед — крепкий средний уровень, тут решает не рейтинг, а день.",
]
_OPENING_STRUGGLING = [
    "Пока побеждать получается реже, чем хотелось бы — {wr}% побед, но каждая тем ценнее.",
    "{wr}% побед — путь непростой, но ты продолжаешь выходить к столу.",
    "На столе сейчас тяжело — {wr}% побед, но кто не проигрывал, тот не играл.",
]


def _build_career_narrative(player, s: dict) -> str | None:
    """Абзац-репортаж по карьере игрока для «Моей истории» — вместо сухого
    списка. None при < 5 матчей: слишком мало данных для честного портрета,
    получилась бы либо пустая, либо натянутая на пустом месте фраза.
    """
    total = s["wins"] + s["draws"] + s["losses"]
    if total < 5:
        return None

    wr = s["win_rate"]
    pool = _OPENING_DOMINANT if wr >= 65 else _OPENING_EVEN if wr >= 45 else _OPENING_STRUGGLING
    parts = [pool[player.id % len(pool)].format(wr=wr)]

    if s["style_insight"]:
        style, own_wr, other_wr = s["style_insight"]
        if style == "sprinter":
            parts.append(f"Короткие матчи — твоя стихия: {own_wr}% побед против {other_wr}% в затяжных.")
        else:
            parts.append(f"Ты марафонец: {own_wr}% побед в длинных матчах против {other_wr}% в скоротечных.")

    if s["lucky_day"]:
        day, day_wr = s["lucky_day"]
        loc = _DAY_LOCATIVE.get(day, day)
        parts.append(f"Особенно удачно играется по {loc} — {day_wr}% побед именно в этот день.")

    if s["post_loss"]:
        pl_wr, pl_n = s["post_loss"]
        if pl_wr >= 50:
            parts.append(
                f"После поражений не раскисаешь — отыгрываешься в {pl_wr}% случаев "
                f"(из {pluralize_matches(pl_n)})."
            )
        else:
            parts.append(
                f"После поражений тяжеловато вернуться в колею — только {pl_wr}% побед в следующем матче "
                f"(из {pluralize_matches(pl_n)})."
            )

    if s["favorite_score"]:
        score, cnt = s["favorite_score"]
        parts.append(f"Любимый счёт партии — <b>{score}</b>, случался уже {pluralize_times(cnt)}.")

    nemesis = s["nemesis"]
    if nemesis and nemesis["losses"] >= 3:
        parts.append(
            f"Особый разговор — <b>{h(nemesis['name'])}</b>: "
            f"{pluralize_losses(nemesis['losses'])} от одного соперника, есть над чем поработать."
        )

    if s["boss_fights_played"] > 0:
        if player.is_champion:
            parts.append("И прямо сейчас держишь трон клуба — попробуй его отними.")
        elif s["boss_fights_won"] > 0:
            parts.append(f"В боссфайтах уже {s['boss_fights_won']}/{s['boss_fights_played']} — трон видел твою силу.")

    return " ".join(parts)


# ── Achievement progress ──────────────────────────────────────────────────────

def _progress_bar(ratio: float, width: int = 10) -> str:
    """Текстовый прогресс-бар из █/░ (v2.119.0) — обычные юникод-символы,
    Telegram рендерит их как простой текст, никакой спецподдержки не нужно
    (работает и в HTML parse_mode, и без него, на любом клиенте).

    int(), не round() — ratio всегда < 1.0 (вызывающий уже отфильтровал
    достигнутые цели), поэтому округление в большую сторону могло бы
    показать бар полностью заполненным для ещё не выполненной цели.
    """
    filled = max(0, min(width, int(ratio * width)))
    return "█" * filled + "░" * (width - filled)


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
    _add("workhorse", total_matches, 500, "матчей")
    _add("monument", total_matches, 750, "матчей")
    _add("superstar", total_matches, 1000, "матчей")
    _add("point_saver", s["career_points"], 4000, "очков")
    _add("sturdy_grinder", s["career_points"], 8000, "очков")
    _add("point_farmer", s["career_points"], 12000, "очков")
    _add("set_sniper", s["sets_won"], 200, "партий")
    _add("set_veteran", s["sets_won"], 500, "партий")
    _add("set_legend", s["sets_won"], 1000, "партий")
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
    ratio, text = max(valid, key=lambda x: x[0])
    return f"⏳ Цель: {text}\n{_progress_bar(ratio)}"
