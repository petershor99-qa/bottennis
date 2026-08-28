"""
Тесты личных рекордов (bot/services/personal_records.py).
Запуск: pytest tests/test_personal_records.py -v
"""
from datetime import datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base, Match, MatchStatus, Player
from bot.services.personal_records import (
    check_personal_records_on_draw,
    check_personal_records_on_loss,
    check_personal_records_on_win,
)

_BASE_DT = datetime(2024, 1, 1, 12, 0, 0)


def _ts(days: int = 0, seconds: int = 0) -> datetime:
    return _BASE_DT + timedelta(days=days, seconds=seconds)


def _player(tid: int, name: str) -> Player:
    return Player(telegram_id=tid, display_name=name, rating=1000.0, achievements="[]", backfill_version=0)


def _add_match(
    session, p1: Player, p2: Player, winner: Player | None, sets: list[dict], dt: datetime,
) -> Match:
    """winner=None → ничья. sets — в перспективе challenger'а (p1)."""
    m = Match(
        challenger_id=p1.id,
        challenged_id=p2.id,
        status=MatchStatus.completed,
        winner_id=winner.id if winner else None,
        sets_data=sets,
        completed_at=dt,
        created_at=dt,
    )
    session.add(m)
    return m


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s


# ── Общий барьер: первый матч в карьере — сравнивать не с чем ────────────────

async def test_no_records_on_very_first_match_ever(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    m = _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts())
    await db.flush()

    assert await check_personal_records_on_win(db, p1, m) == []


# ── #1 Серия побед подряд ─────────────────────────────────────────────────────

async def test_win_streak_record_fires_on_second_win_in_a_row(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(0))
    m2 = _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(1))
    await db.flush()

    msgs = await check_personal_records_on_win(db, p1, m2)
    assert any("RAMPAGE" in t and "2 побед подряд" in t for t in msgs)


async def test_win_streak_record_not_fired_on_first_ever_win(db):
    """Матчей уже 2 (есть с чем сравнивать формально), но первая победа —
    ещё не рекорд: до неё серии побед не было вообще (before == 0)."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    _add_match(db, p2, p1, p2, [{"w": 11, "l": 5}], _ts(0))  # p1 проиграл
    m2 = _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(1))  # первая победа p1
    await db.flush()

    msgs = await check_personal_records_on_win(db, p1, m2)
    assert not any("RAMPAGE" in t for t in msgs)


async def test_win_streak_record_not_fired_when_tying_previous_best(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    # серия из 2 побед, потом поражение (стрик обнулился, лучший результат остался 2)
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(0))
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(1))
    _add_match(db, p2, p1, p2, [{"w": 11, "l": 5}], _ts(2))
    # новая победа — стрик снова 1, это не рекорд (лучший всё ещё 2)
    m4 = _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(3))
    await db.flush()

    msgs = await check_personal_records_on_win(db, p1, m4)
    assert not any("RAMPAGE" in t for t in msgs)


# ── #2 Серия без поражений (через ничью) ──────────────────────────────────────

async def test_no_loss_streak_record_fires_via_draw(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(0))
    m2 = _add_match(db, p1, p2, None, [{"w": 11, "l": 9}, {"w": 9, "l": 11}], _ts(1))
    await db.flush()

    msgs = await check_personal_records_on_draw(db, p1, m2)
    assert any("Ты не пройдёшь" in t and "2 матчей" in t for t in msgs)


# ── #3 Побед за один день ─────────────────────────────────────────────────────

async def test_wins_in_a_day_record_fires(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(days=0))            # день 1: 1 победа
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(days=1))            # день 2: 1-я победа
    m3 = _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(days=1, seconds=3600))  # день 2: 2-я победа
    await db.flush()

    msgs = await check_personal_records_on_win(db, p1, m3)
    assert any("День сурка" in t and "2 побед за сегодня" in t for t in msgs)


# ── #4 Разгром по очкам ────────────────────────────────────────────────────────

async def test_margin_record_fires(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 6}], _ts(0))            # margin 5
    m2 = _add_match(db, p1, p2, p1, [{"w": 11, "l": 3}], _ts(1))       # margin 8
    await db.flush()

    msgs = await check_personal_records_on_win(db, p1, m2)
    assert any("Hasta la vista" in t and "8 очков" in t for t in msgs)


async def test_margin_record_not_fired_on_first_ever_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    _add_match(db, p2, p1, p2, [{"w": 11, "l": 5}], _ts(0))
    m2 = _add_match(db, p1, p2, p1, [{"w": 11, "l": 0}], _ts(1))
    await db.flush()

    msgs = await check_personal_records_on_win(db, p1, m2)
    assert not any("Hasta la vista" in t for t in msgs)


# ── #5 Самый долгий матч (любой исход) ─────────────────────────────────────────

async def test_longest_match_record_fires_on_win(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}] * 3, _ts(0))
    m2 = _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}] * 5, _ts(1))
    await db.flush()

    msgs = await check_personal_records_on_win(db, p1, m2)
    assert any("Санта Барбара" in t and "5 партий" in t for t in msgs)


async def test_longest_match_record_fires_on_loss(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    _add_match(db, p2, p1, p2, [{"w": 11, "l": 5}] * 3, _ts(0))
    m2 = _add_match(db, p2, p1, p2, [{"w": 11, "l": 5}] * 5, _ts(1))
    await db.flush()

    msgs = await check_personal_records_on_loss(db, p1, m2)
    assert any("Санта Барбара" in t and "5 партий" in t for t in msgs)


async def test_longest_match_record_fires_on_draw(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    _add_match(db, p1, p2, None, [{"w": 11, "l": 9}, {"w": 9, "l": 11}], _ts(0))
    m2 = _add_match(
        db, p1, p2, None,
        [{"w": 11, "l": 9}, {"w": 9, "l": 11}, {"w": 11, "l": 9}, {"w": 9, "l": 11}],
        _ts(1),
    )
    await db.flush()

    msgs = await check_personal_records_on_draw(db, p1, m2)
    assert any("Санта Барбара" in t and "4 партий" in t for t in msgs)


# ── #6 Больше всего очков за матч ─────────────────────────────────────────────

async def test_most_points_record_fires(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 9}], _ts(0))          # 20 очков
    m2 = _add_match(db, p1, p2, p1, [{"w": 11, "l": 9}] * 3, _ts(1))  # 60 очков
    await db.flush()

    msgs = await check_personal_records_on_win(db, p1, m2)
    assert any("Война и мир" in t and "60 очков" in t for t in msgs)


# ── #7 Серия побед над одним соперником ───────────────────────────────────────

async def test_streak_vs_single_opponent_record_fires(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(0))   # 1-я против p2
    _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(1))   # 2-я против p2 подряд
    _add_match(db, p1, p3, p1, [{"w": 11, "l": 5}], _ts(2))   # против p3 — не мешает счётчику p2
    m4 = _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(3))  # 3-я против p2 подряд — новый рекорд
    await db.flush()

    msgs = await check_personal_records_on_win(db, p1, m4)
    assert any("личный кошмар" in t and "3 побед подряд" in t for t in msgs)


async def test_streak_vs_single_opponent_not_fired_on_first_win_ever(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    _add_match(db, p3, p1, p3, [{"w": 11, "l": 5}], _ts(0))  # p1 проиграл p3
    m2 = _add_match(db, p1, p2, p1, [{"w": 11, "l": 5}], _ts(1))  # первая победа вообще
    await db.flush()

    msgs = await check_personal_records_on_win(db, p1, m2)
    assert not any("личный кошмар" in t for t in msgs)
