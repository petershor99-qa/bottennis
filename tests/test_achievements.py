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
    check_draw_achievements,
    check_loss_achievements,
    check_win_achievements,
    get_achievements,
)

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
    sets=None, dt: datetime = None,
) -> Match:
    """Добавить в БД завершённый матч с победителем."""
    m = Match(
        challenger_id=winner.id,
        challenged_id=loser.id,
        status=MatchStatus.completed,
        winner_id=winner.id,
        sets_data=sets or _DEFAULT_SETS,
        completed_at=dt or _ts(),
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
    dt: datetime = None,
) -> list[str]:
    """Добавить победный матч в БД и вызвать check_win_achievements."""
    sets = sets or _DEFAULT_SETS
    m = await _add_win(session, winner, loser, sets=sets, dt=dt or _ts())
    return await check_win_achievements(
        session, winner, loser, sets, m.id, old_wr, old_lr,
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

async def test_highlander_when_winner_is_top_rated(db):
    p1 = _player(1, "Alice", rating=1050.0)
    p2 = _player(2, "Bob", rating=1000.0)
    db.add_all([p1, p2])
    await db.flush()

    new = await _do_win(db, p1, p2)
    assert "highlander" in new


async def test_no_highlander_when_not_top_rated(db):
    p1 = _player(1, "Alice", rating=900.0)
    p2 = _player(2, "Bob", rating=1100.0)
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

    today = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    for i in range(9):
        await _add_win(db, p1, p2, dt=today + timedelta(minutes=i))
    new = await _do_win(db, p1, p2, dt=today + timedelta(minutes=9))
    assert "maniac" in new


async def test_no_maniac_with_old_matches(db):
    """9 матчей вчера + 1 сегодня — maniac не даётся."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    yesterday = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0) \
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
