"""
Общие фикстуры для всех тестов — свежая in-memory SQLite на каждый тест.

Раньше engine/sessionmaker дублировались инлайн в каждом тест-файле (и внутри
файлов — на каждый тест, использующий scheduler.py). Вынесено сюда, чтобы:
- убрать копипасту create_async_engine/create_all/sessionmaker;
- гарантировать engine.dispose() через teardown фикстуры, а не ручной вызов
  в конце теста (раньше при падении assert'а до этой строки engine не
  освобождался).
"""
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base


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
