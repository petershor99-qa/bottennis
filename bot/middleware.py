import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.db.database import async_session
from bot.db.models import UsageEvent
from bot.services.usage import normalize_action
from bot.utils import CHALLENGE_BUTTON_LABELS

# Кнопки постоянной клавиатуры (main_reply_kb) — обычные текстовые сообщения, не
# callback'и, поэтому раньше не считались и открытия рейтинга/статистики/вызова
# были занижены (v2.148.0). Пишем под теми же именами, что и инлайн-двойники, —
# для /usage это один и тот же экран.
REPLY_KEYBOARD_ACTIONS: dict[str, str] = {
    "📊 Рейтинг": "menu_leaderboard",
    "📈 Статистика": "menu_stats",
    **{label: "menu_play" for label in CHALLENGE_BUTTON_LABELS},
}

logger = logging.getLogger(__name__)


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
    событие, а сбой записи события не должен ронять хендлер. Запись — после
    вызова хендлера, в `finally`, любая ошибка глотается (только
    `logger.warning`): счётчик никогда не должен ломать бота.

    Регистрируется ДВАЖДЫ в main.py с разным `kind` — на `dp.callback_query`
    (kind="callback") и на `dp.message` (kind="command") — вместо одной
    проверки `isinstance(event, ...)` внутри. Дешевле и надёжнее тестировать:
    дуплицированный тип события Telegram (Message vs CallbackQuery) в тестах
    мокается обычными объектами, не настоящими классами aiogram."""

    def __init__(self, kind: str) -> None:
        if kind not in ("callback", "command"):
            raise ValueError(f"Неизвестный kind для UsageMiddleware: {kind!r}")
        self.kind = kind

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        finally:
            await self._record(event)

    async def _record(self, event: TelegramObject) -> None:
        raw_action = self._extract_raw_action(event)
        if raw_action is None:
            return
        user = getattr(event, "from_user", None)
        if user is None:
            return
        try:
            async with async_session() as session:
                session.add(UsageEvent(user_id=user.id, action=normalize_action(raw_action)))
                await session.commit()
        except Exception:
            logger.warning("Не удалось записать событие использования", exc_info=True)

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
