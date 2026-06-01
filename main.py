import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from bot.db.database import init_db, async_session
from bot.middleware import DatabaseMiddleware
from bot.handlers.admin import router as admin_router
from bot.handlers.start import router as start_router
from bot.handlers.leaderboard import router as leaderboard_router
from bot.handlers.profile import router as profile_router
from bot.handlers.history import router as history_router
from bot.handlers.challenge import router as challenge_router
from bot.handlers.match_result import router as match_result_router
from bot.scheduler import setup_scheduler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан в .env файле")

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(DatabaseMiddleware(async_session))

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
