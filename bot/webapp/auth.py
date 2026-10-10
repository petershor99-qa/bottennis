"""
Вход в Mini App через `initData` (v2.160.0). Паролей нет: Telegram передаёт
странице строку `initData`, подписанную ключом из токена бота. Страница шлёт
её в заголовке `Authorization: tma <initData>`, сервер проверяет подпись
(готовая проверка aiogram — HMAC-SHA256 по правилам документации Telegram)
и свежесть, затем берёт `user.id` и ищет игрока по `telegram_id`.
"""
from datetime import datetime, timezone

from aiogram.utils.web_app import safe_parse_webapp_init_data

# Сколько живёт подписанная строка. Mini App открывают на минуты, сутки —
# с запасом; старая перехваченная строка дольше не годится.
INIT_DATA_MAX_AGE_SECONDS = 24 * 60 * 60


class WebAppAuthError(Exception):
    """Подпись неверна, строка устарела или в ней нет пользователя."""


def telegram_id_from_init_data(
    bot_token: str, init_data: str, now: datetime | None = None,
) -> int:
    if not init_data:
        raise WebAppAuthError("нет initData")
    try:
        data = safe_parse_webapp_init_data(bot_token, init_data)
    except ValueError as e:
        raise WebAppAuthError("неверная подпись") from e
    if data.user is None:
        raise WebAppAuthError("нет пользователя")
    now = now or datetime.now(timezone.utc)
    auth_date = data.auth_date
    if auth_date.tzinfo is None:
        auth_date = auth_date.replace(tzinfo=timezone.utc)
    age = (now - auth_date).total_seconds()
    if age > INIT_DATA_MAX_AGE_SECONDS or age < -300:
        raise WebAppAuthError("initData устарела")
    return data.user.id


def init_data_from_header(value: str | None) -> str:
    """`Authorization: tma <initData>` -> `<initData>` (пусто, если формат другой)."""
    if not value:
        return ""
    scheme, _, rest = value.partition(" ")
    return rest.strip() if scheme.lower() == "tma" else ""
