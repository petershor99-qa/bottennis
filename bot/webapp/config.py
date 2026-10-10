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

from bot.utils import env_int

# Подпись кнопки в боте. Пока приложение видно только админу; перед
# включением всем игрокам текст согласуется с владельцем.
WEBAPP_BUTTON_TEXT = "Открыть приложение"


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


def webapp_url_for(telegram_id: int) -> str | None:
    """URL страницы «Рейтинг клуба» для кнопки, либо None — кнопку не показывать."""
    if not is_webapp_allowed(telegram_id):
        return None
    return webapp_base_url()
