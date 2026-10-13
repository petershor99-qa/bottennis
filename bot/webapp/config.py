"""
Настройки Mini App (v2.160.0) — только из `.env`, чтобы включать и выключать
приложение без релиза (правка `.env` + перезапуск сервиса).

WEBAPP_URL       — внешний HTTPS-адрес приложения (например https://<имя>/).
                   Имя хоста в репозиторий не пишем: оно только в `.env` на VPS.
                   Пусто — приложение выключено: веб-сервер не стартует,
                   кнопки в боте нет.
WEBAPP_AUDIENCE  — кому видна кнопка и кому отвечает API: `admin` (по
                   умолчанию — только ADMIN_ID) или `all` (все игроки).
WEBAPP_HOST/PORT — где слушает веб-сервер внутри VPS (по умолчанию
                   127.0.0.1:8081); снаружи к нему ходит HTTPS-прокси (Caddy).

Значения читаются при каждом вызове, а не при импорте: тесты подменяют их
через monkeypatch.setenv, а `.env` в main.py грузится раньше всего остального.
"""
import os
from urllib.parse import urlencode

from bot.utils import env_int

# Подпись кнопки в боте. Пока приложение видно только админу; перед
# включением всем игрокам текст согласуется с владельцем.
WEBAPP_BUTTON_TEXT = "📱 Открыть приложение"
# Подпись кнопки меню Telegram слева от строки ввода (одна на чат, короткая)
WEBAPP_MENU_BUTTON_TEXT = "Клуб"
# Короткий бот (v2.167.0): одна кнопка вместо глубоких экранов и переход сразу в рейтинг
WEBAPP_MORE_TEXT = "📱 Подробнее в приложении"
WEBAPP_RATING_TEXT = "📱 Рейтинг в приложении"


def webapp_base_url() -> str | None:
    url = os.getenv("WEBAPP_URL", "").strip()
    if not url.startswith("https://"):
        return None  # Telegram открывает Mini App только по HTTPS
    return url.rstrip("/") + "/"


def webapp_audience() -> str:
    return "all" if os.getenv("WEBAPP_AUDIENCE", "").strip().lower() == "all" else "admin"


def webapp_listen() -> tuple[str, int]:
    return os.getenv("WEBAPP_HOST", "127.0.0.1"), env_int("WEBAPP_PORT", 8081)


def is_webapp_allowed(telegram_id: int) -> bool:
    """Доступно ли приложение этому пользователю (без проверки регистрации)."""
    if webapp_base_url() is None:
        return False
    if webapp_audience() == "all":
        return True
    admin_id = env_int("ADMIN_ID")
    return admin_id != 0 and telegram_id == admin_id


def is_admin(telegram_id: int) -> bool:
    """Администратор (ADMIN_ID) — ему в приложении виден экран «Использование»."""
    admin_id = env_int("ADMIN_ID")
    return admin_id != 0 and telegram_id == admin_id


def webapp_url_for(telegram_id: int) -> str | None:
    """URL страницы «Рейтинг клуба» для кнопки, либо None — кнопку не показывать."""
    if not is_webapp_allowed(telegram_id):
        return None
    return webapp_base_url()


def webapp_link(telegram_id: int, route: str = "", **params) -> str | None:
    """Ссылка сразу на экран приложения (v2.167.0): `?r=player/5&from=3`.
    Страница при запуске открывает маршрут `r`; `from` — место до матча для
    анимации подъёма в рейтинге. None — приложение этому игроку недоступно."""
    base = webapp_url_for(telegram_id)
    if base is None:
        return None
    query = {"r": route} if route else {}
    query.update({k: v for k, v in params.items() if v is not None})
    return base + ("?" + urlencode(query) if query else "")


def slim_bot_for(telegram_id: int) -> bool:
    """Короткий бот (v2.167.0): если приложение игроку доступно, глубокая
    статистика в боте прячется за кнопкой «Подробнее в приложении». Без
    приложения (WEBAPP_URL пуст или не та аудитория) бот полный, как раньше."""
    return webapp_url_for(telegram_id) is not None
