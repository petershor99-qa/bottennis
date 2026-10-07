"""
Тесты счётчика использования экранов (bot/services/usage.py, bot/middleware.py) —
этап 1 дорожной карты из CLAUDE.md.
"""
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import bot.middleware as middleware_module
from bot.db.models import UsageEvent
from bot.middleware import UsageMiddleware
from bot.services.usage import action_label, normalize_action

# ── normalize_action ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("player_profile_42", "player_profile"),
    ("player_profile_7", "player_profile"),
    ("h2h_5_0", "h2h"),
    ("cancel_match_42", "cancel_match"),
    ("menu_stats", "menu_stats"),          # нет числового сегмента — не меняется
    ("player_chart_-3", "player_chart"),   # отрицательный id — тоже число
    ("/start ABC123", "/start"),
    ("/help", "/help"),
])
def test_normalize_action_examples(raw, expected):
    assert normalize_action(raw) == expected


def test_normalize_action_truncates_to_64_chars():
    assert len(normalize_action("x" * 100)) == 64


# ── action_label ──────────────────────────────────────────────────────────────

def test_action_label_known_action_translated():
    assert action_label("player_profile") == "Профиль игрока"


def test_action_label_unknown_action_falls_back_to_raw():
    """Новый экран, ещё не вписанный в ACTION_LABELS, — не теряется молча."""
    assert action_label("brand_new_screen") == "brand_new_screen"


# ── UsageMiddleware ───────────────────────────────────────────────────────────

def test_middleware_invalid_kind_raises():
    with pytest.raises(ValueError):
        UsageMiddleware("bogus")


async def test_middleware_records_callback(db_factory, monkeypatch):
    monkeypatch.setattr(middleware_module, "async_session", db_factory)
    mw = UsageMiddleware("callback")
    cb = SimpleNamespace(data="player_profile_42", from_user=SimpleNamespace(id=1))

    async def handler(event, data):
        return "ok"

    result = await mw(handler, cb, {})
    await mw.drain()
    assert result == "ok"

    async with db_factory() as s:
        events = (await s.execute(select(UsageEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].user_id == 1
    assert events[0].action == "player_profile"


async def test_middleware_records_command(db_factory, monkeypatch):
    monkeypatch.setattr(middleware_module, "async_session", db_factory)
    mw = UsageMiddleware("command")
    msg = SimpleNamespace(text="/start ABC123", from_user=SimpleNamespace(id=2))

    async def handler(event, data):
        return None

    await mw(handler, msg, {})
    await mw.drain()

    async with db_factory() as s:
        events = (await s.execute(select(UsageEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].action == "/start"


async def test_middleware_command_kind_ignores_plain_text(db_factory, monkeypatch):
    """Прямой ввод счёта ("11:7 9:11") — не команда, молча не считается."""
    monkeypatch.setattr(middleware_module, "async_session", db_factory)
    mw = UsageMiddleware("command")
    msg = SimpleNamespace(text="11:7 9:11", from_user=SimpleNamespace(id=2))

    async def handler(event, data):
        return None

    await mw(handler, msg, {})
    await mw.drain()

    async with db_factory() as s:
        events = (await s.execute(select(UsageEvent))).scalars().all()
    assert events == []


async def test_middleware_skips_event_without_from_user(db_factory, monkeypatch):
    monkeypatch.setattr(middleware_module, "async_session", db_factory)
    mw = UsageMiddleware("callback")
    cb = SimpleNamespace(data="menu_stats", from_user=None)

    async def handler(event, data):
        return "ok"

    result = await mw(handler, cb, {})
    await mw.drain()
    assert result == "ok"

    async with db_factory() as s:
        events = (await s.execute(select(UsageEvent))).scalars().all()
    assert events == []


async def test_middleware_write_failure_does_not_break_handler(monkeypatch, caplog):
    """РЕГРЕССИЯ: если запись события падает (например, БД временно
    недоступна), хендлер всё равно должен нормально отработать."""
    def broken_session_factory():
        raise RuntimeError("боевая база недоступна")

    monkeypatch.setattr(middleware_module, "async_session", broken_session_factory)
    mw = UsageMiddleware("callback")
    cb = SimpleNamespace(data="menu_stats", from_user=SimpleNamespace(id=1))

    async def handler(event, data):
        return "handled"

    monkeypatch.setattr(middleware_module, "_WRITE_RETRY_DELAY", 0)
    with caplog.at_level(logging.WARNING):
        result = await mw(handler, cb, {})
        await mw.drain()

    assert result == "handled"
    assert any("использования" in r.message for r in caplog.records)


async def test_middleware_propagates_handler_exception(db_factory, monkeypatch):
    """Ошибка счётчика не должна маскировать реальную ошибку хендлера —
    finally всё равно пишет событие, но исключение хендлера летит наверх."""
    monkeypatch.setattr(middleware_module, "async_session", db_factory)
    mw = UsageMiddleware("callback")
    cb = SimpleNamespace(data="menu_stats", from_user=SimpleNamespace(id=1))

    async def handler(event, data):
        raise RuntimeError("хендлер упал")

    with pytest.raises(RuntimeError, match="хендлер упал"):
        await mw(handler, cb, {})
    await mw.drain()

    async with db_factory() as s:
        events = (await s.execute(select(UsageEvent))).scalars().all()
    assert len(events) == 1  # событие всё равно записалось


async def test_middleware_retries_transient_write_failure(db_factory, monkeypatch):
    """Событие не теряется, если база занята при первой попытке записи."""
    calls = {"n": 0}

    def flaky_factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return db_factory()

    monkeypatch.setattr(middleware_module, "async_session", flaky_factory)
    monkeypatch.setattr(middleware_module, "_WRITE_RETRY_DELAY", 0)
    mw = UsageMiddleware("callback")
    cb = SimpleNamespace(data="menu_stats", from_user=SimpleNamespace(id=1))

    async def handler(event, data):
        return None

    await mw(handler, cb, {})
    await mw.drain()

    async with db_factory() as s:
        events = (await s.execute(select(UsageEvent))).scalars().all()
    assert len(events) == 1
    assert calls["n"] == 2


async def test_middleware_does_not_stall_handler_that_writes_in_same_db(tmp_path, monkeypatch):
    """РЕГРЕССИЯ v2.157.2: цепочка DatabaseMiddleware -> UsageMiddleware ->
    хендлер, пишущий в БД, на файловой SQLite. Раньше запись события в
    `finally` ждала блокировку писателя, которую держала транзакция самого
    хендлера (коммит — только в DatabaseMiddleware, снаружи), и упиралась в
    busy-timeout ~5 с: кнопка отвечала с задержкой, событие терялось."""
    import time

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from bot.db.models import Base, Player
    from bot.middleware import DatabaseMiddleware

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(middleware_module, "async_session", factory)

    db_mw = DatabaseMiddleware(factory)
    usage_mw = UsageMiddleware("callback")
    cb = SimpleNamespace(data="yv_pick_gentleman_5", from_user=SimpleNamespace(id=1))

    async def handler(event, data):
        data["session"].add(Player(
            telegram_id=1, display_name="A", rating=1000.0,
            achievements="[]", backfill_version=0,
        ))
        await data["session"].flush()  # транзакция хендлера держит блокировку записи
        return "ok"

    async def inner(event, data):
        return await usage_mw(handler, event, data)

    started = time.monotonic()
    result = await db_mw(inner, cb, {})
    elapsed = time.monotonic() - started
    await usage_mw.drain()

    assert result == "ok"
    assert elapsed < 2.0
    async with factory() as s:
        events = (await s.execute(select(UsageEvent))).scalars().all()
        players = (await s.execute(select(Player))).scalars().all()
    assert len(events) == 1
    assert len(players) == 1
    await engine.dispose()
