"""
Счётчик нажатий в Mini App (v2.166.0). Страница после каждого показанного
экрана шлёт `POST /api/track {"screen": "me_matches"}`, сервер пишет событие в
ту же таблицу `usage_events`, что и бот, с действием `app_<экран>` — отчёт
`scripts/export_usage.py` и `/usage` видят бота и приложение вместе.

Запись — фоновой задачей с повторами, как у счётчика бота (bot/middleware.py,
v2.157.2): ответ странице не ждёт базу, сбой записи только пишется в лог.
Первый экран после запуска приносит ещё и время загрузки (`boot_ms`) — оно
пишется только в лог сервиса, не в базу (диагностика «зависаний» на старте).
С v2.175.0 к нему добавлены клиент и версия Telegram и время, когда пришёл
скрипт Telegram, а страница, которая не смогла запуститься, присылает отчёт
(`log_boot_failure`) — тоже только в лог.
"""
import asyncio
import logging
import re
import time

from bot.db.models import UsageEvent

logger = logging.getLogger(__name__)

SCREEN_RE = re.compile(r"^[a-z_]{1,40}$")
CLIENT_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,24}$")
BOOT_FAIL_COOLDOWN_S = 60
BOOT_FAIL_TEXT_MAX = 300
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


def _ms(value) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < MAX_BOOT_MS:
        return int(value)
    return None


def _client(value) -> str:
    """Платформа или версия Telegram от страницы — только безопасные символы."""
    text = str(value) if value is not None else ""
    return text if CLIENT_RE.match(text) else "?"


def log_boot_time(screen: str, boot_ms, info: dict | None = None) -> None:
    """Время первого экрана. info — что ещё прислала страница (v2.175.0):
    platform, version (клиент Telegram), tg_ms — когда пришёл скрипт Telegram
    (None — не пришёл, приложение стартовало без него)."""
    ms = _ms(boot_ms)
    if not ms:
        return
    info = info if isinstance(info, dict) else {}
    tg_ms = _ms(info.get("tg_ms"))
    logger.info(
        "Mini App: загрузка %d мс до первого экрана (%s), клиент %s %s, скрипт Telegram %s",
        ms, screen, _client(info.get("platform")), _client(info.get("version")),
        f"{tg_ms} мс" if tg_ms is not None else "не пришёл",
    )


_last_failure: dict[int, float] = {}


def log_boot_failure(telegram_id: int, body) -> bool:
    """Страница не смогла запуститься (v2.175.0) — записать причину в лог. Не
    чаще раза в BOOT_FAIL_COOLDOWN_S на игрока. False — отчёт отброшен."""
    now = time.monotonic()
    if now - _last_failure.get(telegram_id, -BOOT_FAIL_COOLDOWN_S) < BOOT_FAIL_COOLDOWN_S:
        return False
    _last_failure[telegram_id] = now
    body = body if isinstance(body, dict) else {}
    stage = str(body.get("stage", ""))[:40]
    error = " ".join(str(body.get("error", "")).split())[:BOOT_FAIL_TEXT_MAX]
    logger.warning(
        "Mini App: не запустилось у игрока %s (клиент %s %s): этап «%s», скрипт Telegram %s, ошибка: %s",
        telegram_id, _client(body.get("platform")), _client(body.get("version")), stage or "?",
        "пришёл" if body.get("tg") else "не пришёл", error or "нет",
    )
    return True
