import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from html import escape as h

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, Player

MSK_OFFSET = timedelta(hours=3)


def msk_day_start() -> datetime:
    """Начало текущего дня по МСК в naive-UTC (как хранятся даты в БД).

    Единая граница «сегодня» для экрана Сегодня, итогов дня и пасхалок —
    иначе день считался то по UTC (с 03:00 МСК), то по МСК.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    msk_midnight = (now + MSK_OFFSET).replace(hour=0, minute=0, second=0, microsecond=0)
    return msk_midnight - MSK_OFFSET


def _match_line(m: Match, player_id: int) -> str:
    """Форматирует одну строку матча для истории/статистики/дайджеста.

    Формат: иконка  дд.мм  vs Имя  счёт партий  (дельта)
    Счёт всегда показывается с перспективы player_id.
    """
    opponent = m.challenged if m.challenger_id == player_id else m.challenger
    is_draw = m.winner_id is None
    won = m.winner_id == player_id
    i_am_challenger = m.challenger_id == player_id
    icon = "🤝" if is_draw else ("✅" if won else "❌")
    date_str = m.completed_at.strftime("%d.%m") if m.completed_at else ""

    sets_str = ""
    if m.sets_data:
        parts = []
        for s in m.sets_data:
            if won or (is_draw and i_am_challenger):
                parts.append(f"{s['w']}:{s['l']}")
            else:
                parts.append(f"{s['l']}:{s['w']}")
        sets_str = "  " + ", ".join(parts)

    delta_str = ""
    if m.rating_change is not None:
        if is_draw:
            d = m.rating_change if i_am_challenger else -m.rating_change
            delta_str = f"  <i>({'+' if d >= 0 else ''}{d})</i>"
        elif won:
            delta_str = f"  <i>(+{m.rating_change})</i>"
        else:
            delta_str = f"  <i>(-{m.rating_change})</i>"

    return f"{icon} {date_str} vs {h(opponent.display_name)}{sets_str}{delta_str}"


def pluralize_matches(n: int) -> str:
    """1 матч / 2 матча / 5 матчей"""
    if 11 <= n % 100 <= 14:
        return f"{n} матчей"
    r = n % 10
    if r == 1:
        return f"{n} матч"
    if 2 <= r <= 4:
        return f"{n} матча"
    return f"{n} матчей"


async def get_player(session: AsyncSession, telegram_id: int) -> Player | None:
    r = await session.execute(select(Player).where(Player.telegram_id == telegram_id))
    return r.scalar_one_or_none()


# ── «Матч дня» — индекс драмы ────────────────────────────────────────────────

DRAMA_THRESHOLD = 8.0   # минимальный балл, чтобы матч мог стать «матчем дня»


def match_drama_score(m: Match) -> float:
    """Балл «драматичности» матча. Чем выше — тем эпичнее.

    Факторы: длина (число партий), дьюсы (партии за 11), концовка впритык
    (разница в 1 партию), камбэк (победитель проиграл стартовую партию),
    значимость по дельте рейтинга (апсет).
    """
    sets = m.sets_data or []
    if not sets:
        return 0.0
    n = len(sets)
    score = n * 2.0
    deuces = sum(1 for s in sets if min(s["w"], s["l"]) >= 10)
    score += deuces * 3.0
    w_sets = sum(1 for s in sets if s["w"] > s["l"])
    l_sets = n - w_sets
    if n >= 3 and abs(w_sets - l_sets) == 1:
        score += 4.0
    # Камбэк: победитель проиграл первую партию (только для побед, не ничьих).
    # sets_data для побед хранится в перспективе победителя (w = очки победителя).
    if m.winner_id is not None and sets[0]["w"] < sets[0]["l"]:
        score += 5.0
    if m.rating_change:
        score += min(abs(m.rating_change), 30.0) * 0.2
    return round(score, 1)


def match_drama_reason(m: Match) -> str:
    """Короткая авто-подпись «почему этот матч эпичный»."""
    sets = m.sets_data or []
    n = len(sets)
    deuces = sum(1 for s in sets if min(s["w"], s["l"]) >= 10)
    w_sets = sum(1 for s in sets if s["w"] > s["l"])
    l_sets = n - w_sets
    comeback = m.winner_id is not None and bool(sets) and sets[0]["w"] < sets[0]["l"]

    reasons: list[str] = []
    if m.winner_id is None:
        reasons.append("ничья в равной борьбе")
    if n >= 5:
        reasons.append(f"марафон на {n} партий")
    if comeback:
        reasons.append("камбэк после проигранного старта")
    if deuces >= 1:
        reasons.append("дьюсы" if deuces > 1 else "дьюс на тоненького")
    if m.winner_id is not None and (m.rating_change or 0) >= 20:
        reasons.append("апсет — фаворит повержен")
    if m.winner_id is not None and n >= 3 and abs(w_sets - l_sets) == 1 and not reasons:
        reasons.append("решилось в последней партии")
    if not reasons:
        # ничего «драматичного» не сработало — победа всухую
        reasons.append("уверенный разгром" if l_sets == 0 else "напряжённый матч")

    text = ", ".join(reasons)
    return text[0].upper() + text[1:]


def pick_match_of_day(matches: list[Match]) -> Match | None:
    """Выбирает самый драматичный матч из списка. None — если все слишком тривиальны."""
    scored = [(match_drama_score(m), m) for m in matches]
    scored = [(s, m) for s, m in scored if s >= DRAMA_THRESHOLD]
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1].completed_at or datetime.min), reverse=True)
    return scored[0][1]


def match_score_challenger_first(m: Match) -> str:
    """Счёт партий в перспективе challenger'а: 'challenger:challenged, ...'."""
    sets = m.sets_data or []
    if not sets:
        return ""
    # Победа: sets хранятся в перспективе победителя; ничья — в перспективе challenger.
    if m.winner_id is None or m.winner_id == m.challenger_id:
        return ", ".join(f"{s['w']}:{s['l']}" for s in sets)
    return ", ".join(f"{s['l']}:{s['w']}" for s in sets)


def _my_opp_points(m: Match, s: dict, viewer_id: int) -> tuple[int, int]:
    """Очки (мои, соперника) в партии s с перспективы viewer_id."""
    if m.winner_id is None:
        # ничья: sets хранятся в перспективе challenger
        if m.challenger_id == viewer_id:
            return s["w"], s["l"]
        return s["l"], s["w"]
    if m.winner_id == viewer_id:
        return s["w"], s["l"]
    return s["l"], s["w"]


def compute_h2h(matches: list[Match], viewer_id: int, opponent_id: int) -> dict:
    """Статистика личных встреч viewer против opponent.

    matches — завершённые матчи между этими двумя игроками,
    отсортированные по убыванию completed_at (свежие первыми).
    """
    wins = losses = draws = 0
    my_sets = opp_sets = 0
    rating_delta = 0.0
    best_win: float | None = None
    first_date = None

    for m in matches:
        if m.winner_id is None:
            draws += 1
        elif m.winner_id == viewer_id:
            wins += 1
        else:
            losses += 1

        for s in (m.sets_data or []):
            mp, op = _my_opp_points(m, s, viewer_id)
            if mp > op:
                my_sets += 1
            elif op > mp:
                opp_sets += 1

        d = match_rating_delta(m, viewer_id)
        rating_delta += d
        if m.winner_id == viewer_id and (best_win is None or d > best_win):
            best_win = d

        if m.completed_at and (first_date is None or m.completed_at < first_date):
            first_date = m.completed_at

    # Текущая серия в этом противостоянии (matches уже desc по дате)
    streak_desc: str | None = None
    if matches:
        latest = matches[0]
        if latest.winner_id == viewer_id:
            n = 0
            for m in matches:
                if m.winner_id == viewer_id:
                    n += 1
                else:
                    break
            if n >= 2:
                streak_desc = f"ты ведёшь — {n} побед подряд"
        elif latest.winner_id is not None:
            n = 0
            for m in matches:
                if m.winner_id is not None and m.winner_id != viewer_id:
                    n += 1
                else:
                    break
            if n >= 2:
                streak_desc = f"ты проигрываешь — {n} подряд"

    return {
        "total": len(matches),
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "my_sets": my_sets,
        "opp_sets": opp_sets,
        "rating_delta": round(rating_delta, 1),
        "best_win": round(best_win, 1) if best_win is not None else None,
        "first_date": first_date,
        "streak_desc": streak_desc,
    }


def match_rating_delta(match: Match, player_id: int) -> float:
    """Возвращает изменение рейтинга игрока в матче (+ или -).

    Для ничьей rating_change хранит challenger_delta (знаковый).
    Для победы/поражения rating_change всегда положительный.
    """
    if match.rating_change is None:
        return 0.0
    if match.winner_id is None:
        # ничья
        return match.rating_change if match.challenger_id == player_id else -match.rating_change
    return match.rating_change if match.winner_id == player_id else -match.rating_change


# ── График рейтинга (quickchart.io) ───────────────────────────────────────────

CHART_MAX_POINTS = 40  # сколько последних матчей показывать на графике


def build_rating_series(
    matches: list[Match], player_id: int, current_rating: float, limit: int = CHART_MAX_POINTS
) -> tuple[list[str], list[float]]:
    """Строит ряд рейтинга игрока для графика.

    matches — завершённые матчи игрока с rating_change, отсортированные по
    completed_at (старые первыми). Возвращает (labels, values), где values[i] —
    рейтинг ПОСЛЕ матча i. Ряд восстанавливается НАЗАД от current_rating через
    дельты: последняя точка точно равна текущему рейтингу, недавние точки точны.
    Пол рейтинга (1000/900) при откате не учитывается — это приближение, как и в
    ▲▼ лидерборда; для давних точек возможен небольшой дрейф.
    """
    recent = list(matches[-limit:]) if limit else list(matches)
    n = len(recent)
    values = [0.0] * n
    post = current_rating
    for i in range(n - 1, -1, -1):
        values[i] = round(post, 1)
        post -= match_rating_delta(recent[i], player_id)
    labels = [
        (m.completed_at.strftime("%d.%m") if m.completed_at else str(i + 1))
        for i, m in enumerate(recent)
    ]
    return labels, values


def rating_chart_url(name: str, labels: list[str], values: list[float]) -> str:
    """Формирует URL картинки графика рейтинга через quickchart.io.

    Картинку скачивает сам Telegram при send_photo(photo=url) — собственный
    HTTP-клиент не нужен. Конфиг — Chart.js (line), компактный JSON в query.
    """
    config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "Рейтинг",
                    "data": values,
                    "borderColor": "rgb(54,162,235)",
                    "backgroundColor": "rgba(54,162,235,0.15)",
                    "fill": True,
                    "tension": 0.3,
                    "pointRadius": 2,
                }
            ],
        },
        "options": {
            "title": {"display": True, "text": f"Рейтинг — {name}"},
            "legend": {"display": False},
        },
    }
    encoded = urllib.parse.quote(json.dumps(config, separators=(",", ":"), ensure_ascii=False))
    return f"https://quickchart.io/chart?w=700&h=420&bkg=white&c={encoded}"
