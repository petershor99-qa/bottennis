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

from bot.db.models import AppBoot, UsageEvent

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

    def record_boot(self, ms: int, platform: str, failed: bool = False) -> None:
        """Время запуска — в таблицу app_boots (v2.177.0), фоном, как и события.
        failed — запуск не дошёл до первого экрана (v2.180.0), ms = 0."""
        task = asyncio.create_task(self._write_boot(ms, platform, failed))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _write_boot(self, ms: int, platform: str, failed: bool = False) -> None:
        try:
            async with self._factory() as session:
                session.add(AppBoot(ms=ms, platform=platform, failed=failed))
                await session.commit()
        except Exception:
            logger.warning("Mini App: не удалось записать время запуска", exc_info=True)

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


def client_name(value) -> str:
    """Безопасное имя клиента Telegram для записи (v2.180.0)."""
    return _client(value)


def _client(value) -> str:
    """Платформа или версия Telegram от страницы — только безопасные символы."""
    text = str(value) if value is not None else ""
    return text if CLIENT_RE.match(text) else "?"


def log_boot_time(screen: str, boot_ms, info: dict | None = None) -> tuple[int, str] | None:
    """Время первого экрана. info — что ещё прислала страница (v2.175.0):
    platform, version (клиент Telegram), tg_ms — когда пришёл скрипт Telegram
    (None — не пришёл, приложение стартовало без него)."""
    ms = _ms(boot_ms)
    if not ms:
        return None
    info = info if isinstance(info, dict) else {}
    tg_ms = _ms(info.get("tg_ms"))
    logger.info(
        "Mini App: загрузка %d мс до первого экрана (%s), клиент %s %s, скрипт Telegram %s",
        ms, screen, _client(info.get("platform")), _client(info.get("version")),
        f"{tg_ms} мс" if tg_ms is not None else "не пришёл",
    )
    platform = _client(info.get("platform"))
    return ms, ("" if platform == "?" else platform)


CLIENT_ERRORS_PER_MINUTE = 5
_client_errors: dict[int, list[float]] = {}


def log_client_error(telegram_id: int, body) -> bool:
    """Ошибка JS на устройстве игрока (v2.177.0) — в журнал сервиса, не чаще
    CLIENT_ERRORS_PER_MINUTE в минуту на игрока. Только лог, без уведомлений."""
    now = time.monotonic()
    recent = [t for t in _client_errors.get(telegram_id, []) if now - t < 60]
    if len(recent) >= CLIENT_ERRORS_PER_MINUTE:
        _client_errors[telegram_id] = recent
        return False
    _client_errors[telegram_id] = recent + [now]
    body = body if isinstance(body, dict) else {}
    message = " ".join(str(body.get("message", "")).split())[:BOOT_FAIL_TEXT_MAX]
    where = " ".join(str(body.get("where", "")).split())[:120]
    route = str(body.get("route", ""))[:60]
    logger.warning("Mini App: ошибка JS у игрока %s (клиент %s %s), экран «%s»: %s @ %s",
                   telegram_id, _client(body.get("platform")), _client(body.get("version")),
                   route, message or "?", where or "?")
    return True


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
