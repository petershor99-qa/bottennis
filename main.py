from dotenv import load_dotenv

# .env загружаем ДО импорта модулей бота: database.py, start.py и admin.py
# читают DATABASE_URL / INVITE_CODE / ADMIN_ID на уровне модуля. Если load_dotenv()
# вызвать после импортов (как было), .env-only деплой получает дефолты:
# локальную БД, открытую регистрацию и выключенную админку.
load_dotenv()

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import ExceptionTypeFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from bot.db.database import async_session, init_db
from bot.handlers.admin import router as admin_router
from bot.handlers.challenge import router as challenge_router
from bot.handlers.history import router as history_router
from bot.handlers.leaderboard import router as leaderboard_router
from bot.handlers.match_result import router as match_result_router
from bot.handlers.profile import router as profile_router
from bot.handlers.start import router as start_router
from bot.middleware import DatabaseMiddleware
from bot.scheduler import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


async def on_telegram_bad_request(event: ErrorEvent) -> bool:
    """Обрабатывает ошибки Telegram, чтобы НИКОГДА не вешать спиннер на кнопке.

    Любой сбой рендера (двойной тап = "message is not modified", сломанный HTML
    и т.п.) без ответа на callback оставляет крутящийся спиннер на ~15 секунд,
    после чего Telegram отдаёт "query is too old". Поэтому на любой ошибке
    снимаем спиннер и логируем причину — бот остаётся отзывчивым."""
    msg = str(event.exception)
    callback = event.update.callback_query if event.update else None

    # Двойной тап по той же кнопке — штатная ситуация, гасим тихо.
    if "message is not modified" in msg:
        if callback:
            try:
                await callback.answer()
            except Exception:
                pass
        return True

    # Прочие ошибки (например сломанный HTML) — логируем с трейсом и снимаем спиннер.
    logging.error("Ошибка обработки апдейта: %s", msg, exc_info=event.exception)
    if callback:
        try:
            await callback.answer("Упс, что-то пошло не так. Попробуй ещё раз 🙏")
        except Exception:
            pass
    return True


async def on_telegram_rate_limit(event: ErrorEvent) -> bool:
    """RetryAfter (429) — Telegram rate-limit на редактирование сообщений.
    Снимаем спиннер, пользователь может нажать кнопку ещё раз."""
    callback = event.update.callback_query if event.update else None
    if callback:
        try:
            await callback.answer()
        except Exception:
            pass
    return True


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан в .env файле")

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(DatabaseMiddleware(async_session))

    dp.error.register(on_telegram_bad_request, ExceptionTypeFilter(TelegramBadRequest))
    dp.error.register(on_telegram_rate_limit, ExceptionTypeFilter(TelegramRetryAfter))

    # Бот работает только в личных чатах — в группах молчит
    dp.message.filter(F.chat.type == "private")
    dp.callback_query.filter(F.message.chat.type == "private")

    dp.include_router(admin_router)
    dp.include_router(start_router)
    dp.include_router(leaderboard_router)
    dp.include_router(profile_router)
    dp.include_router(history_router)
    dp.include_router(challenge_router)
    dp.include_router(match_result_router)

    await init_db()

    scheduler = setup_scheduler(bot)
    scheduler.start()
    logging.info("Планировщик запущен.")

    logging.info("Бот запущен. Нажми Ctrl+C для остановки.")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
