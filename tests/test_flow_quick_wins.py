"""
Быстрые победы флоу «позвал → сыграл → записал» (v2.166.0, по разбору usage):
тихое уведомление о вызове и постоянная нижняя клавиатура. Ввод счёта одним
сообщением — в tests/test_handlers.py (test_process_set_score_batch*).
"""
from unittest.mock import AsyncMock

from bot.handlers.challenge import send_challenge
from bot.keyboards.inline import main_reply_kb
from tests.conftest import _callback, _player


async def test_challenge_notification_is_silent_but_sent(db):
    """Вызов делают после игры — соперник рядом, через полминуты придёт результат.
    Сообщение остаётся (напоминает записать), но без звука."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    cb, bot = _callback(1, f"challenge_{p2.id}"), AsyncMock()
    await send_challenge(cb, db, bot)
    to_opponent = [c for c in bot.send_message.call_args_list if c.args and c.args[0] == p2.telegram_id]
    assert to_opponent, "сопернику ничего не отправлено"
    assert to_opponent[0].kwargs.get("disable_notification") is True


def test_reply_keyboard_is_persistent():
    """Клавиатуру не свернуть — меню больше не «теряется» (38% сессий начинались с /start)."""
    kb = main_reply_kb()
    assert kb.is_persistent is True
    assert kb.resize_keyboard is True
