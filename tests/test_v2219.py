"""
v2.219.0: постоянный возврат reply-клавиатуры — safe_send прикрепляет её
сообщениям без своей клавиатуры. Постоянную клавиатуру можно свернуть на
любом клиенте, а повторная отправка идентичной клавиатуры — no-op для
Telegram: свернувшийся игрок оставался без кнопок до следующего bump версии.
Теперь каждая пасхалка/уведомление/рассылка без inline-клавиатуры возвращает
её; подпись первой кнопки меняется по часу — клиент перерисовывает.
Запуск: pytest tests/test_v2219.py
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import ReplyKeyboardMarkup

from bot.keyboards.inline import REPLY_KB_APP
from bot.phrases import CHALLENGE_BUTTON_LABELS
from bot.utils import safe_send
from tests.test_v2203 import ADMIN, app_on  # noqa: F401 — фикстура окружения приложения


async def test_safe_send_attaches_reply_keyboard():
    bot = SimpleNamespace(send_message=AsyncMock())
    assert await safe_send(bot, 42, "текст") is True
    kb = bot.send_message.call_args.kwargs["reply_markup"]
    assert isinstance(kb, ReplyKeyboardMarkup)
    assert kb.is_persistent
    assert len(kb.keyboard) == 1 and len(kb.keyboard[0]) == 3


async def test_safe_send_first_button_from_challenge_pool():
    bot = SimpleNamespace(send_message=AsyncMock())
    await safe_send(bot, 42, "текст")
    first = bot.send_message.call_args.kwargs["reply_markup"].keyboard[0][0]
    assert first.text in CHALLENGE_BUTTON_LABELS


async def test_safe_send_keeps_passed_inline_markup():
    """Сообщениям со своей inline-клавиатурой (busy_with_match_kb, кнопки
    сводок) reply-клавиатура не перекрывает — у одного сообщения клавиатуры
    двух типов быть не может."""
    bot = SimpleNamespace(send_message=AsyncMock())
    sentinel = ReplyKeyboardMarkup(keyboard=[[]])
    await safe_send(bot, 42, "текст", reply_markup=sentinel)
    assert bot.send_message.call_args.kwargs["reply_markup"] is sentinel


async def test_safe_send_third_button_web_app_when_allowed(app_on):  # noqa: F811 — параметр = импортированная фикстура
    bot = SimpleNamespace(send_message=AsyncMock())
    await safe_send(bot, ADMIN, "текст")
    third = bot.send_message.call_args.kwargs["reply_markup"].keyboard[0][2]
    assert third.text == REPLY_KB_APP and third.web_app.url == "https://club.example/"
