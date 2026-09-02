"""
Система достижений.

Каждое достижение:
  id    — строковый ключ
  emoji — иконка
  name  — название (с отсылками к играм/мемам)
  desc  — условие (показывается игроку)

Хранение: player.achievements — JSON-список заработанных id.
"""
import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AchievementEarned, Match, MatchStatus, Player
from bot.utils import MSK_OFFSET, as_naive, msk_day_start, msk_hour_and_weekday, rating_tenths


@dataclass
class Achievement:
    id: str
    emoji: str
    name: str
    desc: str
    category: str = ""
    hidden: bool = False  # скрыта до разблокировки — в списке показывается как "🔒 ???"


# Категории — только для группировки экрана «Достижения» (_render_achievements,
# profile.py). Плоский список из 30+ пунктов подряд читается как нечитаемая
# простыня (тот же принцип уже применён к «Статистике» и «Рекордам клуба»).
CAT_START = "🏁 Старт карьеры"
CAT_STREAKS = "🔥 Серии"
CAT_SPECIAL = "🎯 Особые победы"
CAT_MILESTONES = "📊 Объём и вехи"
CAT_CLUB = "🤝 Дух клуба"
CAT_THRONE = "👑 Трон"

# Порядок отображения категорий на экране «Достижения» — намеренный, не
# алфавитный и не «первое появление в списке»: от первых шагов к вершине.
CATEGORY_ORDER = [CAT_START, CAT_STREAKS, CAT_SPECIAL, CAT_MILESTONES, CAT_CLUB, CAT_THRONE]


ACHIEVEMENTS_LIST: list[Achievement] = [
    Achievement("press_start",    "🎮", "Я только посмотреть",      "Сыграть первый матч", category=CAT_START),
    Achievement("first_blood",    "🩸", "Первая кровь",              "Одержать первую победу в карьере", category=CAT_START),
    Achievement("beginners_luck", "😎", "Новичкам везёт",            "Победить в самом первом матче", category=CAT_START),
    Achievement("hat_trick",      "🔥", "Хет-трик",                  "Выиграть 3 матча подряд", category=CAT_STREAKS),
    Achievement("im_on_fire",     "💀", "Я горяч нахуй!",            "Выиграть 5 матчей подряд", category=CAT_STREAKS),
    Achievement("god_mode",       "😤", "Ахуджел. Дай другим выиграть!", "Выиграть 10 матчей подряд", category=CAT_STREAKS),
    Achievement("phoenix",        "💪", "Восставший из зада",        "Победить после серии 3+ поражений подряд", category=CAT_STREAKS, hidden=True),
    Achievement("highlander",     "👑", "Останется только один",     "Стать чемпионом — победить в босс-файте или получить трон", category=CAT_THRONE),
    Achievement("david_goliath",  "🎯", "Ебнул четырёхпалубку",     "Победить игрока с рейтингом выше на 100+ pts", category=CAT_SPECIAL, hidden=True),
    Achievement("marathon",       "🕰", "Совсем абанулись",          "Сыграть матч из 5 и более партий", category=CAT_SPECIAL),
    Achievement("fatality",       "💥", "Фаталити",                  "Победить, не отдав сопернику ни одной партии", category=CAT_SPECIAL, hidden=True),
    Achievement("no_sweat",       "⚡", "Даже не вспотел",           "Выиграть партию со счётом 11:0", category=CAT_SPECIAL, hidden=True),
    Achievement("diplomat",       "🤝", "Мир, дружба, жвачка",      "Сыграть 5 ничьих", category=CAT_CLUB),
    Achievement("revenge",        "⚔️", "Ответ_очка",               "Победить того, кто последним обыграл тебя", category=CAT_SPECIAL, hidden=True),
    Achievement("dominator",      "☠️", "То что мертво",             "Победить одного соперника 10 раз подряд", category=CAT_STREAKS, hidden=True),
    Achievement("fifty",          "🎊", "Стукнул полтинник",          "Сыграть 50 матчей", category=CAT_MILESTONES),
    Achievement("veteran",        "🏆", "Прошаренный",               "Сыграть 100 матчей", category=CAT_MILESTONES),
    Achievement("legend",         "🎾", "Великий теннисит",          "Сыграть 200 матчей", category=CAT_MILESTONES),
    Achievement("workhorse",      "⚙️", "Стахановец",                "Сыграть 500 матчей", category=CAT_MILESTONES),
    Achievement("monument",       "🗿", "Монумент",                  "Сыграть 750 матчей", category=CAT_MILESTONES),
    Achievement("superstar",      "🌟", "Суперстар",                 "Сыграть 1000 матчей", category=CAT_MILESTONES),
    Achievement("point_saver",    "🪙", "Копил по очку",             "Набрать 4000 очков за карьеру", category=CAT_MILESTONES),
    Achievement("sturdy_grinder", "💪", "Крепкий середняк",          "Набрать 8000 очков за карьеру", category=CAT_MILESTONES),
    Achievement("point_farmer",   "🌾", "Нафармил очков",            "Набрать 12000 очков за карьеру", category=CAT_MILESTONES),
    Achievement("set_sniper",     "🎯", "Сетовый снайпёр",           "Выиграть 200 партий за карьеру", category=CAT_MILESTONES),
    Achievement("set_veteran",    "🎖", "Сетовый ветеран труда",     "Выиграть 500 партий за карьеру", category=CAT_MILESTONES),
    Achievement("set_legend",     "👑", "Сетовая лехенда",           "Выиграть 1000 партий за карьеру", category=CAT_MILESTONES),
    Achievement("maniac",         "🤪", "Теннисный маньячелло",       "Сыграть 10 матчей за один день", category=CAT_MILESTONES),
    Achievement("collector",      "🗺", "Со всеми познакомился",     "Победить каждого игрока хотя бы раз", category=CAT_CLUB),
    Achievement("rating_1200",    "⭐", "Рейтинг 1200",              "Достичь рейтинга 1200 pts", category=CAT_MILESTONES),
    Achievement("anchorage_spirit", "🏳️", "Дух Анкориджа",          "Отменить матч", category=CAT_CLUB),
    Achievement("comeback",       "🔄", "CumБэк",                    "Выиграть матч, проигрывая 0:2 по партиям", category=CAT_SPECIAL, hidden=True),
    Achievement("fk_tyumen",      "🥊", "ФК Тюмень",                 "Проиграть 5 матчей подряд", category=CAT_STREAKS),
    Achievement("relentless",     "☀️", "Неистого",                  "Выиграть все свои матчи за день (от 3)", category=CAT_MILESTONES),
    Achievement("deuce_maker",    "🎢", "Дьюсмейкер",                "Выиграть партию на дьюсе (12:10 и выше)", category=CAT_SPECIAL),
    Achievement("titans",         "🥋", "Битва такеши титанов",      "Победить в матче, где оба были 1100+ pts", category=CAT_SPECIAL, hidden=True),
    Achievement("takova_zhis",    "🎭", "Такова жись",               "6 матчей подряд с чередованием побед и поражений", category=CAT_STREAKS, hidden=True),
    Achievement("terminator_slain", "🦾", "Вынес терминатора",       "Победить соперника, шедшего с серией 5+ побед подряд", category=CAT_SPECIAL, hidden=True),
    Achievement("night_king",     "🌙", "Король ночи",               "Обыграть всех игроков клуба за один день", category=CAT_MILESTONES, hidden=True),
    Achievement("throne_defended", "🛡", "Трон удержан",             "Отбиться от претендента в босс-файте", category=CAT_THRONE, hidden=True),
    Achievement("throne_denied",  "🚪", "Мимо трона",                "Проиграть босс-файт за трон, оставшись претендентом", category=CAT_THRONE, hidden=True),
    Achievement("chance_blown",   "💸", "Просран шанс",              "Потерять статус претендента на трон, не дойдя до босс-файта", category=CAT_THRONE, hidden=True),
    Achievement("night_owl",      "🦉", "Полуночник",                "Выиграть матч, завершённый ночью (0:00–6:00 МСК)", category=CAT_SPECIAL, hidden=True),
    Achievement("deuce_storm",    "🌪", "Дьюсопад",                  "Выиграть матч, где каждая партия закончилась на дьюсе", category=CAT_SPECIAL, hidden=True),
    Achievement("no_rest_win",    "🔁", "Добивашка",                 "Выиграть матч, начатый в течение 10 минут после предыдущего с тем же соперником", category=CAT_SPECIAL, hidden=True),
    Achievement("round_hundred",  "💯", "Круглая цифра",             "Рейтинг стал ровно кратен 100", category=CAT_MILESTONES, hidden=True),
    Achievement("absolute_zero",  "🥶", "Абсолютный ноль",           "Выиграть матч, где КАЖДАЯ партия закончилась 11:0", category=CAT_SPECIAL, hidden=True),
    Achievement("weekend_warrior", "🏖", "Выходного дня",            "Выиграть матч, сыгранный в субботу или воскресенье", category=CAT_SPECIAL, hidden=True),
    Achievement("rock_bottom",    "🕳", "Дно",                       "Рейтинг упал ровно до 900.0 (пол ветерана)", category=CAT_MILESTONES, hidden=True),
    Achievement("full_circle_week", "🌐", "Полный круг за неделю",   "Обыграть каждого игрока клуба минимум раз за 7 дней", category=CAT_CLUB, hidden=True),
    Achievement("draw_double",    "🕊", "Дубль мира",                "Сыграть 2 ничьи подряд", category=CAT_CLUB, hidden=True),
    Achievement("first_crown",    "🎉", "Первая корона",             "Выиграть свой самый первый босс-файт в карьере", category=CAT_THRONE, hidden=True),
]

ACHIEVEMENTS_MAP: dict[str, Achievement] = {a.id: a for a in ACHIEVEMENTS_LIST}

# Увеличивай при добавлении новых ачивок, требующих бэкфилл.
# Игроки с player.backfill_version < BACKFILL_VERSION будут обработаны один раз при старте.
BACKFILL_VERSION = 11

TERMINATOR_STREAK_LEN = 5  # активная серия соперника для «Вынес терминатора»

ALTERNATING_STREAK_LEN = 6  # длина цепочки для «Такова жись»


def _has_alternating_tail(matches_asc: list, player_id: int, length: int = ALTERNATING_STREAK_LEN) -> bool:
    """Проверяет, что последние `length` завершённых матчей игрока строго
    чередуют победу/поражение (W-L-W-L… или L-W-L-W…). Ничья в окне рвёт
    цепочку — считаем её отсутствующей. matches_asc — по возрастанию даты."""
    if len(matches_asc) < length:
        return False
    tail = matches_asc[-length:]
    outcomes = []
    for m in tail:
        if m.winner_id is None:
            return False
        outcomes.append(m.winner_id == player_id)
    return all(outcomes[i] != outcomes[i + 1] for i in range(length - 1))


def _career_points_and_sets(matches: list, player_id: int) -> tuple[int, int]:
    """Суммарные набранные очки и выигранные партии за карьеру — с перспективы
    player_id, независимо от исхода матча (для вех «Копил по очку»/«Сетовый
    снайпёр» и их старших ступеней). Та же перспектива, что и в _match_line
    (utils.py) и _compute_player_stats (services/stats.py)."""
    points = sets_won = 0
    for m in matches:
        if not m.sets_data:
            continue
        is_draw = m.winner_id is None
        i_am_challenger = m.challenger_id == player_id
        i_am_winner = m.winner_id == player_id
        i_am_favored = i_am_challenger if is_draw else i_am_winner
        for s in m.sets_data:
            mine, theirs = (s["w"], s["l"]) if i_am_favored else (s["l"], s["w"])
            points += mine
            if mine > theirs:
                sets_won += 1
    return points, sets_won


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_achievements(player: Player) -> list[str]:
    """Список заработанных id достижений."""
    try:
        return json.loads(player.achievements or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def _add_new(earned: list[str], ach_id: str) -> bool:
    """Добавить если ещё нет. Возвращает True если добавлено."""
    if ach_id not in earned:
        earned.append(ach_id)
        return True
    return False


def record_achievements_earned(
    session: AsyncSession, player_id: int, new_ids: list[str], earned_at: datetime | None,
) -> None:
    """Записывает дату получения новых ачивок (v2.106.0) — вызывается ПОСЛЕ
    check_win/loss/draw_achievements() / check_cancel_achievements() /
    check_boss_fight_*() / check_chance_blown_achievement(). Эти функции
    возвращают только список id, без контекста момента (один срабатывает от
    конкретного матча, другой — от события без матча вроде отмены), поэтому
    дата передаётся явно вызывающим — тот уже знает нужный timestamp."""
    for ach_id in new_ids:
        session.add(AchievementEarned(player_id=player_id, achievement_id=ach_id, earned_at=earned_at))


# ── Check after win ────────────────────────────────────────────────────────────

async def check_win_achievements(
    session: AsyncSession,
    winner: Player,
    loser: Player,
    match: Match,
    old_winner_rating: float,
    old_loser_rating: float,
    h2h_matches: list[Match],
) -> list[str]:
    """
    Проверяет все достижения после победы.
    Возвращает список id новых (только что заработанных) достижений победителя.

    h2h_matches — завершённые матчи между winner/loser ДО текущего (desc по
    completed_at), из общего bot.utils.get_h2h_matches() — переиспользуется
    вызывающим вместо повторного похода в БД за той же историей.
    """
    sets_data = match.sets_data  # winner perspective: [{"w": winner_pts, "l": loser_pts}, ...]
    earned = get_achievements(winner)
    new_ids: list[str] = []

    def maybe(ach_id: str) -> None:
        if _add_new(earned, ach_id):
            new_ids.append(ach_id)

    # Все завершённые матчи победителя по хронологии (включая текущий)
    r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == winner.id, Match.challenged_id == winner.id),
            Match.status == MatchStatus.completed,
        )
        .order_by(Match.completed_at)
    )
    all_matches = r.scalars().all()
    total = len(all_matches)

    # ── Первый матч ──────────────────────────────────────────────────────────
    if total == 1:
        maybe("press_start")

    # ── Первая победа ────────────────────────────────────────────────────────
    wins_before = sum(
        1 for m in all_matches if m.winner_id == winner.id and m.id != match.id
    )
    if wins_before == 0:
        maybe("first_blood")
        if total == 1:
            maybe("beginners_luck")

    # ── Стрик побед ──────────────────────────────────────────────────────────
    streak = 0
    for m in reversed(all_matches):
        if m.winner_id == winner.id:
            streak += 1
        else:
            break  # ничья или поражение — стрик прерывается
    if streak >= 3:
        maybe("hat_trick")
    if streak >= 5:
        maybe("im_on_fire")
    if streak >= 10:
        maybe("god_mode")

    # ── Феникс: серия 3+ поражений ДО текущей победы ────────────────────────
    prev_matches = [m for m in all_matches if m.id != match.id]
    loss_streak_before = 0
    for m in reversed(prev_matches):
        if m.winner_id is not None and m.winner_id != winner.id:
            loss_streak_before += 1
        else:
            break
    if loss_streak_before >= 3:
        maybe("phoenix")

    # ── Останется только один: стал/остаётся чемпионом ──────────────────────
    # Перепривязано с "рейтинг выше всех" на "владеет местом #1" (Player.is_champion) —
    # #1 больше не занимается по очкам, только через босс-файт. При выключенной
    # фиче (чемпион не назначен) is_champion всегда False — ачивка не выдастся
    # никому, пока не пройдёт bootstrap_champion (см. utils.py).
    if winner.is_champion:
        maybe("highlander")

    # ── Давид и Голиаф: соперник был на 100+ pts сильнее ────────────────────
    if old_loser_rating - old_winner_rating >= 100:
        maybe("david_goliath")

    # ── Совсем абанулись: 5+ партий ─────────────────────────────────────────
    if len(sets_data) >= 5:
        maybe("marathon")

    # ── Фаталити: ни одной партии сопернику (минимум 2 партии в матче) ──────
    loser_sets = sum(1 for s in sets_data if s["l"] > s["w"])
    if loser_sets == 0 and len(sets_data) >= 2:
        maybe("fatality")

    # ── Даже не вспотел: партия 11:0 ────────────────────────────────────────
    if any(s["w"] == 11 and s["l"] == 0 for s in sets_data):
        maybe("no_sweat")

    # ── Ответ_очка: предыдущий матч между ними выиграл соперник ─────────────
    prev_h2h = h2h_matches[0] if h2h_matches else None
    if prev_h2h is not None and prev_h2h.winner_id == loser.id:
        maybe("revenge")

    # ── Добивашка: этот матч начат в течение 10 минут после предыдущего между
    # этой же парой — переиспользует prev_h2h, уже загруженный для «Ответ_очка».
    if prev_h2h is not None and match.created_at and prev_h2h.completed_at:
        gap = (as_naive(match.created_at) - as_naive(prev_h2h.completed_at)).total_seconds()
        if 0 <= gap <= 600:
            maybe("no_rest_win")

    # ── Полуночник: матч завершился ночью (0:00–6:00 МСК) ────────────────────
    if match.completed_at and 0 <= msk_hour_and_weekday(match.completed_at)[0] < 6:
        maybe("night_owl")

    # ── Дьюсопад: КАЖДАЯ партия матча закончилась на дьюсе (12+ очков, выиграна ──
    # ── с отрывом ровно 2) — независимо от того, кто выиграл конкретную партию ─
    if len(sets_data) >= 2 and all(
        max(s["w"], s["l"]) >= 12 and abs(s["w"] - s["l"]) == 2 for s in sets_data
    ):
        maybe("deuce_storm")

    # ── Абсолютный ноль: КАЖДАЯ партия закончилась 11:0 (соперник вообще не набрал) ─
    if len(sets_data) >= 2 and all(s["w"] == 11 and s["l"] == 0 for s in sets_data):
        maybe("absolute_zero")

    # ── Выходного дня: матч завершился в субботу или воскресенье (по МСК) ────
    if match.completed_at and msk_hour_and_weekday(match.completed_at)[1] >= 5:
        maybe("weekend_warrior")

    # ── Вынес терминатора: у соперника была активная серия 5+ побед ДО этого матча ─
    loser_prev_r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == loser.id, Match.challenged_id == loser.id),
            Match.status == MatchStatus.completed,
            Match.id != match.id,
        )
        .order_by(Match.completed_at)
    )
    loser_prev_matches = loser_prev_r.scalars().all()
    loser_streak_before = 0
    for m in reversed(loser_prev_matches):
        if m.winner_id == loser.id:
            loser_streak_before += 1
        else:
            break
    if loser_streak_before >= TERMINATOR_STREAK_LEN:
        maybe("terminator_slain")

    # ── Вехи по числу матчей ─────────────────────────────────────────────────
    if total >= 50:
        maybe("fifty")
    if total >= 100:
        maybe("veteran")
    if total >= 200:
        maybe("legend")
    if total >= 500:
        maybe("workhorse")
    if total >= 750:
        maybe("monument")
    if total >= 1000:
        maybe("superstar")

    # ── Вехи по очкам и выигранным партиям ───────────────────────────────────
    career_points, career_sets_won = _career_points_and_sets(all_matches, winner.id)
    if career_points >= 4000:
        maybe("point_saver")
    if career_points >= 8000:
        maybe("sturdy_grinder")
    if career_points >= 12000:
        maybe("point_farmer")
    if career_sets_won >= 200:
        maybe("set_sniper")
    if career_sets_won >= 500:
        maybe("set_veteran")
    if career_sets_won >= 1000:
        maybe("set_legend")

    # ── Теннисный маньячелло: 10+ матчей за сегодня ──────────────────────────
    # Граница «сегодня» — полночь по МСК (единое бизнес-правило, как в экранах и пасхалках)
    today_start = msk_day_start()
    today_r = await session.execute(
        select(func.count()).select_from(Match).where(
            or_(Match.challenger_id == winner.id, Match.challenged_id == winner.id),
            Match.status == MatchStatus.completed,
            Match.completed_at >= today_start,
        )
    )
    if today_r.scalar() >= 10:
        maybe("maniac")

    # ── То что мертво: 10+ побед подряд над одним соперником ─────────────────
    dom_r = await session.execute(
        select(Match)
        .where(
            or_(
                and_(Match.challenger_id == winner.id, Match.challenged_id == loser.id),
                and_(Match.challenger_id == loser.id, Match.challenged_id == winner.id),
            ),
            Match.status == MatchStatus.completed,
        )
        .order_by(desc(Match.completed_at))
    )
    dom_matches = dom_r.scalars().all()
    dom_streak = 0
    for m in dom_matches:
        if m.winner_id == winner.id:
            dom_streak += 1
        else:
            break
    if dom_streak >= 10:
        maybe("dominator")

    # ── Со всеми познакомился ────────────────────────────────────────────────
    other_ids_r = await session.execute(
        select(Player.id).where(Player.id != winner.id)
    )
    other_ids = {row[0] for row in other_ids_r.all()}
    beaten_ids = {
        (m.challenged_id if m.challenger_id == winner.id else m.challenger_id)
        for m in all_matches if m.winner_id == winner.id
    }
    if other_ids and other_ids.issubset(beaten_ids):
        maybe("collector")

    # ── Полный круг за неделю: обыграл каждого игрока клуба за трейлинг 7 дней ─
    if other_ids and match.completed_at:
        week_ago = as_naive(match.completed_at) - timedelta(days=7)
        beaten_week_ids = {
            (m.challenged_id if m.challenger_id == winner.id else m.challenger_id)
            for m in all_matches
            if m.winner_id == winner.id
            and m.completed_at
            and as_naive(m.completed_at) >= week_ago
        }
        if other_ids.issubset(beaten_week_ids):
            maybe("full_circle_week")

    # ── Первая корона: выиграл свой самый первый босс-файт в карьере ─────────
    if match.is_boss_fight:
        prior_boss_fights = any(m.is_boss_fight for m in all_matches if m.id != match.id)
        if not prior_boss_fights:
            maybe("first_crown")

    # ── Рейтинг 1200 ─────────────────────────────────────────────────────────
    if winner.rating >= 1200.0:
        maybe("rating_1200")

    # ── Круглая цифра: рейтинг стал ровно кратен 100 ─────────────────────────
    if rating_tenths(winner.rating) % 1000 == 0:
        maybe("round_hundred")

    # ── CumБэк: выиграл, проиграв первые две партии (0:2 → победа) ───────────
    if (
        len(sets_data) >= 2
        and sets_data[0]["l"] > sets_data[0]["w"]
        and sets_data[1]["l"] > sets_data[1]["w"]
    ):
        maybe("comeback")

    # ── Дьюсмейкер: выиграл партию на дьюсе (12+ очков) ──────────────────────
    if any(s["w"] >= 12 and s["w"] > s["l"] for s in sets_data):
        maybe("deuce_maker")

    # ── Битва такеши титанов: оба игрока были 1100+ ─────────────────────────
    if old_winner_rating >= 1100.0 and old_loser_rating >= 1100.0:
        maybe("titans")

    # ── Неистого: все матчи за сегодня — победы (от 3) ──────────────────────
    today_matches = [
        m for m in all_matches
        if m.completed_at and as_naive(m.completed_at) >= today_start
    ]
    if len(today_matches) >= 3 and all(m.winner_id == winner.id for m in today_matches):
        maybe("relentless")

    # ── Король ночи: обыграл всех остальных игроков клуба за сегодня ────────
    beaten_today_ids = {
        (m.challenged_id if m.challenger_id == winner.id else m.challenger_id)
        for m in today_matches if m.winner_id == winner.id
    }
    if other_ids and other_ids.issubset(beaten_today_ids):
        maybe("night_king")

    # ── Такова жись: 6 матчей подряд с чередованием побед/поражений ─────────
    if _has_alternating_tail(all_matches, winner.id):
        maybe("takova_zhis")

    if new_ids:
        winner.achievements = json.dumps(earned)

    return new_ids


# ── Check after loss ──────────────────────────────────────────────────────────

async def check_loss_achievements(
    session: AsyncSession,
    loser: Player,
    sets_data: list[dict],      # winner perspective: [{"w": winner_pts, "l": loser_pts}, ...]
) -> list[str]:
    """
    Проверяет достижения для проигравшего.
    Возвращает список id новых достижений.

    Применимые ачивки: press_start, marathon, no_sweat (выиграл партию 11:0 в проигранном матче),
    veteran, legend.
    """
    earned = get_achievements(loser)
    new_ids: list[str] = []

    def maybe(ach_id: str) -> None:
        if _add_new(earned, ach_id):
            new_ids.append(ach_id)

    r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == loser.id, Match.challenged_id == loser.id),
            Match.status == MatchStatus.completed,
        )
        .order_by(Match.completed_at)
    )
    all_matches = r.scalars().all()
    total = len(all_matches)

    # Первый матч
    if total == 1:
        maybe("press_start")

    # Совсем абанулись: 5+ партий
    if len(sets_data) >= 5:
        maybe("marathon")

    # Даже не вспотел: проигравший выиграл хотя бы одну партию 11:0
    # В sets_data (winner perspective): s["l"] — очки проигравшего
    if any(s["l"] == 11 and s["w"] == 0 for s in sets_data):
        maybe("no_sweat")

    # Вехи
    if total >= 50:
        maybe("fifty")
    if total >= 100:
        maybe("veteran")
    if total >= 200:
        maybe("legend")
    if total >= 500:
        maybe("workhorse")
    if total >= 750:
        maybe("monument")
    if total >= 1000:
        maybe("superstar")

    career_points, career_sets_won = _career_points_and_sets(all_matches, loser.id)
    if career_points >= 4000:
        maybe("point_saver")
    if career_points >= 8000:
        maybe("sturdy_grinder")
    if career_points >= 12000:
        maybe("point_farmer")
    if career_sets_won >= 200:
        maybe("set_sniper")
    if career_sets_won >= 500:
        maybe("set_veteran")
    if career_sets_won >= 1000:
        maybe("set_legend")

    # Теннисный маньячелло: 10+ матчей за сегодня
    # Граница «сегодня» — полночь по МСК (единое бизнес-правило, как в экранах и пасхалках)
    today_start = msk_day_start()
    today_r = await session.execute(
        select(func.count()).select_from(Match).where(
            or_(Match.challenger_id == loser.id, Match.challenged_id == loser.id),
            Match.status == MatchStatus.completed,
            Match.completed_at >= today_start,
        )
    )
    if today_r.scalar() >= 10:
        maybe("maniac")

    # ── ФК Тюмень: 5 поражений подряд ───────────────────────────────────────
    loss_streak = 0
    for m in reversed(all_matches):
        if m.winner_id is not None and m.winner_id != loser.id:
            loss_streak += 1
        else:
            break
    if loss_streak >= 5:
        maybe("fk_tyumen")

    # ── Дьюсмейкер: проигравший всё же выиграл партию на дьюсе ───────────────
    # sets_data в перспективе победителя: очки проигравшего — s["l"]
    if any(s["l"] >= 12 and s["l"] > s["w"] for s in sets_data):
        maybe("deuce_maker")

    # ── Такова жись: 6 матчей подряд с чередованием побед/поражений ─────────
    if _has_alternating_tail(all_matches, loser.id):
        maybe("takova_zhis")

    # ── Дно: рейтинг упал ровно до пола ветерана (900.0) ─────────────────────
    if rating_tenths(loser.rating) == 9000:
        maybe("rock_bottom")

    if new_ids:
        loser.achievements = json.dumps(earned)

    return new_ids


# ── Check after draw ───────────────────────────────────────────────────────────

async def check_draw_achievements(
    session: AsyncSession,
    player: Player,
    sets_data: list[dict],          # challenger perspective: {"w": ch_pts, "l": cd_pts}
    is_challenger: bool,
) -> list[str]:
    """
    Проверяет достижения после ничьей для одного из участников.
    Возвращает список id новых достижений.
    """
    earned = get_achievements(player)
    new_ids: list[str] = []

    def maybe(ach_id: str) -> None:
        if _add_new(earned, ach_id):
            new_ids.append(ach_id)

    r = await session.execute(
        select(Match)
        .where(
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
            Match.status == MatchStatus.completed,
        )
        .order_by(Match.completed_at)
    )
    all_matches = r.scalars().all()
    total = len(all_matches)

    # Первый матч
    if total == 1:
        maybe("press_start")

    # Дипломат: 5 ничьих
    total_draws = sum(1 for m in all_matches if m.winner_id is None)
    if total_draws >= 5:
        maybe("diplomat")

    # Дубль мира: 2 ничьи подряд (необязательно с одним и тем же соперником) —
    # all_matches[-1] это текущий матч (уже ничья, раз мы в этой функции)
    if len(all_matches) >= 2 and all_matches[-2].winner_id is None:
        maybe("draw_double")

    # Совсем абанулись: 5+ партий
    if len(sets_data) >= 5:
        maybe("marathon")

    # Даже не вспотел: партия 11:0 (с перспективы игрока)
    if is_challenger:
        if any(s["w"] == 11 and s["l"] == 0 for s in sets_data):
            maybe("no_sweat")
    else:
        if any(s["l"] == 11 and s["w"] == 0 for s in sets_data):
            maybe("no_sweat")

    # Вехи
    if total >= 50:
        maybe("fifty")
    if total >= 100:
        maybe("veteran")
    if total >= 200:
        maybe("legend")
    if total >= 500:
        maybe("workhorse")
    if total >= 750:
        maybe("monument")
    if total >= 1000:
        maybe("superstar")

    career_points, career_sets_won = _career_points_and_sets(all_matches, player.id)
    if career_points >= 4000:
        maybe("point_saver")
    if career_points >= 8000:
        maybe("sturdy_grinder")
    if career_points >= 12000:
        maybe("point_farmer")
    if career_sets_won >= 200:
        maybe("set_sniper")
    if career_sets_won >= 500:
        maybe("set_veteran")
    if career_sets_won >= 1000:
        maybe("set_legend")

    # Теннисный маньячелло: 10+ матчей за сегодня
    # Граница «сегодня» — полночь по МСК (единое бизнес-правило, как в экранах и пасхалках)
    today_start = msk_day_start()
    today_r = await session.execute(
        select(func.count()).select_from(Match).where(
            or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
            Match.status == MatchStatus.completed,
            Match.completed_at >= today_start,
        )
    )
    if today_r.scalar() >= 10:
        maybe("maniac")

    # ── Дьюсмейкер: выиграл партию на дьюсе (с перспективы игрока) ───────────
    if is_challenger:
        if any(s["w"] >= 12 and s["w"] > s["l"] for s in sets_data):
            maybe("deuce_maker")
    else:
        if any(s["l"] >= 12 and s["l"] > s["w"] for s in sets_data):
            maybe("deuce_maker")

    if new_ids:
        player.achievements = json.dumps(earned)

    return new_ids


# ── Check after cancel ──────────────────────────────────────────────────────────

async def check_cancel_achievements(session: AsyncSession, player: Player) -> list[str]:
    """Достижение за отмену матча (Дух Анкориджа). Вызывается из обработчика отмены.

    Начисляется любому участнику отменённого матча.
    """
    earned = get_achievements(player)
    new_ids: list[str] = []
    if _add_new(earned, "anchorage_spirit"):
        new_ids.append("anchorage_spirit")
        player.achievements = json.dumps(earned)
    return new_ids


# ── Check after boss-fight defense ──────────────────────────────────────────────

async def check_boss_fight_defense_achievement(champion: Player) -> list[str]:
    """'Трон удержан' — чемпион отбился от претендента в босс-файте.

    Вызывается напрямую из confirm_result() (match_result.py) сразу после
    подтверждения, что трон НЕ перешёл — не требует запросов к БД.
    """
    earned = get_achievements(champion)
    new_ids: list[str] = []
    if _add_new(earned, "throne_defended"):
        new_ids.append("throne_defended")
        champion.achievements = json.dumps(earned)
    return new_ids


# ── Check after boss-fight loss (as the challenger) ──────────────────────────────

async def check_boss_fight_challenger_defeat_achievement(challenger: Player) -> list[str]:
    """'Мимо трона' — претендент проиграл боссфайт, чемпион отбился.

    Вызывается напрямую из confirm_result() (match_result.py) сразу после
    подтверждения, что трон НЕ перешёл — тем же местом и тем же способом,
    что check_boss_fight_defense_achievement(), только для проигравшей стороны.
    Не восстанавливается бэкфиллом — как highlander/david_goliath/revenge,
    требует знать роль (чемпион vs претендент) на момент КОНКРЕТНОГО матча,
    а Player.is_champion хранит только текущее состояние, не историю.
    """
    earned = get_achievements(challenger)
    new_ids: list[str] = []
    if _add_new(earned, "throne_denied"):
        new_ids.append("throne_denied")
        challenger.achievements = json.dumps(earned)
    return new_ids


# ── Check after losing challenger status (before reaching a boss fight) ──────────

async def check_chance_blown_achievement(ex_challenger: Player) -> list[str]:
    """'Просран шанс' — игрок был претендентом на трон и перестал им быть, не
    дойдя до босс-файта: чемпион обошёл его обратно очками, сам потерял очки
    (в т.ч. проиграв кому-то другому — не обязательно чемпиону), или его тихо
    обогнал по рейтингу третий игрок, вообще не игравший ни с чемпионом,
    ни с ним самим.

    Вызывается из confirm_result() (match_result.py) в общем хвосте, который
    уже сравнивает претендента до/после матча (challenger_before/after) —
    переиспользует тот же снапшот, что и уведомление «появился новый
    претендент», только для противоположного случая. Не для боссфайтов —
    поражение непосредственно в боссфайте даёт отдельную throne_denied.
    Не восстанавливается бэкфиллом — та же причина, что у throne_denied.
    """
    earned = get_achievements(ex_challenger)
    new_ids: list[str] = []
    if _add_new(earned, "chance_blown"):
        new_ids.append("chance_blown")
        ex_challenger.achievements = json.dumps(earned)
    return new_ids


# ── Backfill ───────────────────────────────────────────────────────────────────

async def backfill_achievements(session: AsyncSession) -> None:
    """
    Рассчитывает исторические достижения для всех игроков.
    Идемпотентна: повторный вызов не изменит уже заработанные.
    Вызывается при старте из init_db().

    Примечание: highlander, david_goliath, revenge, throne_denied,
    chance_blown, rock_bottom и rating_1200 не восстанавливаются (требуют
    снапшот рейтинга/роли на момент КОНКРЕТНОГО исторического матча, а не
    текущее значение) — будут начислены в реальном времени.

    С v2.106.0 попутно (BACKFILL_VERSION 10→11, форсирует повторный проход
    ДАЖЕ для уже полностью забэкфилленных игроков) заполняет даты получения
    в AchievementEarned для ачивок, у которых даты ещё нет — большинство
    проверок теперь считается инкрементально ПРЯМО В цикле по матчам (а не
    постфактум по агрегату, как раньше), поэтому дата берётся из конкретного
    матча-триггера. Для по-настоящему непроверяемых по истории (снапшот
    рейтинга) дата остаётся NULL — не выдумываем задним числом.
    """
    players_r = await session.execute(
        select(Player).where(Player.backfill_version < BACKFILL_VERSION)
    )
    players = players_r.scalars().all()
    if not players:
        return  # все игроки уже обработаны — быстрый выход

    all_ids_r = await session.execute(select(Player.id))
    all_player_ids = {row[0] for row in all_ids_r.all()}

    # ── Вынес терминатора: глобальный проход по ВСЕМ матчам клуба в хронологии ──
    # Метрика зависит от истории соперника (его серии), а не только текущего
    # игрока — обычный per-player replay ниже этого не видит, нужен один общий проход.
    club_matches_r = await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed)
        .order_by(Match.completed_at)
    )
    club_matches = club_matches_r.scalars().all()
    club_streaks: dict[int, int] = {}
    terminator_dates: dict[int, datetime] = {}  # winner_id -> дата матча-триггера (первого)
    for m in club_matches:
        if m.winner_id is None:
            club_streaks[m.challenger_id] = 0
            club_streaks[m.challenged_id] = 0
            continue
        loser_id = m.challenged_id if m.winner_id == m.challenger_id else m.challenger_id
        if club_streaks.get(loser_id, 0) >= TERMINATOR_STREAK_LEN and m.winner_id not in terminator_dates:
            terminator_dates[m.winner_id] = m.completed_at
        club_streaks[m.winner_id] = club_streaks.get(m.winner_id, 0) + 1
        club_streaks[loser_id] = 0

    for player in players:
        earned = get_achievements(player)
        dates: dict[str, datetime | None] = {}  # ach_id -> дата первого срабатывания в ЭТОМ проходе

        def mark(ach_id: str, when: datetime | None = None) -> None:
            _add_new(earned, ach_id)
            if ach_id not in dates:
                dates[ach_id] = when

        r = await session.execute(
            select(Match)
            .where(
                or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
                Match.status == MatchStatus.completed,
            )
            .order_by(Match.completed_at)
        )
        matches = r.scalars().all()
        total = len(matches)

        if total == 0:
            continue

        # Первый матч
        mark("press_start", matches[0].completed_at)

        # Вынес терминатора (из глобального прохода выше)
        if player.id in terminator_dates:
            mark("terminator_slain", terminator_dates[player.id])

        # Replay — считаем статистику в хронологическом порядке
        win_streak = 0
        loss_streak = 0
        total_wins = 0
        total_draws = 0
        beaten_opponents: set[int] = set()
        first_win_at: datetime | None = None
        alt_window: list[bool] = []  # для «Такова жись» — скользящее окно исходов
        last_completed_vs: dict[int, datetime] = {}  # для «Добивашка» — по опоненту
        prev_was_draw = False  # для «Дубль мира»
        had_boss_fight_before = False  # для «Первая корона»
        other_ids = all_player_ids - {player.id}  # «Полный круг за неделю» / «Со всеми познакомился»
        # Скользящее окно побед за трейлинг 7 дней для «Полный круг за неделю» —
        # деque вместо полного скана matches на КАЖДОЙ победе (был O(n²) на игрока).
        recent_wins: deque[tuple[datetime, int]] = deque()
        # Инкрементальные очки/партии за карьеру (та же перспектива, что и
        # _career_points_and_sets) — считаем по ходу, чтобы поймать ТОЧНЫЙ матч,
        # на котором вехи по очкам/партиям пересечены, а не просто финальную сумму.
        running_points = 0
        running_sets_won = 0

        for m in matches:
            opp_id = m.challenged_id if m.challenger_id == player.id else m.challenger_id
            is_win = m.winner_id == player.id
            is_draw = m.winner_id is None

            if m.is_boss_fight:
                if is_win and not had_boss_fight_before:
                    mark("first_crown", m.completed_at)
                had_boss_fight_before = True

            if is_draw:
                alt_window = []
            else:
                alt_window.append(is_win)
                if len(alt_window) > ALTERNATING_STREAK_LEN:
                    alt_window.pop(0)
                if len(alt_window) == ALTERNATING_STREAK_LEN and all(
                    alt_window[i] != alt_window[i + 1] for i in range(ALTERNATING_STREAK_LEN - 1)
                ):
                    mark("takova_zhis", m.completed_at)

            # Очки/партии за карьеру (перспектива игрока — см. _career_points_and_sets)
            if m.sets_data:
                is_favored = (m.challenger_id == player.id) if is_draw else is_win
                for s in m.sets_data:
                    mine, theirs = (s["w"], s["l"]) if is_favored else (s["l"], s["w"])
                    running_points += mine
                    if mine > theirs:
                        running_sets_won += 1
            if running_points >= 4000:
                mark("point_saver", m.completed_at)
            if running_points >= 8000:
                mark("sturdy_grinder", m.completed_at)
            if running_points >= 12000:
                mark("point_farmer", m.completed_at)
            if running_sets_won >= 200:
                mark("set_sniper", m.completed_at)
            if running_sets_won >= 500:
                mark("set_veteran", m.completed_at)
            if running_sets_won >= 1000:
                mark("set_legend", m.completed_at)

            if is_win:
                total_wins += 1
                if first_win_at is None:
                    first_win_at = m.completed_at
                if loss_streak >= 3:
                    mark("phoenix", m.completed_at)
                loss_streak = 0
                win_streak += 1
                if win_streak == 3:
                    mark("hat_trick", m.completed_at)
                if win_streak == 5:
                    mark("im_on_fire", m.completed_at)
                if win_streak == 10:
                    mark("god_mode", m.completed_at)
                beaten_opponents.add(opp_id)
                if other_ids and other_ids.issubset(beaten_opponents):
                    mark("collector", m.completed_at)

                if m.sets_data:
                    if sum(1 for s in m.sets_data if s["l"] > s["w"]) == 0 and len(m.sets_data) >= 2:
                        mark("fatality", m.completed_at)
                    if any(s["w"] == 11 and s["l"] == 0 for s in m.sets_data):
                        mark("no_sweat", m.completed_at)
                    if len(m.sets_data) >= 5:
                        mark("marathon", m.completed_at)
                    # CumБэк: проиграл первые две партии и выиграл матч
                    if (
                        len(m.sets_data) >= 2
                        and m.sets_data[0]["l"] > m.sets_data[0]["w"]
                        and m.sets_data[1]["l"] > m.sets_data[1]["w"]
                    ):
                        mark("comeback", m.completed_at)
                    # Дьюсмейкер: выиграл партию на дьюсе (победитель = w)
                    if any(s["w"] >= 12 and s["w"] > s["l"] for s in m.sets_data):
                        mark("deuce_maker", m.completed_at)
                    # Полуночник: матч завершился ночью (0:00–6:00 МСК)
                    if m.completed_at and 0 <= msk_hour_and_weekday(m.completed_at)[0] < 6:
                        mark("night_owl", m.completed_at)
                    # Дьюсопад: КАЖДАЯ партия закончилась на дьюсе
                    if len(m.sets_data) >= 2 and all(
                        max(s["w"], s["l"]) >= 12 and abs(s["w"] - s["l"]) == 2
                        for s in m.sets_data
                    ):
                        mark("deuce_storm", m.completed_at)
                    # Абсолютный ноль: КАЖДАЯ партия закончилась 11:0
                    if len(m.sets_data) >= 2 and all(
                        s["w"] == 11 and s["l"] == 0 for s in m.sets_data
                    ):
                        mark("absolute_zero", m.completed_at)
                # Выходного дня: матч завершился в субботу/воскресенье (по МСК)
                if m.completed_at and msk_hour_and_weekday(m.completed_at)[1] >= 5:
                    mark("weekend_warrior", m.completed_at)
                # Добивашка: начат в течение 10 минут после предыдущего с этим же соперником
                prev_vs = last_completed_vs.get(opp_id)
                if prev_vs and m.created_at and 0 <= (as_naive(m.created_at) - prev_vs).total_seconds() <= 600:
                    mark("no_rest_win", m.completed_at)
                # Полный круг за неделю: обыграл каждого игрока клуба за трейлинг 7 дней —
                # скользящее окно (recent_wins), а не полный скан matches на каждой победе
                if other_ids and m.completed_at:
                    completed_naive = as_naive(m.completed_at)
                    recent_wins.append((completed_naive, opp_id))
                    week_ago = completed_naive - timedelta(days=7)
                    while recent_wins and recent_wins[0][0] < week_ago:
                        recent_wins.popleft()
                    beaten_week_ids = {oid for _, oid in recent_wins}
                    if other_ids.issubset(beaten_week_ids):
                        mark("full_circle_week", m.completed_at)

            elif is_draw:
                total_draws += 1
                win_streak = 0
                loss_streak = 0
                if total_draws == 5:
                    mark("diplomat", m.completed_at)

                if prev_was_draw:
                    mark("draw_double", m.completed_at)

                if m.sets_data:
                    is_ch = m.challenger_id == player.id
                    if is_ch:
                        if any(s["w"] == 11 and s["l"] == 0 for s in m.sets_data):
                            mark("no_sweat", m.completed_at)
                        if any(s["w"] >= 12 and s["w"] > s["l"] for s in m.sets_data):
                            mark("deuce_maker", m.completed_at)
                    else:
                        if any(s["l"] == 11 and s["w"] == 0 for s in m.sets_data):
                            mark("no_sweat", m.completed_at)
                        if any(s["l"] >= 12 and s["l"] > s["w"] for s in m.sets_data):
                            mark("deuce_maker", m.completed_at)
                    if len(m.sets_data) >= 5:
                        mark("marathon", m.completed_at)

            else:  # поражение
                win_streak = 0
                loss_streak += 1
                if loss_streak == 5:
                    mark("fk_tyumen", m.completed_at)

                # no_sweat: проигравший мог выиграть партию 11:0
                if m.sets_data:
                    if any(s["l"] == 11 and s["w"] == 0 for s in m.sets_data):
                        mark("no_sweat", m.completed_at)
                    # Дьюсмейкер: проигравший выиграл партию на дьюсе (проигравший = l)
                    if any(s["l"] >= 12 and s["l"] > s["w"] for s in m.sets_data):
                        mark("deuce_maker", m.completed_at)
                # marathon: 5+ партий независимо от результата
                if m.sets_data and len(m.sets_data) >= 5:
                    mark("marathon", m.completed_at)

            if m.completed_at:
                last_completed_vs[opp_id] = as_naive(m.completed_at)
            prev_was_draw = is_draw

        # Вехи по числу матчей — индекс N-го матча в хронологии даёт точную дату
        for n, ach_id in ((50, "fifty"), (100, "veteran"), (200, "legend"),
                          (500, "workhorse"), (750, "monument"), (1000, "superstar")):
            if total >= n:
                mark(ach_id, matches[n - 1].completed_at)

        # Первая победа / новичкам везёт
        if total_wins >= 1:
            mark("first_blood", first_win_at)
            if matches[0].winner_id == player.id:
                mark("beginners_luck", matches[0].completed_at)

        # Неистого: любой день, где все матчи (от 3) — победы.
        # Король ночи: любой день, где обыграны все остальные игроки клуба.
        # Теннисный маньячелло: любой день с 10+ матчами.
        # День считаем по МСК (даты в БД — naive-UTC), как и в realtime-проверках.
        day_groups: dict = {}
        for m in matches:
            if m.completed_at:
                day_groups.setdefault((as_naive(m.completed_at) + MSK_OFFSET).date(), []).append(m)

        for day_matches in day_groups.values():
            if len(day_matches) >= 3 and all(mm.winner_id == player.id for mm in day_matches):
                mark("relentless", day_matches[2].completed_at)
            if len(day_matches) >= 10:
                mark("maniac", day_matches[9].completed_at)

            beaten_today: set[int] = set()
            for mm in day_matches:
                if mm.winner_id == player.id:
                    beaten_today.add(mm.challenged_id if mm.challenger_id == player.id else mm.challenger_id)
                    if other_ids and other_ids.issubset(beaten_today):
                        mark("night_king", mm.completed_at)
                        break

        # То что мертво: 10+ побед подряд над одним соперником
        opp_win_streaks: dict[int, int] = {}
        for m in matches:
            opp_id = m.challenged_id if m.challenger_id == player.id else m.challenger_id
            if m.winner_id == player.id:
                opp_win_streaks[opp_id] = opp_win_streaks.get(opp_id, 0) + 1
                if opp_win_streaks[opp_id] == 10:
                    mark("dominator", m.completed_at)
            else:
                opp_win_streaks[opp_id] = 0

        # Рейтинг 1200 (по peak_rating — текущее значение, не снапшот на момент
        # исторического матча, поэтому дата принципиально недоступна)
        if player.peak_rating and player.peak_rating >= 1200.0:
            mark("rating_1200")

        # Дух Анкориджа: были отменённые (declined) матчи — дата первого такого
        declined_r = await session.execute(
            select(Match.created_at).where(
                or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
                Match.status == MatchStatus.declined,
            ).order_by(Match.created_at).limit(1)
        )
        declined_first = declined_r.scalar_one_or_none()
        if declined_first is not None:
            mark("anchorage_spirit", declined_first)

        player.achievements = json.dumps(earned)
        player.backfill_version = BACKFILL_VERSION

        # Реконциляция дат: для каждой ачивки в итоговом earned (новой или уже
        # имевшейся раньше) без строки в AchievementEarned — создать её. Дата —
        # из dates, если поймали в ЭТОМ проходе, иначе NULL (не восстановима:
        # либо нужен снапшот рейтинга, либо это одна из 6 полностью исключённых
        # из бэкфилла ачивок, полученных раньше в реальном времени).
        existing_r = await session.execute(
            select(AchievementEarned.achievement_id).where(AchievementEarned.player_id == player.id)
        )
        existing_dated = {row[0] for row in existing_r.all()}
        for ach_id in earned:
            if ach_id not in existing_dated:
                session.add(AchievementEarned(
                    player_id=player.id, achievement_id=ach_id, earned_at=dates.get(ach_id),
                ))

    await session.commit()
