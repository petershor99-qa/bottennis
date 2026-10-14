"""Обновить копию скрипта Telegram для Mini App (v2.178.0).

Приложение берёт `telegram-web-app.js` со своего сервера, а не с telegram.org:
если telegram.org у игрока отвечает медленно или висит, Telegram не убирал свою
заставку с полоской загрузки (приложение висело «на первых процентах»). Копия
лежит в webapp/static/telegram-web-app.js и отдаётся сжатой, с версией в адресе.

Обновлять при появлении нужных новых возможностей Telegram (раз в несколько
месяцев) и при каждом заметном обновлении клиентов. Запуск (нужна сеть):
  py -3.13 scripts/update_telegram_js.py
"""
import sys
import urllib.request
from pathlib import Path

URL = "https://telegram.org/js/telegram-web-app.js"
TARGET = Path(__file__).resolve().parent.parent / "webapp" / "static" / "telegram-web-app.js"
# То, чем пользуется приложение, — без этого копия не годится
REQUIRED = ["web_app_ready", "TelegramWebviewProxy", "isVersionAtLeast", "disableVerticalSwipes",
            "CloudStorage", "HapticFeedback", "BackButton"]


def main() -> None:
    with urllib.request.urlopen(URL, timeout=30) as resp:
        body = resp.read()
    text = body.decode("utf-8")
    missing = [name for name in REQUIRED if name not in text]
    if missing:
        sys.exit(f"В скачанном скрипте нет: {', '.join(missing)} — копия не обновлена")
    old = TARGET.read_bytes() if TARGET.exists() else b""
    TARGET.write_bytes(body)
    print(f"{TARGET.name}: {len(old)} -> {len(body)} байт" + (" (без изменений)" if old == body else ""))


if __name__ == "__main__":
    main()
