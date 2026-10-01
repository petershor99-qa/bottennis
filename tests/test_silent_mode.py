"""Тихий режим дайджестов (v2.145.0): экран «Рассылки» и пропуск отключённых сводок."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

import bot.db.database as database
import bot.handlers.admin as admin_module
import bot.scheduler as sched
from bot.db.models import Player
from bot.handlers.admin import send_whatsnew
from bot.handlers.notifications import show_notifications, toggle_notification
from bot.handlers.start import cmd_help
from bot.keyboards.inline import notifications_kb
from bot.services.digests import (
    DIGEST_KINDS,
    is_digest_muted,
    muted_digests,
    toggle_digest,
)
from tests.conftest import _callback, _completed, _player


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


# ── Сервис ─────────────────────────────────────────────────────────────────────

def test_new_player_receives_everything():
    p = _player(1, "A")
    assert muted_digests(p) == set()
    assert not any(is_digest_muted(p, k) for k, _ in DIGEST_KINDS)


def test_toggle_mutes_then_unmutes():
    p = _player(1, "A")
    assert toggle_digest(p, "day") is True
    assert is_digest_muted(p, "day")
    assert not is_digest_muted(p, "week")
    assert toggle_digest(p, "day") is False
    assert not is_digest_muted(p, "day")


def test_toggle_keeps_stable_order_and_other_kinds():
    p = _player(1, "A")
    toggle_digest(p, "month")
    toggle_digest(p, "day")
    assert p.muted_digests == "day,month"      # порядок как в DIGEST_KINDS, не по времени
    toggle_digest(p, "day")
    assert p.muted_digests == "month"


def test_toggle_unknown_kind_changes_nothing():
    p = _player(1, "A")
    assert toggle_digest(p, "nonsense") is None
    assert muted_digests(p) == set()


def test_unknown_stored_keys_are_ignored():
    p = _player(1, "A")
    p.muted_digests = "day,gone,,week"
    assert muted_digests(p) == {"day", "week"}


# ── Клавиатура и экран ─────────────────────────────────────────────────────────

def test_keyboard_marks_muted_and_active():
    kb = notifications_kb({"week"})
    by_cb = {b.callback_data: b.text for b in _buttons(kb)}
    assert by_cb["notif_toggle_week"].endswith("🔕")
    assert by_cb["notif_toggle_day"].endswith("✅")
    assert len([c for c in by_cb if c.startswith("notif_toggle_")]) == len(DIGEST_KINDS)
    assert _buttons(kb)[-1].callback_data == "back_to_menu"


async def test_screen_requires_registration(db):
    cb = _callback(99999, "menu_notifications")
    await show_notifications(cb, db)
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    cb.message.edit_text.assert_not_awaited()


async def test_screen_opens_for_registered_player(db):
    db.add(_player(1, "A"))
    await db.commit()
    cb = _callback(1, "menu_notifications")
    await show_notifications(cb, db)
    shown = cb.message.edit_text.await_args
    assert "Рассылки" in shown.args[0]
    assert "отключить нельзя" in shown.args[0]
    assert all(b.text.endswith("✅") for b in _buttons(shown.kwargs["reply_markup"])[:-1])


async def test_toggle_saves_choice_and_redraws(db):
    db.add(_player(1, "A"))
    await db.commit()
    cb = _callback(1, "notif_toggle_month")
    await toggle_notification(cb, db)

    fresh = (await db.execute(select(Player).where(Player.telegram_id == 1))).scalar_one()
    assert fresh.muted_digests == "month"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert {b.callback_data: b.text for b in _buttons(kb)}["notif_toggle_month"].endswith("🔕")

    # повторный тап включает обратно
    await toggle_notification(_callback(1, "notif_toggle_month"), db)
    fresh = (await db.execute(select(Player).where(Player.telegram_id == 1))).scalar_one()
    assert fresh.muted_digests == ""


async def test_toggle_unknown_kind_alerts(db):
    db.add(_player(1, "A"))
    await db.commit()
    cb = _callback(1, "notif_toggle_nonsense")
    await toggle_notification(cb, db)
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    cb.message.edit_text.assert_not_awaited()


async def test_help_has_notifications_button():
    msg = AsyncMock()
    await cmd_help(msg)
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert "menu_notifications" in [b.callback_data for b in _buttons(kb)]


# ── Миграция ───────────────────────────────────────────────────────────────────

async def test_migration_adds_column_to_old_players_table(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE players (id INTEGER PRIMARY KEY, telegram_id INTEGER)"))
        await conn.execute(text("INSERT INTO players (id, telegram_id) VALUES (1, 100)"))
    monkeypatch.setattr(database, "engine", engine)

    await database._migrate_db()
    await database._migrate_db()   # повторный запуск не ломает

    async with engine.begin() as conn:
        cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(players)"))).all()}
        value = (await conn.execute(text("SELECT muted_digests FROM players"))).scalar_one()
    assert "muted_digests" in cols
    assert value == ""                  # существующий игрок получает все сводки
    await engine.dispose()


# ── Рассылки пропускают отключивших ────────────────────────────────────────────

async def _recipients(bot) -> set[int]:
    return {c.args[0] for c in bot.send_message.await_args_list}


async def _seed_two(factory, *, muted_for_bob: str, when):
    async with factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        p2.muted_digests = muted_for_bob
        s.add_all([p1, p2])
        await s.flush()
        s.add(_completed(p1, p2, p1.id, 10.0, when))
        await s.commit()


async def test_daily_skips_muted_player(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    await _seed_two(db_factory, muted_for_bob="day", when=datetime.now(timezone.utc).replace(tzinfo=None))
    bot = AsyncMock()
    await sched.send_daily_summary(bot)
    assert await _recipients(bot) == {1}


async def test_daily_still_sent_when_other_digest_muted(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    await _seed_two(db_factory, muted_for_bob="week,month", when=datetime.now(timezone.utc).replace(tzinfo=None))
    bot = AsyncMock()
    await sched.send_daily_summary(bot)
    assert await _recipients(bot) == {1, 2}


async def test_weekly_skips_muted_player(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await _seed_two(db_factory, muted_for_bob="week", when=now - timedelta(hours=2))
    bot = AsyncMock()
    await sched.send_weekly_digest(bot)
    assert await _recipients(bot) == {1}


async def test_monthly_skips_muted_player(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + sched.MSK_OFFSET
    month_end = msk_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start = (month_end - timedelta(days=1)).replace(day=1)
    when = (month_start + timedelta(days=5)) - sched.MSK_OFFSET
    await _seed_two(db_factory, muted_for_bob="month", when=when)
    bot = AsyncMock()
    await sched.send_monthly_summary(bot)
    assert await _recipients(bot) == {1}


async def test_quarterly_skips_muted_player(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + sched.MSK_OFFSET
    q_start, _ = sched._quarter_bounds_msk(msk_now)
    when = (q_start + timedelta(days=5)) - sched.MSK_OFFSET
    await _seed_two(db_factory, muted_for_bob="quarter", when=when)
    bot = AsyncMock()
    await sched.send_quarterly_summary(bot)
    assert await _recipients(bot) == {1}


async def test_yearly_skips_muted_player(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await _seed_two(db_factory, muted_for_bob="year", when=now - timedelta(hours=1))
    bot = AsyncMock()
    await sched.send_yearly_summary(bot)
    assert await _recipients(bot) == {1}


async def test_whatsnew_broadcast_ignores_muted_digests(db, monkeypatch):
    """«Что нового» — не автосводка: отключившие ВСЕ сводки её всё равно получают."""
    monkeypatch.setattr(admin_module, "ADMIN_ID", 1)
    p = _player(10, "Алиса")
    p.muted_digests = "day,week,month,quarter,year"
    db.add(p)
    await db.commit()
    cb, bot = _callback(1, "whatsnew_send"), AsyncMock()
    await send_whatsnew(cb, bot, db)
    assert {c.args[0] for c in bot.send_message.await_args_list} == {10}
