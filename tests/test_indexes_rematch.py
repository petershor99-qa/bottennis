"""Индексы на matches и реванш в уведомлении второго участника (v2.143.0)."""
from unittest.mock import AsyncMock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import bot.db.database as database
from bot.db.models import Base
from bot.handlers.match_result import confirm_result
from bot.keyboards.inline import main_menu_kb
from tests.conftest import _callback, _player
from tests.test_boss_fight import _accepted_match, _confirming_state

EXPECTED_INDEXES = {
    "ix_matches_challenger_id",
    "ix_matches_challenged_id",
    "ix_matches_status_completed_at",
}


async def _index_names(conn) -> set[str]:
    r = await conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'index'"))
    return {row[0] for row in r.all()}


async def test_create_all_creates_match_indexes():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        assert EXPECTED_INDEXES <= await _index_names(conn)
    await engine.dispose()


async def test_migration_adds_indexes_to_existing_db_and_is_idempotent(monkeypatch):
    """Старая БД (таблицы есть, индексов нет) получает индексы при старте; повторный
    запуск миграции ничего не ломает."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for name in EXPECTED_INDEXES:
            await conn.execute(text(f"DROP INDEX {name}"))
        assert not EXPECTED_INDEXES & await _index_names(conn)

    monkeypatch.setattr(database, "engine", engine)
    await database._migrate_db()
    await database._migrate_db()

    async with engine.begin() as conn:
        assert EXPECTED_INDEXES <= await _index_names(conn)
    await engine.dispose()


def _rematch_buttons(markup) -> list[str]:
    return [
        b.callback_data for row in markup.inline_keyboard for b in row
        if b.callback_data and b.callback_data.startswith("rematch_")
    ]


def test_main_menu_has_no_rematch_by_default():
    assert _rematch_buttons(main_menu_kb()) == []


def test_main_menu_rematch_button_goes_first_after_card():
    kb = main_menu_kb(share_match_id=7, rematch_opponent_id=5)
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert flat[0] == "share_card_7"
    assert flat[1] == "rematch_5"
    assert "menu_play" in flat   # остальное меню на месте


def _notification_markup(bot, chat_id: int):
    calls = [c for c in bot.send_message.await_args_list if c.args[0] == chat_id]
    assert calls, f"уведомление в чат {chat_id} не ушло"
    return calls[0].kwargs["reply_markup"]


async def _confirm(db, reporter, other, sets, is_draw=False, boss=False):
    m = await _accepted_match(db, reporter, other, is_boss_fight=boss)
    await db.commit()
    st = await _confirming_state(m.id, reporter.id, sets, is_draw=is_draw)
    cb, bot = _callback(reporter.telegram_id, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)
    return bot


async def test_loser_notification_offers_rematch_with_winner(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    bot = await _confirm(db, p1, p2, [{"reporter": 11, "opponent": 3}, {"reporter": 11, "opponent": 5}])
    assert _rematch_buttons(_notification_markup(bot, 2)) == [f"rematch_{p1.id}"]


async def test_winner_notification_after_inverted_report_has_card_and_rematch(db):
    """Счёт внёс проигравший: победитель получает карточку победы И реванш."""
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    bot = await _confirm(db, p1, p2, [{"reporter": 3, "opponent": 11}, {"reporter": 5, "opponent": 11}])
    markup = _notification_markup(bot, 2)
    assert _rematch_buttons(markup) == [f"rematch_{p1.id}"]
    assert any(
        b.callback_data and b.callback_data.startswith("share_card_")
        for row in markup.inline_keyboard for b in row
    )


async def test_draw_notification_offers_rematch(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    bot = await _confirm(
        db, p1, p2,
        [{"reporter": 11, "opponent": 9}, {"reporter": 8, "opponent": 11}],
        is_draw=True,
    )
    assert _rematch_buttons(_notification_markup(bot, 2)) == [f"rematch_{p1.id}"]


async def test_boss_fight_notification_has_no_rematch(db):
    champion = _player(1, "Champion")
    champion.is_champion = True
    challenger = _player(2, "Challenger")
    db.add_all([champion, challenger])
    await db.flush()
    bot = await _confirm(
        db, challenger, champion,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
        boss=True,
    )
    # у боссфайта свои сообщения (трон и т.п.) — главное, что реванша нет ни в одном
    calls = [c for c in bot.send_message.await_args_list if c.args[0] == 1]
    assert calls
    for c in calls:
        markup = c.kwargs.get("reply_markup")
        assert markup is None or _rematch_buttons(markup) == []

