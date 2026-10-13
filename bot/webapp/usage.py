"""
Счётчик нажатий в Mini App (v2.166.0). Страница после каждого показанного
экрана шлёт `POST /api/track {"screen": "me_matches"}`, сервер пишет событие в
ту же таблицу `usage_events`, что и бот, с действием `app_<экран>` — отчёт
`scripts/export_usage.py` и `/usage` видят бота и приложение вместе.

Запись — фоновой задачей с повторами, как у счётчика бота (bot/middleware.py,
v2.157.2): ответ странице не ждёт базу, сбой записи только пишется в лог.
Первый экран после запуска приносит ещё и время загрузки (`boot_ms`) — оно
пишется только в лог сервиса, не в базу (диагностика «зависаний» на старте).
"""
import asyncio
import logging
import re

from bot.db.models import UsageEvent

logger = logging.getLogger(__name__)

SCREEN_RE = re.compile(r"^[a-z_]{1,40}$")
WRITE_ATTEMPTS = 4
WRITE_RETRY_DELAY = 0.15
MAX_BOOT_MS = 120_000


class UsageWriter:
    def __init__(self, session_factory) -> None:
        self._factory = session_factory
        self._pending: set[asyncio.Task] = set()

    def record(self, telegram_id: int, screen: str) -> bool:
        """Поставить запись в очередь. False — имя экрана не подходит (не пишется)."""
        if not SCREEN_RE.match(screen):
            return False
        task = asyncio.create_task(self._write(telegram_id, f"app_{screen}"))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return True

    async def drain(self) -> None:
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def _write(self, telegram_id: int, action: str) -> None:
        for attempt in range(WRITE_ATTEMPTS):
            try:
                async with self._factory() as session:
                    session.add(UsageEvent(user_id=telegram_id, action=action))
                    await session.commit()
                return
            except Exception:
                if attempt == WRITE_ATTEMPTS - 1:
                    logger.warning("Mini App: не удалось записать событие использования", exc_info=True)
                    return
                await asyncio.sleep(WRITE_RETRY_DELAY)


def log_boot_time(screen: str, boot_ms) -> None:
    if isinstance(boot_ms, (int, float)) and not isinstance(boot_ms, bool) and 0 < boot_ms < MAX_BOOT_MS:
        logger.info("Mini App: загрузка %d мс до первого экрана (%s)", int(boot_ms), screen)
