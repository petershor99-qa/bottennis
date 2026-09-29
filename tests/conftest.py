"""
Общие фикстуры и хелперы для всех тестов.

Раньше engine/sessionmaker дублировались инлайн в каждом тест-файле (и внутри
файлов — на каждый тест, использующий scheduler.py), а _player/_state/_callback
были продублированы построчно один-в-один в 4-5 файлах. Вынесено сюда, чтобы:
- убрать копипасту create_async_engine/create_all/sessionmaker;
- гарантировать engine.dispose() через teardown фикстуры, а не ручной вызов
  в конце теста (раньше при падении assert'а до этой строки engine не
  освобождался);
- убрать копипасту тестовых хелперов — импортируются как обычные функции
  (`from tests.conftest import _player, ...`), НЕ pytest-фикстуры: вызываются
  по нескольку раз за тест с разными аргументами («дай игрока с ЭТИМИ
  параметрами»), это фабричный вызов, а не инъекция значения — фикстура
  добавила бы каждому тесту обязательный параметр в сигнатуре ради нуля
  выгоды.
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


@pytest_asyncio.fixture
async def db():
    """Одна открытая AsyncSession — для тестов, работающих с БД напрямую
    (вызывают хендлеры/сервисы, передавая сессию явным аргументом)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def db_factory():
    """Фабрика сессий (не готовая сессия) — для тестов, которые подменяют
    bot.scheduler.async_session через monkeypatch: scheduler.py открывает
    свои сессии сам, поэтому ему нужна фабрика, а не db()."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
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


def _completed(
    challenger: Player, challenged: Player, winner_id: int, rc: float, when: datetime,
) -> Match:
    """Завершённый матч (для db.add(...) напрямую в тестах). rc — rating_change,
    явный аргумент (не дефолт) — сигнатура унифицирована между test_handlers.py
    (было `rc` явным с самого начала) и test_boss_fight.py (было жёстко 5.0
    внутри) — заход 2 рефактора тестовых хелперов, см. CLAUDE.md."""
    return Match(
        challenger_id=challenger.id, challenged_id=challenged.id,
        status=MatchStatus.completed, winner_id=winner_id,
        sets_data=[{"w": 11, "l": 5}], rating_change=rc, completed_at=when,
    )


async def show_all_club_records(cb, session) -> None:
    """Тест-хелпер: экран «Рекорды клуба» разбит на категории (v2.136.0), а
    старые тесты проверяют «рекорд X есть в тексте». Хелпер собирает текст всех
    категорий и кладёт его в cb.message.edit_text одним вызовом — проверки
    остаются валидными без правки каждой. Пустой клуб идёт через настоящий
    хендлер (там свой текст «Матчей ещё не было»)."""
    from bot.handlers.leaderboard import (
        RECORD_CATEGORIES,
        _collect_club_records,
        _render_records_category,
        show_club_records,
    )

    groups = await _collect_club_records(session)
    if groups is None:
        await show_club_records(cb, session)
        return
    text = "\n".join(
        _render_records_category(title, groups[key])
        for key, title in RECORD_CATEGORIES if groups[key]
    )
    await cb.message.edit_text(text)
