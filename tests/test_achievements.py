"""
Тесты системы достижений.
Запуск: pytest tests/test_achievements.py -v
"""
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base, Match, MatchStatus, Player
from bot.services.achievements import (
    BACKFILL_VERSION,
    backfill_achievements,
    check_cancel_achievements,
    check_draw_achievements,
    check_loss_achievements,
    check_win_achievements,
    get_achievements,
)
from bot.utils import msk_day_start

# ── Fixtures & helpers ─────────────────────────────────────────────────────────

_BASE_DT = datetime(2024, 1, 1, 12, 0, 0)


def _ts(i: int = 0) -> datetime:
    """Детерминированная метка времени: base + i секунд."""
    return _BASE_DT + timedelta(seconds=i)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _player(tid: int, name: str, rating: float = 1000.0) -> Player:
    return Player(
        telegram_id=tid,
        display_name=name,
        rating=rating,
        achievements="[]",
        backfill_version=0,
    )


_DEFAULT_SETS = [{"w": 11, "l": 7}, {"w": 11, "l": 7}]


async def _add_win(
    session, winner: Player, loser: Player,
    sets=None, dt: datetime = None, created_at: datetime = None,
) -> Match:
    """Добавить в БД завершённый матч с победителем."""
    completed = dt or _ts()
    m = Match(
        challenger_id=winner.id,
        challenged_id=loser.id,
        status=MatchStatus.completed,
        winner_id=winner.id,
        sets_data=sets or _DEFAULT_SETS,
        completed_at=completed,
        created_at=created_at or completed,
    )
    session.add(m)
    await session.flush()
    return m


async def _add_draw(
    session, p1: Player, p2: Player,
    sets=None, dt: datetime = None,
) -> Match:
    """Добавить в БД завершённый матч-ничья."""
    m = Match(
        challenger_id=p1.id,
        challenged_id=p2.id,
        status=MatchStatus.completed,
        winner_id=None,
        sets_data=sets or _DEFAULT_SETS,
        completed_at=dt or _ts(),
    )
    session.add(m)
    await session.flush()
    return m


async def _do_win(
    session, winner: Player, loser: Player,
    sets=None, old_wr: float = 1000.0, old_lr: float = 1000.0,
    dt: datetime = None, created_at: datetime = None,
) -> list[str]:
    """Добавить победный матч в БД и вызвать check_win_achievements."""
    sets = sets or _DEFAULT_SETS
    m = await _add_win(session, winner, loser, sets=sets, dt=dt or _ts(), created_at=created_at)
    return await check_win_achievements(
        session, winner, loser, m, old_wr, old_lr,
    )


# ── press_start ────────────────────────────────────────────────────────────────

async def test_press_start_on_first_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2)
    assert "press_start" in new


async def test_press_start_on_first_loss(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    await _add_win(db, p2, p1)
    new = await check_loss_achievements(db, p1, _DEFAULT_SETS)
    assert "press_start" in new


async def test_press_start_not_repeated(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    await _do_win(db, p1, p2, dt=_ts(0))
    new2 = await _do_win(db, p1, p2, dt=_ts(1))
    assert "press_start" not in new2


# ── first_blood / beginners_luck ───────────────────────────────────────────────

async def test_first_blood_and_beginners_luck_on_first_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2)
    assert "first_blood" in new
    assert "beginners_luck" in new


async def test_no_beginners_luck_after_first_loss(db):
    """Первый матч — проигрыш. Следующий — победа. beginners_luck не даётся."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    await _add_win(db, p2, p1, dt=_ts(0))  # p1 проигрывает первый матч
    new = await _do_win(db, p1, p2, dt=_ts(1))

    assert "first_blood" in new
    assert "beginners_luck" not in new


# ── hat_trick / im_on_fire / god_mode ─────────────────────────────────────────

async def test_hat_trick_after_3_consecutive_wins(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    await _add_win(db, p1, p2, dt=_ts(0))
    await _add_win(db, p1, p2, dt=_ts(1))
    new = await _do_win(db, p1, p2, dt=_ts(2))
    assert "hat_trick" in new


async def test_hat_trick_resets_on_loss(db):
    """Победа-поражение-победа-победа — стрик только 2, hat_trick не даётся."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    await _add_win(db, p1, p2, dt=_ts(0))
    await _add_win(db, p2, p1, dt=_ts(1))  # p1 проигрывает
    await _add_win(db, p1, p2, dt=_ts(2))
    new = await _do_win(db, p1, p2, dt=_ts(3))
    assert "hat_trick" not in new


async def test_im_on_fire_after_5_consecutive_wins(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(4):
        await _add_win(db, p1, p2, dt=_ts(i))
    new = await _do_win(db, p1, p2, dt=_ts(4))
    assert "im_on_fire" in new
    assert "hat_trick" in new  # hat_trick тоже должна быть


async def test_god_mode_after_10_consecutive_wins(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(9):
        await _add_win(db, p1, p2, dt=_ts(i))
    new = await _do_win(db, p1, p2, dt=_ts(9))
    assert "god_mode" in new


# ── phoenix ────────────────────────────────────────────────────────────────────

async def test_phoenix_after_3_consecutive_losses(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(3):
        await _add_win(db, p2, p1, dt=_ts(i))
    new = await _do_win(db, p1, p2, dt=_ts(3))
    assert "phoenix" in new


async def test_no_phoenix_after_only_2_losses(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(2):
        await _add_win(db, p2, p1, dt=_ts(i))
    new = await _do_win(db, p1, p2, dt=_ts(2))
    assert "phoenix" not in new


# ── highlander ─────────────────────────────────────────────────────────────────
# Перепривязано в v2.82.0 (боссфайт): было "рейтинг выше всех", стало "владеет
# местом #1" (Player.is_champion) — место #1 больше не занимается по очкам.

async def test_highlander_when_winner_is_champion(db):
    p1 = _player(1, "Alice", rating=1050.0)
    p2 = _player(2, "Bob", rating=1000.0)
    p1.is_champion = True
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2)
    assert "highlander" in new


async def test_no_highlander_when_top_rated_but_not_champion(db):
    """Высокий рейтинг сам по себе больше не даёт ачивку — нужен реальный
    статус чемпиона (is_champion), а не просто топ-1 по очкам."""
    p1 = _player(1, "Alice", rating=1050.0)
    p2 = _player(2, "Bob", rating=1000.0)
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2)
    assert "highlander" not in new


# ── david_goliath ──────────────────────────────────────────────────────────────

async def test_david_goliath_opponent_100_pts_higher(db):
    p1, p2 = _player(1, "Alice", 900.0), _player(2, "Bob", 1100.0)
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2, old_wr=900.0, old_lr=1100.0)
    assert "david_goliath" in new


async def test_no_david_goliath_when_gap_below_100(db):
    p1, p2 = _player(1, "Alice", 950.0), _player(2, "Bob", 1049.0)
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2, old_wr=950.0, old_lr=1049.0)
    assert "david_goliath" not in new


# ── marathon ───────────────────────────────────────────────────────────────────

async def test_marathon_5_sets_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets5 = [{"w": 11, "l": 9}, {"w": 9, "l": 11}, {"w": 11, "l": 9},
             {"w": 9, "l": 11}, {"w": 11, "l": 7}]
    new = await _do_win(db, p1, p2, sets=sets5)
    assert "marathon" in new


async def test_marathon_5_sets_loss(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets5 = [{"w": 11, "l": 9}, {"w": 9, "l": 11}, {"w": 11, "l": 9},
             {"w": 9, "l": 11}, {"w": 11, "l": 7}]
    await _add_win(db, p2, p1, sets=sets5)
    new = await check_loss_achievements(db, p1, sets5)
    assert "marathon" in new


async def test_no_marathon_with_4_sets(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets4 = [{"w": 11, "l": 7}] * 4
    new = await _do_win(db, p1, p2, sets=sets4)
    assert "marathon" not in new


# ── fatality ───────────────────────────────────────────────────────────────────

async def test_fatality_no_sets_lost(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets = [{"w": 11, "l": 7}, {"w": 11, "l": 3}]
    new = await _do_win(db, p1, p2, sets=sets)
    assert "fatality" in new


async def test_no_fatality_when_set_lost(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets = [{"w": 11, "l": 7}, {"w": 7, "l": 11}, {"w": 11, "l": 5}]
    new = await _do_win(db, p1, p2, sets=sets)
    assert "fatality" not in new


async def test_no_fatality_with_single_set(db):
    """Минимум 2 партии — fatality за 1 партию не даётся."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets = [{"w": 11, "l": 0}]
    new = await _do_win(db, p1, p2, sets=sets)
    assert "fatality" not in new


# ── no_sweat ───────────────────────────────────────────────────────────────────

async def test_no_sweat_winner_wins_set_11_0(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets = [{"w": 11, "l": 0}, {"w": 11, "l": 7}]
    new = await _do_win(db, p1, p2, sets=sets)
    assert "no_sweat" in new


async def test_no_sweat_loser_wins_set_11_0(db):
    """Проигравший выиграл одну партию 11:0 — тоже получает no_sweat."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    # p2 выигрывает матч; p1 выигрывает одну партию 11:0
    # sets_data с позиции победителя (p2): {"w": 0, "l": 11} = p1 выиграл эту партию
    sets = [{"w": 11, "l": 7}, {"w": 11, "l": 7}, {"w": 0, "l": 11}]
    await _add_win(db, p2, p1, sets=sets)
    new = await check_loss_achievements(db, p1, sets)
    assert "no_sweat" in new


# ── diplomat ──────────────────────────────────────────────────────────────────

async def test_diplomat_after_5_draws(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(5):
        await _add_draw(db, p1, p2, dt=_ts(i))
    new = await check_draw_achievements(db, p1, _DEFAULT_SETS, is_challenger=True)
    assert "diplomat" in new


async def test_no_diplomat_with_only_4_draws(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(4):
        await _add_draw(db, p1, p2, dt=_ts(i))
    new = await check_draw_achievements(db, p1, _DEFAULT_SETS, is_challenger=True)
    assert "diplomat" not in new


# ── revenge ────────────────────────────────────────────────────────────────────

async def test_revenge_beats_last_defeater(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    await _add_win(db, p2, p1, dt=_ts(0))  # p2 обыгрывает p1
    new = await _do_win(db, p1, p2, dt=_ts(1))  # p1 берёт реванш
    assert "revenge" in new


async def test_no_revenge_on_first_h2h_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2)  # первый матч между ними
    assert "revenge" not in new


# ── dominator ─────────────────────────────────────────────────────────────────

async def test_dominator_10_consecutive_wins_vs_same(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(9):
        await _add_win(db, p1, p2, dt=_ts(i))
    new = await _do_win(db, p1, p2, dt=_ts(9))
    assert "dominator" in new


async def test_no_dominator_when_streak_broken(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(8):
        await _add_win(db, p1, p2, dt=_ts(i))
    await _add_win(db, p2, p1, dt=_ts(8))  # p2 прерывает серию
    new = await _do_win(db, p1, p2, dt=_ts(9))  # p1 выигрывает, но серия = 1
    assert "dominator" not in new


# ── fifty / veteran ────────────────────────────────────────────────────────────

async def test_fifty_at_50th_match(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(49):
        await _add_win(db, p1, p2, dt=_ts(i))
    new = await _do_win(db, p1, p2, dt=_ts(49))
    assert "fifty" in new
    assert "veteran" not in new  # ещё не 100


async def test_veteran_at_100th_match(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(99):
        await _add_win(db, p1, p2, dt=_ts(i))
    new = await _do_win(db, p1, p2, dt=_ts(99))
    assert "fifty" in new
    assert "veteran" in new


# ── maniac ─────────────────────────────────────────────────────────────────────

async def test_maniac_10_matches_today(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    today = msk_day_start() + timedelta(hours=9)
    for i in range(9):
        await _add_win(db, p1, p2, dt=today + timedelta(minutes=i))
    new = await _do_win(db, p1, p2, dt=today + timedelta(minutes=9))
    assert "maniac" in new


async def test_no_maniac_with_old_matches(db):
    """9 матчей вчера + 1 сегодня — maniac не даётся."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    yesterday = msk_day_start() + timedelta(hours=10) \
                - timedelta(days=1)
    for i in range(9):
        await _add_win(db, p1, p2, dt=yesterday + timedelta(minutes=i))
    new = await _do_win(db, p1, p2, dt=datetime.now(timezone.utc))
    assert "maniac" not in new


# ── collector ─────────────────────────────────────────────────────────────────

async def test_collector_beats_all_players(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Charlie")
    db.add_all([p1, p2, p3])
    await db.flush()

    await _add_win(db, p1, p2, dt=_ts(0))
    new = await _do_win(db, p1, p3, dt=_ts(1))
    assert "collector" in new


async def test_no_collector_missing_one_opponent(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Charlie")
    db.add_all([p1, p2, p3])
    await db.flush()

    new = await _do_win(db, p1, p2)  # p1 победил только p2, не p3
    assert "collector" not in new


# ── rating_1200 ────────────────────────────────────────────────────────────────

async def test_rating_1200_at_threshold(db):
    p1 = _player(1, "Alice", rating=1200.0)
    p2 = _player(2, "Bob", rating=1000.0)
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2)
    assert "rating_1200" in new


async def test_no_rating_1200_below_threshold(db):
    p1 = _player(1, "Alice", rating=1199.9)
    p2 = _player(2, "Bob", rating=1000.0)
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2)
    assert "rating_1200" not in new


# ── idempotency ────────────────────────────────────────────────────────────────

# ── takova_zhis ─────────────────────────────────────────────────────────────────

async def test_takova_zhis_after_6_alternating_matches(db):
    """W-L-W-L-W-L (с точки зрения p1) — ачивка даётся на 6-м матче."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = None
    for i, p1_wins in enumerate([True, False, True, False, True, False]):
        if p1_wins:
            new = await _do_win(db, p1, p2, dt=_ts(i))
        else:
            await _add_win(db, p2, p1, dt=_ts(i))
            new = await check_loss_achievements(db, p1, _DEFAULT_SETS)

    assert "takova_zhis" in new


async def test_no_takova_zhis_after_5_alternating_matches(db):
    """Только 5 матчей чередования — рано, ачивки ещё нет."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = None
    for i, p1_wins in enumerate([True, False, True, False, True]):
        if p1_wins:
            new = await _do_win(db, p1, p2, dt=_ts(i))
        else:
            await _add_win(db, p2, p1, dt=_ts(i))
            new = await check_loss_achievements(db, p1, _DEFAULT_SETS)

    assert "takova_zhis" not in new


async def test_no_takova_zhis_when_draw_breaks_chain(db):
    """Ничья внутри окна рвёт цепочку чередования — ачивка не даётся."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    outcomes = [True, False, True, False, True]  # W-L-W-L-W — почти цепочка
    for i, p1_wins in enumerate(outcomes):
        if p1_wins:
            await _do_win(db, p1, p2, dt=_ts(i))
        else:
            await _add_win(db, p2, p1, dt=_ts(i))
            await check_loss_achievements(db, p1, _DEFAULT_SETS)

    # 6-й матч — ничья, разрывает цепочку вместо её завершения
    await _add_draw(db, p1, p2, dt=_ts(5))
    new = await check_draw_achievements(db, p1, _DEFAULT_SETS, is_challenger=True)

    assert "takova_zhis" not in new
    assert "takova_zhis" not in get_achievements(p1)


async def test_takova_zhis_not_given_for_2_wins_in_a_row(db):
    """W-L-W-L-W-W — последние 6 не чередуются (два подряд), ачивки нет."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = None
    for i, p1_wins in enumerate([True, False, True, False, True, True]):
        if p1_wins:
            new = await _do_win(db, p1, p2, dt=_ts(i))
        else:
            await _add_win(db, p2, p1, dt=_ts(i))
            new = await check_loss_achievements(db, p1, _DEFAULT_SETS)

    assert "takova_zhis" not in new


async def test_no_duplicate_achievements(db):
    """Одно и то же достижение не добавляется дважды."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    await _do_win(db, p1, p2, dt=_ts(0))
    await _do_win(db, p1, p2, dt=_ts(1))

    earned = get_achievements(p1)
    assert len(earned) == len(set(earned)), "Найдены дублирующиеся достижения"


# ── backfill ───────────────────────────────────────────────────────────────────

async def test_backfill_assigns_basic_achievements(db):
    """backfill правильно назначает press_start, first_blood, beginners_luck."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, completed_at=_ts(),
    ))
    await db.flush()

    await backfill_achievements(db)

    assert "press_start" in get_achievements(p1)
    assert "press_start" in get_achievements(p2)
    assert "first_blood" in get_achievements(p1)
    assert "beginners_luck" in get_achievements(p1)


async def test_backfill_assigns_hat_trick(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(3):
        db.add(Match(
            challenger_id=p1.id, challenged_id=p2.id,
            status=MatchStatus.completed, winner_id=p1.id,
            sets_data=_DEFAULT_SETS, completed_at=_ts(i),
        ))
    await db.flush()

    await backfill_achievements(db)
    assert "hat_trick" in get_achievements(p1)


async def test_backfill_assigns_takova_zhis(db):
    """backfill находит цепочку чередования 6 матчей по истории."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    outcomes = [True, False, True, False, True, False]  # p1: W-L-W-L-W-L
    for i, p1_wins in enumerate(outcomes):
        winner, loser = (p1, p2) if p1_wins else (p2, p1)
        db.add(Match(
            challenger_id=winner.id, challenged_id=loser.id,
            status=MatchStatus.completed, winner_id=winner.id,
            sets_data=_DEFAULT_SETS, completed_at=_ts(i),
        ))
    await db.flush()

    await backfill_achievements(db)
    assert "takova_zhis" in get_achievements(p1)


async def test_backfill_sets_backfill_version(db):
    """После backfill у игрока с матчами выставляется backfill_version."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, completed_at=_ts(),
    ))
    await db.flush()

    await backfill_achievements(db)

    assert p1.backfill_version == BACKFILL_VERSION
    assert p2.backfill_version == BACKFILL_VERSION


async def test_backfill_skips_already_processed_players(db):
    """Игрок с актуальным backfill_version не обрабатывается повторно."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    p1.backfill_version = BACKFILL_VERSION  # уже обработан
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, completed_at=_ts(),
    ))
    await db.flush()

    await backfill_achievements(db)

    # p1 пропущен — достижений нет
    assert "press_start" not in get_achievements(p1)
    # p2 обработан — press_start есть
    assert "press_start" in get_achievements(p2)


# ── НОВЫЕ АЧИВКИ ─────────────────────────────────────────────────────────────

# comeback (CumБэк)

async def test_comeback_win_after_losing_first_two_sets(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    # winner perspective: проиграл первые 2 партии, выиграл матч 3:2
    sets = [{"w": 9, "l": 11}, {"w": 7, "l": 11}, {"w": 11, "l": 7},
            {"w": 11, "l": 9}, {"w": 11, "l": 8}]
    new = await _do_win(db, p1, p2, sets=sets)
    assert "comeback" in new


async def test_no_comeback_when_first_two_sets_won(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets = [{"w": 11, "l": 7}, {"w": 11, "l": 9}]
    new = await _do_win(db, p1, p2, sets=sets)
    assert "comeback" not in new


# titans (Битва такеши титанов)

async def test_titans_when_both_1100(db):
    p1, p2 = _player(1, "Alice", 1100.0), _player(2, "Bob", 1100.0)
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2, old_wr=1100.0, old_lr=1100.0)
    assert "titans" in new


async def test_no_titans_when_loser_below_1100(db):
    p1, p2 = _player(1, "Alice", 1150.0), _player(2, "Bob", 1050.0)
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2, old_wr=1150.0, old_lr=1050.0)
    assert "titans" not in new


# deuce_maker (Дьюсмейкер)

async def test_deuce_maker_winner_wins_deuce_set(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets = [{"w": 12, "l": 10}, {"w": 11, "l": 7}]
    new = await _do_win(db, p1, p2, sets=sets)
    assert "deuce_maker" in new


async def test_no_deuce_maker_without_deuce(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets = [{"w": 11, "l": 9}, {"w": 11, "l": 7}]
    new = await _do_win(db, p1, p2, sets=sets)
    assert "deuce_maker" not in new


async def test_deuce_maker_loser_wins_deuce_set(db):
    """Проигравший взял партию на дьюсе — тоже получает дьюсмейкера."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    # p2 выиграл матч; p1 (проигравший) взял партию 12:10
    sets = [{"w": 11, "l": 7}, {"w": 10, "l": 12}, {"w": 11, "l": 7}]
    await _add_win(db, p2, p1, sets=sets)
    new = await check_loss_achievements(db, p1, sets)
    assert "deuce_maker" in new


# fk_tyumen (ФК Тюмень — 5 поражений подряд)

async def test_fk_tyumen_after_5_losses(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(5):
        await _add_win(db, p2, p1, dt=_ts(i))  # p1 проигрывает 5 раз
    new = await check_loss_achievements(db, p1, _DEFAULT_SETS)
    assert "fk_tyumen" in new


async def test_no_fk_tyumen_after_4_losses(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(4):
        await _add_win(db, p2, p1, dt=_ts(i))
    new = await check_loss_achievements(db, p1, _DEFAULT_SETS)
    assert "fk_tyumen" not in new


# relentless (Неистого — все матчи за день победы, от 3)

async def test_relentless_three_wins_same_day(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    today = msk_day_start() + timedelta(hours=9)
    await _add_win(db, p1, p2, dt=today)
    await _add_win(db, p1, p2, dt=today + timedelta(minutes=1))
    new = await _do_win(db, p1, p2, dt=today + timedelta(minutes=2))
    assert "relentless" in new


async def test_no_relentless_with_a_loss_today(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    today = msk_day_start() + timedelta(hours=9)
    await _add_win(db, p1, p2, dt=today)
    await _add_win(db, p2, p1, dt=today + timedelta(minutes=1))  # поражение p1
    new = await _do_win(db, p1, p2, dt=today + timedelta(minutes=2))
    assert "relentless" not in new


# night_king (Король ночи — обыграть всех игроков клуба за один день)

async def test_night_king_beats_all_others_today(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Charlie")
    db.add_all([p1, p2, p3])
    await db.flush()

    today = msk_day_start() + timedelta(hours=9)
    await _add_win(db, p1, p2, dt=today)
    new = await _do_win(db, p1, p3, dt=today + timedelta(minutes=1))
    assert "night_king" in new


async def test_no_night_king_missing_one_opponent_today(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Charlie")
    db.add_all([p1, p2, p3])
    await db.flush()

    today = msk_day_start() + timedelta(hours=9)
    new = await _do_win(db, p1, p2, dt=today)  # Charlie не побеждён
    assert "night_king" not in new


async def test_no_night_king_when_beaten_on_different_days(db):
    """Обыграл всех, но не в один день — Король ночи не даётся."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Charlie")
    db.add_all([p1, p2, p3])
    await db.flush()

    yesterday = msk_day_start() + timedelta(hours=9) \
                - timedelta(days=1)
    today = yesterday + timedelta(days=1)
    await _add_win(db, p1, p2, dt=yesterday)
    new = await _do_win(db, p1, p3, dt=today)
    assert "night_king" not in new


# anchorage_spirit (Дух Анкориджа — отмена матча)

async def test_anchorage_spirit_on_cancel(db):
    p1 = _player(1, "Alice")
    db.add(p1)
    await db.flush()

    new = await check_cancel_achievements(db, p1)
    assert "anchorage_spirit" in new


async def test_anchorage_spirit_not_repeated(db):
    p1 = _player(1, "Alice")
    db.add(p1)
    await db.flush()

    await check_cancel_achievements(db, p1)
    new2 = await check_cancel_achievements(db, p1)
    assert "anchorage_spirit" not in new2


# backfill новых ачивок

async def test_backfill_fk_tyumen(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    for i in range(5):
        db.add(Match(
            challenger_id=p2.id, challenged_id=p1.id,
            status=MatchStatus.completed, winner_id=p2.id,
            sets_data=_DEFAULT_SETS, completed_at=_ts(i),
        ))
    await db.flush()

    await backfill_achievements(db)
    assert "fk_tyumen" in get_achievements(p1)


async def test_backfill_assigns_night_king(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Charlie")
    db.add_all([p1, p2, p3])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, completed_at=_ts(0),
    ))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p3.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, completed_at=_ts(1),
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "night_king" in get_achievements(p1)


async def test_backfill_anchorage_from_declined_match(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    # один завершённый (чтобы игрок попал в обработку) + один отменённый
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, completed_at=_ts(0),
    ))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.declined, completed_at=None,
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "anchorage_spirit" in get_achievements(p1)


async def test_backfill_idempotent(db):
    """Повторный запуск backfill не дублирует достижения."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, completed_at=_ts(),
    ))
    await db.flush()

    await backfill_achievements(db)
    earned_first = sorted(get_achievements(p1))

    # Имитируем повторный запуск: сбрасываем версию
    p1.backfill_version = 0
    p2.backfill_version = 0
    await db.flush()
    await backfill_achievements(db)
    earned_second = sorted(get_achievements(p1))

    assert earned_first == earned_second


# ── terminator_slain («Вынес терминатора») ──────────────────────────────────

async def test_terminator_slain_on_beating_5_win_streak(db):
    """p2 идёт с 5 победами подряд над p3, p1 обрывает эту серию — ачивка."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()

    for i in range(5):
        await _add_win(db, p2, p3, dt=_ts(i))  # p2: 5 побед подряд над p3
    new = await _do_win(db, p1, p2, dt=_ts(5))  # p1 обрывает серию p2
    assert "terminator_slain" in new


async def test_no_terminator_slain_on_4_win_streak(db):
    """Серии всего 4 победы — недостаточно, ачивки нет."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()

    for i in range(4):
        await _add_win(db, p2, p3, dt=_ts(i))
    new = await _do_win(db, p1, p2, dt=_ts(4))
    assert "terminator_slain" not in new


async def test_no_terminator_slain_when_streak_broken_before(db):
    """Серия p2 уже была прервана раньше — на момент этого матча стрик = 1."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()

    for i in range(5):
        await _add_win(db, p2, p3, dt=_ts(i))
    await _add_win(db, p3, p2, dt=_ts(5))       # p3 прерывает серию p2
    await _add_win(db, p2, p3, dt=_ts(6))       # p2 выигрывает один раз (стрик=1)
    new = await _do_win(db, p1, p2, dt=_ts(7))  # p1 побеждает p2 со стриком всего 1
    assert "terminator_slain" not in new


async def test_terminator_slain_not_given_to_the_streak_owner(db):
    """Достижение — победителю, а не тому, у кого была серия."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()

    for i in range(5):
        await _add_win(db, p2, p3, dt=_ts(i))
    await _do_win(db, p1, p2, dt=_ts(5))
    assert "terminator_slain" not in get_achievements(p2)
    assert "terminator_slain" in get_achievements(p1)


async def test_backfill_assigns_terminator_slain(db):
    """backfill находит club-wide серию соперника и награждает победителя."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()

    for i in range(5):
        db.add(Match(
            challenger_id=p2.id, challenged_id=p3.id,
            status=MatchStatus.completed, winner_id=p2.id,
            sets_data=_DEFAULT_SETS, completed_at=_ts(i),
        ))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, completed_at=_ts(5),
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "terminator_slain" in get_achievements(p1)
    assert "terminator_slain" not in get_achievements(p2)


# ── night_owl ─────────────────────────────────────────────────────────────────

async def test_night_owl_fires_on_night_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    # 2024-01-01 01:00 UTC -> 04:00 МСК — ночь
    new = await _do_win(db, p1, p2, dt=datetime(2024, 1, 1, 1, 0, 0))
    assert "night_owl" in new


async def test_night_owl_silent_on_daytime_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2, dt=_ts())  # 12:00 UTC -> 15:00 МСК
    assert "night_owl" not in new


async def test_backfill_assigns_night_owl(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, completed_at=datetime(2024, 1, 1, 1, 0, 0),
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "night_owl" in get_achievements(p1)


# ── deuce_storm ───────────────────────────────────────────────────────────────

_ALL_DEUCE_SETS = [{"w": 12, "l": 10}, {"w": 11, "l": 13}, {"w": 14, "l": 12}]


async def test_deuce_storm_fires_when_every_set_is_deuce(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2, sets=_ALL_DEUCE_SETS)
    assert "deuce_storm" in new


async def test_no_deuce_storm_when_one_set_is_not_deuce(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets = [{"w": 12, "l": 10}, {"w": 11, "l": 7}]  # вторая партия не на дьюсе
    new = await _do_win(db, p1, p2, sets=sets)
    assert "deuce_storm" not in new


async def test_backfill_assigns_deuce_storm(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_ALL_DEUCE_SETS, completed_at=_ts(),
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "deuce_storm" in get_achievements(p1)


# ── no_rest_win ───────────────────────────────────────────────────────────────

async def test_no_rest_win_fires_within_10_minutes(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    await _add_win(db, p1, p2, dt=_ts(0))
    new = await _do_win(db, p2, p1, dt=_ts(300), created_at=_ts(300))
    assert "no_rest_win" in new


async def test_no_rest_win_silent_when_gap_too_large(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    await _add_win(db, p1, p2, dt=_ts(0))
    new = await _do_win(db, p2, p1, dt=_ts(3600), created_at=_ts(3600))
    assert "no_rest_win" not in new


async def test_no_rest_win_silent_without_prior_match(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2)
    assert "no_rest_win" not in new


async def test_backfill_assigns_no_rest_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, created_at=_ts(0), completed_at=_ts(0),
    ))
    db.add(Match(
        challenger_id=p2.id, challenged_id=p1.id,
        status=MatchStatus.completed, winner_id=p2.id,
        sets_data=_DEFAULT_SETS, created_at=_ts(300), completed_at=_ts(300),
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "no_rest_win" in get_achievements(p2)
    assert "no_rest_win" not in get_achievements(p1)


# ── round_hundred ─────────────────────────────────────────────────────────────

async def test_round_hundred_fires_directly(db):
    """Не полагается на формулу ELO — напрямую проверяет условие через winner.rating."""
    p1, p2 = _player(1, "Alice", rating=1000.0), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    p1.rating = 1100.0  # имитирует результат, попавший ровно на круглую цифру

    new = await check_win_achievements(
        db, p1, p2, await _add_win(db, p1, p2), 1090.0, 1000.0,
    )
    assert "round_hundred" in new


async def test_no_round_hundred_on_non_round_rating(db):
    p1, p2 = _player(1, "Alice", rating=1000.0), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    p1.rating = 1113.7

    new = await check_win_achievements(
        db, p1, p2, await _add_win(db, p1, p2), 1090.0, 1000.0,
    )
    assert "round_hundred" not in new


# ── absolute_zero ─────────────────────────────────────────────────────────────

_ALL_ZERO_SETS = [{"w": 11, "l": 0}, {"w": 11, "l": 0}]


async def test_absolute_zero_fires_when_every_set_is_11_0(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2, sets=_ALL_ZERO_SETS)
    assert "absolute_zero" in new


async def test_no_absolute_zero_when_one_set_is_not_11_0(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    sets = [{"w": 11, "l": 0}, {"w": 11, "l": 5}]
    new = await _do_win(db, p1, p2, sets=sets)
    assert "absolute_zero" not in new


async def test_backfill_assigns_absolute_zero(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_ALL_ZERO_SETS, completed_at=_ts(),
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "absolute_zero" in get_achievements(p1)


# ── weekend_warrior ───────────────────────────────────────────────────────────

_SATURDAY = datetime(2024, 1, 6, 12, 0, 0)   # суббота
_MONDAY = datetime(2024, 1, 8, 12, 0, 0)     # понедельник


async def test_weekend_warrior_fires_on_saturday_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2, dt=_SATURDAY)
    assert "weekend_warrior" in new


async def test_no_weekend_warrior_on_weekday_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2, dt=_MONDAY)
    assert "weekend_warrior" not in new


async def test_backfill_assigns_weekend_warrior(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=_DEFAULT_SETS, completed_at=_SATURDAY,
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "weekend_warrior" in get_achievements(p1)


# ── rock_bottom ───────────────────────────────────────────────────────────────

async def test_rock_bottom_fires_when_loser_hits_exactly_900(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob", rating=900.0)
    db.add_all([p1, p2])
    await db.flush()

    m = await _add_win(db, p1, p2)
    new = await check_loss_achievements(db, p2, _DEFAULT_SETS)
    assert "rock_bottom" in new
    assert m is not None


async def test_no_rock_bottom_when_not_exactly_900(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob", rating=905.3)
    db.add_all([p1, p2])
    await db.flush()

    await _add_win(db, p1, p2)
    new = await check_loss_achievements(db, p2, _DEFAULT_SETS)
    assert "rock_bottom" not in new


# ── full_circle_week ──────────────────────────────────────────────────────────

async def test_full_circle_week_fires_after_beating_everyone_within_7_days(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()

    await _do_win(db, p1, p2, dt=_ts(0))
    new = await _do_win(db, p1, p3, dt=_ts(1))
    assert "full_circle_week" in new


async def test_no_full_circle_week_when_one_opponent_never_beaten(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()

    new = await _do_win(db, p1, p2, dt=_ts(0))
    assert "full_circle_week" not in new


async def test_backfill_assigns_full_circle_week(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(0),
    ))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p3.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(1),
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "full_circle_week" in get_achievements(p1)


async def test_backfill_full_circle_week_respects_trailing_window(db):
    """Победа над p2 больше 7 дней назад не должна засчитываться в «Полный
    круг» — регресс на скользящее окно (recent_wins), заменившее полный скан
    matches на каждой победе."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()

    eight_days = 8 * 24 * 3600
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(0),
    ))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p3.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(eight_days),
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "full_circle_week" not in get_achievements(p1)


async def test_backfill_full_circle_week_fires_with_fresh_win_in_window(db):
    """Та же связка, но повторная победа над p2 внутри окна — уже засчитывается."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()

    eight_days = 8 * 24 * 3600
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(0),
    ))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(eight_days - 3600),
    ))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p3.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(eight_days),
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "full_circle_week" in get_achievements(p1)


# ── draw_double ───────────────────────────────────────────────────────────────

async def test_draw_double_fires_on_two_consecutive_draws(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    await _add_draw(db, p1, p2, dt=_ts(0))
    await _add_draw(db, p1, p2, dt=_ts(1))
    new = await check_draw_achievements(db, p1, _DEFAULT_SETS, is_challenger=True)
    assert "draw_double" in new


async def test_no_draw_double_on_single_draw(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    new = await check_draw_achievements(db, p1, _DEFAULT_SETS, is_challenger=True)
    assert "draw_double" not in new


async def test_backfill_assigns_draw_double(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=None, sets_data=_DEFAULT_SETS, completed_at=_ts(0),
    ))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=None, sets_data=_DEFAULT_SETS, completed_at=_ts(1),
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "draw_double" in get_achievements(p1)


# ── first_crown ───────────────────────────────────────────────────────────────

async def test_first_crown_fires_on_first_boss_fight_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    m = Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(0), is_boss_fight=True,
    )
    db.add(m)
    await db.flush()

    new = await check_win_achievements(db, p1, p2, m, 1000.0, 1000.0)
    assert "first_crown" in new


async def test_no_first_crown_on_second_boss_fight_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(0), is_boss_fight=True,
    ))
    await db.flush()
    m2 = Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(1), is_boss_fight=True,
    )
    db.add(m2)
    await db.flush()

    new = await check_win_achievements(db, p1, p2, m2, 1000.0, 1000.0)
    assert "first_crown" not in new


async def test_backfill_assigns_first_crown(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, sets_data=_DEFAULT_SETS, completed_at=_ts(0), is_boss_fight=True,
    ))
    await db.flush()

    await backfill_achievements(db)
    assert "first_crown" in get_achievements(p1)


# ── categories ────────────────────────────────────────────────────────────────

async def test_every_achievement_has_a_category():
    from bot.services.achievements import ACHIEVEMENTS_LIST, CATEGORY_ORDER
    assert set(CATEGORY_ORDER) == {a.category for a in ACHIEVEMENTS_LIST}
    for a in ACHIEVEMENTS_LIST:
        assert a.category, f"{a.id} has no category"
