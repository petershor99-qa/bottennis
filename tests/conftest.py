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
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base, Player


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
