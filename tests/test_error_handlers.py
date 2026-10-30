"""Общий error-handler main.py (v2.215.0): не-Telegram исключения не должны
оставлять вечный спинер на кнопке (~15 с до «query is too old»)."""
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Update

import main as main_module


def _event(exc: Exception, with_callback: bool = True) -> main_module.ErrorEvent:
    """ErrorEvent — pydantic-модель с обязательной валидацией Update; для теста
    хендлера достаточно model_construct: он читает только .exception и
    .update.callback_query."""
    cb = AsyncMock() if with_callback else None
    update = Update.model_construct(update_id=1, callback_query=cb)
    return main_module.ErrorEvent.model_construct(update=update, exception=exc)


async def test_unexpected_error_answers_callback_and_returns_true():
    event = _event(ValueError("boom"))
    assert await main_module.on_unexpected_error(event) is True
    event.update.callback_query.answer.assert_awaited_once()


async def test_unexpected_error_without_callback_still_logged():
    event = _event(RuntimeError("x"), with_callback=False)
    assert await main_module.on_unexpected_error(event) is True  # не упало и без callback


def test_filter_skips_handled_telegram_errors():
    """Конкретные случаи BadRequest/RetryAfter разбирают свои хендлеры —
    catch-all их не перехватывает (иначе «message is not modified» получал бы
    лишний алерт «Упс»)."""
    assert not main_module._is_unexpected_error(_event(TelegramBadRequest(method=None, message="message is not modified")))
    assert not main_module._is_unexpected_error(_event(TelegramRetryAfter(method=None, retry_after=3, message="Too many requests")))
    assert main_module._is_unexpected_error(_event(ValueError("boom")))
