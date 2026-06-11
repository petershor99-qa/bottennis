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
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, MatchStatus, Player
from bot.utils import MSK_OFFSET, msk_day_start


@dataclass
class Achievement:
    id: str
    emoji: str
    name: str
    desc: str


ACHIEVEMENTS_LIST: list[Achievement] = [
    Achievement("press_start",    "🎮", "Я только посмотреть",      "Сыграть первый матч"),
    Achievement("first_blood",    "🩸", "Первая кровь",              "Одержать первую победу в карьере"),
    Achievement("beginners_luck", "😎", "Новичкам везёт",            "Победить в самом первом матче"),
    Achievement("hat_trick",      "🔥", "Хет-трик",                  "Выиграть 3 матча подряд"),
    Achievement("im_on_fire",     "💀", "Я горяч нахуй!",            "Выиграть 5 матчей подряд"),
    Achievement("god_mode",       "😤", "Ахуджел. Дай другим выиграть!", "Выиграть 10 матчей подряд"),
    Achievement("phoenix",        "💪", "Восставший из зада",        "Победить после серии 3+ поражений подряд"),
    Achievement("highlander",     "👑", "Останется только один",     "Впервые выйти на 1-е место в рейтинге"),
    Achievement("david_goliath",  "🎯", "Ебнул четырёхпалубку",     "Победить игрока с рейтингом выше на 100+ pts"),
    Achievement("marathon",       "🕰", "Совсем абанулись",          "Сыграть матч из 5 и более партий"),
    Achievement("fatality",       "💥", "Фаталити",                  "Победить, не отдав сопернику ни одной партии"),
    Achievement("no_sweat",       "⚡", "Даже не вспотел",           "Выиграть партию со счётом 11:0"),
    Achievement("diplomat",       "🤝", "Мир, дружба, жвачка",      "Сыграть 5 ничьих"),
    Achievement("revenge",        "⚔️", "Ответ_очка",               "Победить того, кто последним обыграл тебя"),
    Achievement("dominator",      "☠️", "То что мертво",             "Победить одного соперника 10 раз подряд"),
    Achievement("fifty",          "🎊", "Стукнул полтинник",          "Сыграть 50 матчей"),
    Achievement("veteran",        "🏆", "Прошаренный",               "Сыграть 100 матчей"),
    Achievement("legend",         "🎾", "Великий теннисит",          "Сыграть 200 матчей"),
    Achievement("maniac",         "🤪", "Теннисный маньячелло",       "Сыграть 10 матчей за один день"),
    Achievement("collector",      "🗺", "Со всеми познакомился",     "Победить каждого игрока хотя бы раз"),
    Achievement("rating_1200",    "⭐", "Рейтинг 1200",              "Достичь рейтинга 1200 pts"),
    Achievement("anchorage_spirit", "🏳️", "Дух Анкориджа",          "Отменить матч"),
    Achievement("comeback",       "🔄", "CumБэк",                    "Выиграть матч, проигрывая 0:2 по партиям"),
    Achievement("fk_tyumen",      "🥊", "ФК Тюмень",                 "Проиграть 5 матчей подряд"),
    Achievement("relentless",     "☀️", "Неистого",                  "Выиграть все свои матчи за день (от 3)"),
    Achievement("deuce_maker",    "🎢", "Дьюсмейкер",                "Выиграть партию на дьюсе (12:10 и выше)"),
    Achievement("titans",         "🥋", "Битва такеши титанов",      "Победить в матче, где оба были 1100+ pts"),
]

ACHIEVEMENTS_MAP: dict[str, Achievement] = {a.id: a for a in ACHIEVEMENTS_LIST}

# Увеличивай при добавлении новых ачивок, требующих бэкфилл.
# Игроки с player.backfill_version < BACKFILL_VERSION будут обработаны один раз при старте.
BACKFILL_VERSION = 3


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


# ── Check after win ────────────────────────────────────────────────────────────

async def check_win_achievements(
    session: AsyncSession,
    winner: Player,
    loser: Player,
    sets_data: list[dict],          # winner perspective: [{"w": winner_pts, "l": loser_pts}, ...]
    match_id: int,
    old_winner_rating: float,
    old_loser_rating: float,
) -> list[str]:
    """
    Проверяет все достижения после победы.
    Возвращает список id новых (только что заработанных) достижений победителя.
    """
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
        1 for m in all_matches if m.winner_id == winner.id and m.id != match_id
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
    prev_matches = [m for m in all_matches if m.id != match_id]
    loss_streak_before = 0
    for m in reversed(prev_matches):
        if m.winner_id is not None and m.winner_id != winner.id:
            loss_streak_before += 1
        else:
            break
    if loss_streak_before >= 3:
        maybe("phoenix")

    # ── Останется только один: сейчас #1 ────────────────────────────────────
    rank_r = await session.execute(
        select(func.count()).select_from(Player).where(Player.rating > winner.rating)
    )
    if rank_r.scalar() == 0:
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
    h2h_r = await session.execute(
        select(Match)
        .where(
            or_(
                and_(Match.challenger_id == winner.id, Match.challenged_id == loser.id),
                and_(Match.challenger_id == loser.id, Match.challenged_id == winner.id),
            ),
            Match.status == MatchStatus.completed,
        )
        .order_by(desc(Match.completed_at))
        .limit(2)
    )
    h2h = h2h_r.scalars().all()
    # h2h[0] — текущий матч, h2h[1] — предыдущий между ними
    if len(h2h) >= 2 and h2h[1].winner_id == loser.id:
        maybe("revenge")

    # ── Вехи по числу матчей ─────────────────────────────────────────────────
    if total >= 50:
        maybe("fifty")
    if total >= 100:
        maybe("veteran")
    if total >= 200:
        maybe("legend")

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

    # ── Рейтинг 1200 ─────────────────────────────────────────────────────────
    if winner.rating >= 1200.0:
        maybe("rating_1200")

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
    # completed_at в проде naive-UTC; .replace(tzinfo=None) защищает от tz-aware дат
    today_matches = [
        m for m in all_matches
        if m.completed_at and m.completed_at.replace(tzinfo=None) >= today_start
    ]
    if len(today_matches) >= 3 and all(m.winner_id == winner.id for m in today_matches):
        maybe("relentless")

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


# ── Backfill ───────────────────────────────────────────────────────────────────

async def backfill_achievements(session: AsyncSession) -> None:
    """
    Рассчитывает исторические достижения для всех игроков.
    Идемпотентна: повторный вызов не изменит уже заработанные.
    Вызывается при старте из init_db().

    Примечание: highlander, david_goliath и revenge не восстанавливаются
    (требуют снапшоты рейтинга/контекст момента) — будут начислены в реальном времени.
    """
    players_r = await session.execute(
        select(Player).where(Player.backfill_version < BACKFILL_VERSION)
    )
    players = players_r.scalars().all()
    if not players:
        return  # все игроки уже обработаны — быстрый выход

    all_ids_r = await session.execute(select(Player.id))
    all_player_ids = {row[0] for row in all_ids_r.all()}

    for player in players:
        earned = get_achievements(player)

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
        _add_new(earned, "press_start")

        # Replay — считаем статистику в хронологическом порядке
        win_streak = 0
        max_win_streak = 0
        loss_streak = 0
        max_loss_streak = 0
        total_wins = 0
        total_draws = 0
        beaten_opponents: set[int] = set()
        had_phoenix = False

        for m in matches:
            opp_id = m.challenged_id if m.challenger_id == player.id else m.challenger_id
            is_win = m.winner_id == player.id
            is_draw = m.winner_id is None

            if is_win:
                total_wins += 1
                if loss_streak >= 3:
                    had_phoenix = True
                loss_streak = 0
                win_streak += 1
                max_win_streak = max(max_win_streak, win_streak)
                beaten_opponents.add(opp_id)

                if m.sets_data:
                    if sum(1 for s in m.sets_data if s["l"] > s["w"]) == 0 and len(m.sets_data) >= 2:
                        _add_new(earned, "fatality")
                    if any(s["w"] == 11 and s["l"] == 0 for s in m.sets_data):
                        _add_new(earned, "no_sweat")
                    if len(m.sets_data) >= 5:
                        _add_new(earned, "marathon")
                    # CumБэк: проиграл первые две партии и выиграл матч
                    if (
                        len(m.sets_data) >= 2
                        and m.sets_data[0]["l"] > m.sets_data[0]["w"]
                        and m.sets_data[1]["l"] > m.sets_data[1]["w"]
                    ):
                        _add_new(earned, "comeback")
                    # Дьюсмейкер: выиграл партию на дьюсе (победитель = w)
                    if any(s["w"] >= 12 and s["w"] > s["l"] for s in m.sets_data):
                        _add_new(earned, "deuce_maker")

            elif is_draw:
                total_draws += 1
                win_streak = 0
                loss_streak = 0

                if m.sets_data:
                    is_ch = m.challenger_id == player.id
                    if is_ch:
                        if any(s["w"] == 11 and s["l"] == 0 for s in m.sets_data):
                            _add_new(earned, "no_sweat")
                        if any(s["w"] >= 12 and s["w"] > s["l"] for s in m.sets_data):
                            _add_new(earned, "deuce_maker")
                    else:
                        if any(s["l"] == 11 and s["w"] == 0 for s in m.sets_data):
                            _add_new(earned, "no_sweat")
                        if any(s["l"] >= 12 and s["l"] > s["w"] for s in m.sets_data):
                            _add_new(earned, "deuce_maker")
                    if len(m.sets_data) >= 5:
                        _add_new(earned, "marathon")

            else:  # поражение
                win_streak = 0
                loss_streak += 1
                max_loss_streak = max(max_loss_streak, loss_streak)

                # no_sweat: проигравший мог выиграть партию 11:0
                if m.sets_data:
                    if any(s["l"] == 11 and s["w"] == 0 for s in m.sets_data):
                        _add_new(earned, "no_sweat")
                    # Дьюсмейкер: проигравший выиграл партию на дьюсе (проигравший = l)
                    if any(s["l"] >= 12 and s["l"] > s["w"] for s in m.sets_data):
                        _add_new(earned, "deuce_maker")
                # marathon: 5+ партий независимо от результата
                if m.sets_data and len(m.sets_data) >= 5:
                    _add_new(earned, "marathon")

        # Первая победа / новичкам везёт
        if total_wins >= 1:
            _add_new(earned, "first_blood")
            if matches[0].winner_id == player.id:
                _add_new(earned, "beginners_luck")

        # Стрики
        if max_win_streak >= 3:
            _add_new(earned, "hat_trick")
        if max_win_streak >= 5:
            _add_new(earned, "im_on_fire")
        if max_win_streak >= 10:
            _add_new(earned, "god_mode")

        # Феникс
        if had_phoenix:
            _add_new(earned, "phoenix")

        # ФК Тюмень: 5 поражений подряд
        if max_loss_streak >= 5:
            _add_new(earned, "fk_tyumen")

        # Неистого: любой день, где все матчи (от 3) — победы.
        # День считаем по МСК (даты в БД — naive-UTC), как и в realtime-проверках.
        day_groups: dict = {}
        for m in matches:
            if m.completed_at:
                day_groups.setdefault((m.completed_at + MSK_OFFSET).date(), []).append(m)
        for day_matches in day_groups.values():
            if len(day_matches) >= 3 and all(mm.winner_id == player.id for mm in day_matches):
                _add_new(earned, "relentless")
                break

        # Дипломат
        if total_draws >= 5:
            _add_new(earned, "diplomat")

        # Вехи
        if total >= 50:
            _add_new(earned, "fifty")
        if total >= 100:
            _add_new(earned, "veteran")
        if total >= 200:
            _add_new(earned, "legend")

        # Теннисный маньячелло: любой день (по МСК) с 10+ матчами
        day_counts = Counter(
            (m.completed_at + MSK_OFFSET).date() for m in matches if m.completed_at
        )
        if any(cnt >= 10 for cnt in day_counts.values()):
            _add_new(earned, "maniac")

        # То что мертво: 10+ побед подряд над одним соперником
        opp_win_streaks: dict[int, int] = {}
        for m in matches:
            opp_id = m.challenged_id if m.challenger_id == player.id else m.challenger_id
            if m.winner_id == player.id:
                opp_win_streaks[opp_id] = opp_win_streaks.get(opp_id, 0) + 1
                if opp_win_streaks[opp_id] >= 10:
                    _add_new(earned, "dominator")
            else:
                opp_win_streaks[opp_id] = 0

        # Со всеми познакомился
        other_ids = all_player_ids - {player.id}
        if other_ids and other_ids.issubset(beaten_opponents):
            _add_new(earned, "collector")

        # Рейтинг 1200 (по peak_rating)
        if player.peak_rating and player.peak_rating >= 1200.0:
            _add_new(earned, "rating_1200")

        # Дух Анкориджа: были отменённые (declined) матчи
        declined_r = await session.execute(
            select(func.count()).select_from(Match).where(
                or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
                Match.status == MatchStatus.declined,
            )
        )
        if declined_r.scalar() > 0:
            _add_new(earned, "anchorage_spirit")

        player.achievements = json.dumps(earned)
        player.backfill_version = BACKFILL_VERSION

    await session.commit()
