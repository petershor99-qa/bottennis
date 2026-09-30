"""Тесты рассылки «Что нового» (/whatsnew, v2.140.0)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot.handlers.admin as admin_module
from bot.handlers.admin import WHATS_NEW_TEXT, cmd_whatsnew, send_whatsnew
from tests.conftest import _callback, _player


def _message(user_id: int) -> AsyncMock:
    m = AsyncMock()
    m.from_user = SimpleNamespace(id=user_id)
    m.answer = AsyncMock()
    return m


def test_text_mentions_december_vote_with_the_agreed_phrase():
    assert "21 декабря откроется голосование за неформальные звания года" in WHATS_NEW_TEXT
    assert "(если мы не развалимся. А мы не развалимся)" in WHATS_NEW_TEXT


def test_text_fits_one_telegram_message():
    assert len(WHATS_NEW_TEXT) < 4096


async def test_preview_not_admin_does_nothing(monkeypatch):
    monkeypatch.setattr(admin_module, "ADMIN_ID", 1)
    msg = _message(2)
    await cmd_whatsnew(msg)
    msg.answer.assert_not_awaited()


async def test_preview_shows_text_and_send_button(monkeypatch):
    monkeypatch.setattr(admin_module, "ADMIN_ID", 1)
    msg = _message(1)
    await cmd_whatsnew(msg)

    text = msg.answer.await_args.args[0]
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert WHATS_NEW_TEXT in text
    assert kb.inline_keyboard[0][0].callback_data == "whatsnew_send"


async def test_send_by_non_admin_sends_nothing(db, monkeypatch):
    monkeypatch.setattr(admin_module, "ADMIN_ID", 1)
    db.add(_player(10, "Алиса"))
    await db.commit()
    cb, bot = _callback(2, "whatsnew_send"), AsyncMock()
    await send_whatsnew(cb, bot, db)
    bot.send_message.assert_not_awaited()


async def test_send_by_admin_reaches_everyone_and_reports_count(db, monkeypatch):
    monkeypatch.setattr(admin_module, "ADMIN_ID", 1)
    db.add_all([_player(10, "Алиса"), _player(11, "Боб")])
    await db.commit()
    cb, bot = _callback(1, "whatsnew_send"), AsyncMock()
    await send_whatsnew(cb, bot, db)

    sent_to = {c.args[0] for c in bot.send_message.await_args_list}
    assert sent_to == {10, 11}
    assert all(c.args[1] == WHATS_NEW_TEXT for c in bot.send_message.await_args_list)
    cb.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)  # кнопка убрана
    assert "2 из 2" in cb.message.answer.await_args.args[0]


async def test_send_counts_undelivered_players(db, monkeypatch):
    """Игрок, заблокировавший бота, не роняет рассылку — просто не считается доставленным."""
    from aiogram.exceptions import TelegramForbiddenError

    monkeypatch.setattr(admin_module, "ADMIN_ID", 1)
    db.add_all([_player(10, "Алиса"), _player(11, "Боб")])
    await db.commit()
    cb, bot = _callback(1, "whatsnew_send"), AsyncMock()

    async def fake_send(chat_id, text, **kw):
        if chat_id == 11:
            raise TelegramForbiddenError(method=AsyncMock(), message="blocked")

    bot.send_message.side_effect = fake_send
    await send_whatsnew(cb, bot, db)
    assert "1 из 2" in cb.message.answer.await_args.args[0]
