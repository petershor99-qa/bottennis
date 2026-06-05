"""
Тесты хендлеров (поведение флоу, а не только чистая логика).

Мокаем Telegram-объекты (Message/CallbackQuery/Bot), но используем
настоящие FSM (MemoryStorage) и in-memory SQLite — чтобы проверять
реальный путь пользователя: вызов → ввод счёта → отмена.
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base, Match, MatchStatus, Player
from bot.handlers.challenge import do_cancel_match, send_challenge
from bot.handlers.match_result import handle_direct_score, process_set_score
from bot.services.achievements import get_achievements
from bot.states.states import MatchResultStates

# ── Фикстуры и хелперы ──────────────────────────────────────────────────────────

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
        telegram_id=tid, display_name=name, rating=rating,
        achievements="[]", backfill_version=0,
    )


def _state(user_id: int = 1, chat_id: int = 1) -> FSMContext:
    """Настоящий FSMContext на MemoryStorage."""
    key = StorageKey(bot_id=1, chat_id=chat_id, user_id=user_id)
    return FSMContext(storage=MemoryStorage(), key=key)


def _message(user_id: int, text: str) -> AsyncMock:
    m = AsyncMock()
    m.from_user = SimpleNamespace(id=user_id, username="u", full_name="U")
    m.text = text
    m.chat = SimpleNamespace(id=user_id)
    # message.answer(...) возвращает объект с .message_id
    m.answer = AsyncMock(return_value=SimpleNamespace(message_id=999))
    return m


def _callback(user_id: int, data: str) -> AsyncMock:
    cb = AsyncMock()
    cb.from_user = SimpleNamespace(id=user_id)
    cb.data = data
    cb.message = AsyncMock()
    cb.message.chat = SimpleNamespace(id=user_id)
    cb.message.message_id = 555
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


async def _accepted_match(db, challenger: Player, challenged: Player) -> Match:
    m = Match(
        challenger_id=challenger.id, challenged_id=challenged.id,
        status=MatchStatus.accepted, accepted_at=datetime(2026, 6, 1, 12, 0, 0),
    )
    db.add(m)
    await db.flush()
    return m


# ── handle_direct_score (прямой ввод счёта) ─────────────────────────────────────

async def test_direct_score_no_active_match_is_ignored(db):
    """Нет активного матча → хендлер молча выходит, FSM не запускается."""
    p1 = _player(1, "Alice")
    db.add(p1)
    await db.flush()

    msg, st = _message(1, "11:7"), _state(1)
    await handle_direct_score(msg, db, st)

    assert await st.get_state() is None
    msg.answer.assert_not_called()


async def test_direct_score_one_active_match_starts_input(db):
    """Один активный матч → FSM стартует и счёт обрабатывается."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    await _accepted_match(db, p1, p2)

    msg, st = _message(1, "11:7"), _state(1)
    await handle_direct_score(msg, db, st)

    assert await st.get_state() == MatchResultStates.entering_set_score.state
    data = await st.get_data()
    assert data["sets_data"] == [{"reporter": 11, "opponent": 7}]
    msg.answer.assert_called()  # показал прогресс


async def test_direct_score_multiple_active_matches_prompts(db):
    """РЕГРЕССИЯ: 2+ активных матча → подсказка, без краша и без FSM."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _accepted_match(db, p1, p2)
    await _accepted_match(db, p1, p3)

    msg, st = _message(1, "11:7"), _state(1)
    await handle_direct_score(msg, db, st)

    assert await st.get_state() is None
    msg.answer.assert_called_once()
    assert "несколько активных" in msg.answer.call_args.args[0]


# ── process_set_score (валидация ввода) ─────────────────────────────────────────

async def _prep_input_state(match_id: int) -> FSMContext:
    st = _state(1)
    await st.set_state(MatchResultStates.entering_set_score)
    await st.update_data(sets_data=[], match_id=match_id, reporter_player_id=1)
    return st


async def test_process_set_score_valid_single(db):
    st = await _prep_input_state(1)
    msg = _message(1, "11:7")
    await process_set_score(msg, st)
    data = await st.get_data()
    assert data["sets_data"] == [{"reporter": 11, "opponent": 7}]


async def test_process_set_score_invalid_is_rejected(db):
    """Некорректный счёт 15:7 → ошибка, партия не добавлена."""
    st = await _prep_input_state(1)
    msg = _message(1, "15:7")
    await process_set_score(msg, st)
    data = await st.get_data()
    assert data["sets_data"] == []
    msg.answer.assert_called()


async def test_process_set_score_batch(db):
    """Пакетный ввод '11:7 9:11' → две партии."""
    st = await _prep_input_state(1)
    msg = _message(1, "11:7 9:11")
    await process_set_score(msg, st)
    data = await st.get_data()
    assert data["sets_data"] == [
        {"reporter": 11, "opponent": 7},
        {"reporter": 9, "opponent": 11},
    ]


async def test_process_set_score_dash_separator(db):
    """Дефис как разделитель: '11-7' = '11:7'."""
    st = await _prep_input_state(1)
    msg = _message(1, "11-7")
    await process_set_score(msg, st)
    data = await st.get_data()
    assert data["sets_data"] == [{"reporter": 11, "opponent": 7}]


# ── send_challenge (создание матча) ─────────────────────────────────────────────

async def test_send_challenge_creates_active_match(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    cb, bot = _callback(1, f"challenge_{p2.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    r = await db.execute(
        Match.__table__.select().where(Match.status == MatchStatus.accepted)
    )
    rows = r.fetchall()
    assert len(rows) == 1
    bot.send_message.assert_called()      # соперник уведомлён
    cb.message.edit_text.assert_called()  # инициатор видит «матч начат»


async def test_send_challenge_blocks_duplicate(db):
    """Нельзя вызвать игрока, с которым уже есть активный матч."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    await _accepted_match(db, p1, p2)
    await db.commit()

    cb, bot = _callback(1, f"challenge_{p2.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    cb.answer.assert_called()  # показал alert про активный матч
    assert any("активный матч" in str(c.args) for c in cb.answer.call_args_list)


# ── do_cancel_match (отмена + уведомление + ачивка) ─────────────────────────────

async def test_cancel_match_declines_and_notifies(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2)
    await db.commit()

    cb, bot = _callback(1, f"cancel_yes_{m.id}"), AsyncMock()
    await do_cancel_match(cb, db, bot)

    assert m.status == MatchStatus.declined
    bot.send_message.assert_called()           # соперник уведомлён
    cb.message.edit_text.assert_called()
    # «Дух Анкориджа» — обоим участникам
    assert "anchorage_spirit" in get_achievements(p1)
    assert "anchorage_spirit" in get_achievements(p2)
