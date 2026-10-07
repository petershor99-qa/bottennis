import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.db.database import async_session
from bot.db.models import UsageEvent
from bot.services.usage import normalize_action
from bot.utils import CHALLENGE_BUTTON_LABELS, REPLY_KB_LEADERBOARD_ALL, REPLY_KB_PROFILE_ALL

# Кнопки постоянной клавиатуры (main_reply_kb) — обычные текстовые сообщения, не
# callback'и, поэтому раньше не считались и открытия рейтинга/статистики/вызова
# были занижены (v2.148.0). Пишем под теми же именами, что и инлайн-двойники, —
# для /usage это один и тот же экран.
REPLY_KEYBOARD_ACTIONS: dict[str, str] = {
    **{text: "menu_leaderboard" for text in REPLY_KB_LEADERBOARD_ALL},
    **{text: "menu_stats" for text in REPLY_KB_PROFILE_ALL},
    **{label: "menu_play" for label in CHALLENGE_BUTTON_LABELS},
}

logger = logging.getLogger(__name__)

_WRITE_ATTEMPTS = 4
_WRITE_RETRY_DELAY = 0.15  # секунд между попытками записи события


class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            result = await handler(event, data)
            await session.commit()
            return result


class UsageMiddleware(BaseMiddleware):
    """Пассивный счётчик открытий экранов (v2.132.0, этап 1 дорожной карты
    из CLAUDE.md) — питает /usage и будущую уборку неиспользуемого (этап 4).
    Игроки ничего не видят и не замечают: ни одного нового сообщения/кнопки.

    Пишет в СВОЮ отдельную сессию (`async_session()` напрямую, не
    `data["session"]` хендлера) — откат транзакции хендлера не должен терять
    событие, а сбой записи события не должен ронять хендлер. Любая ошибка
    глотается (только `logger.warning`): счётчик никогда не должен ломать бота.

    Запись идёт ФОНОВОЙ задачей, а не `await` внутри `finally` (v2.157.2).
    UsageMiddleware — внутренний, а DatabaseMiddleware коммитит транзакцию
    хендлера только после него. SQLite допускает одного писателя, поэтому
    синхронная запись события во второй сессии ждала блокировку, которую
    держит сама же обрабатываемая транзакция, и упиралась в busy-timeout
    (~5 с): кнопки с записью (голос, вызов, результат) отвечали с задержкой,
    событие терялось. Фоновая задача стартует независимо и успевает после
    коммита; при занятой БД пробует ещё несколько раз с короткой паузой.

    Регистрируется ДВАЖДЫ в main.py с разным `kind` — на `dp.callback_query`
    (kind="callback") и на `dp.message` (kind="command") — вместо одной
    проверки `isinstance(event, ...)` внутри. Дешевле и надёжнее тестировать:
    дуплицированный тип события Telegram (Message vs CallbackQuery) в тестах
    мокается обычными объектами, не настоящими классами aiogram."""

    def __init__(self, kind: str) -> None:
        if kind not in ("callback", "command"):
            raise ValueError(f"Неизвестный kind для UsageMiddleware: {kind!r}")
        self.kind = kind
        # Ссылки на фоновые записи: без них event loop может собрать задачу
        # до завершения (asyncio хранит на задачи только слабые ссылки).
        self._pending: set[asyncio.Task] = set()

    async def drain(self) -> None:
        """Дождаться всех фоновых записей — для тестов и корректной остановки."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        finally:
            self._schedule_record(event)

    def _schedule_record(self, event: TelegramObject) -> None:
        raw_action = self._extract_raw_action(event)
        if raw_action is None:
            return
        user = getattr(event, "from_user", None)
        if user is None:
            return
        task = asyncio.create_task(self._write(user.id, normalize_action(raw_action)))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    @staticmethod
    async def _write(user_id: int, action: str) -> None:
        for attempt in range(_WRITE_ATTEMPTS):
            try:
                async with async_session() as session:
                    session.add(UsageEvent(user_id=user_id, action=action))
                    await session.commit()
                return
            except Exception:
                if attempt == _WRITE_ATTEMPTS - 1:
                    logger.warning("Не удалось записать событие использования", exc_info=True)
                    return
                await asyncio.sleep(_WRITE_RETRY_DELAY)

    def _extract_raw_action(self, event: TelegramObject) -> str | None:
        if self.kind == "callback":
            return event.data
        # kind == "command" — реальные команды и кнопки постоянной клавиатуры;
        # прямой ввод счёта (например "11:7 9:11") молчаливо не считается.
        text = event.text
        if not text:
            return None
        if text.startswith("/"):
            return text
        return REPLY_KEYBOARD_ACTIONS.get(text)
