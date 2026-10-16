"""
РЕГРЕССИЯ v2.178.0: Mini App с телефона «висела на первых процентах» — Telegram
держит свою заставку с полоской, пока приложение не скажет «готово»
(web_app_ready) или страница не загрузится целиком, а оба события ждали скрипт
с telegram.org. Если он у игрока отвечал медленно или висел, заставка не
убиралась, хотя приложение под ней было готово. Воспроизведено стендом в
браузере: при висящем telegram.org заставка не убиралась никогда, при ответе
через 8 с — через 8,3 с; после исправления — за 0,02–0,04 с во всех сценариях.
Проверка в настоящем браузере — tests/e2e/test_boot_splash_e2e.py.
Запуск: pytest tests/test_boot_splash.py
"""
import gzip
import re
from pathlib import Path

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from bot.webapp.assets import IMMUTABLE
from bot.webapp.server import STATIC_DIR, create_app

INDEX = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
LOADER = INDEX[INDEX.index("(function () {"):]
VENDORED = (STATIC_DIR / "telegram-web-app.js").read_text(encoding="utf-8")


def test_no_request_to_telegram_org():
    assert "https://telegram.org" not in INDEX
    sources = list((STATIC_DIR.parent / "src" / "app").glob("*.js"))
    assert sources and all("telegram.org/js" not in p.read_text(encoding="utf-8") for p in sources)


def test_loader_runs_before_any_file():
    """РЕГРЕССИЯ v2.194.0: загрузчик внизу страницы ждал стили и скрипт Telegram
    (120 КБ) — на медленной сети до 30 с, и полоска Telegram «шла и вставала»
    (15.10, Android: 30 231 мс, из них 29 856 — до прихода скриптов). Теперь он
    первый в <head>: до стилей, предзагрузок и любых скриптов с адресом."""
    loader = INDEX.index("(function () {")
    for tag in ('<link rel="stylesheet"', '<link rel="preload"', "<script src="):
        assert loader < INDEX.index(tag), tag
    assert loader < INDEX.index("</head>")


def test_vendored_script_does_not_block_page():
    tag = re.search(r'<script src="static/telegram-web-app\.js"([^>]*)></script>', INDEX)
    assert tag and " defer" in tag.group(1)
    # пришёл позже приложения — подключается к нему (кнопка «Назад», тема, вибрация)
    assert "window.__clubOnTelegram" in tag.group(1)
    # app.js запускается после разметки, но не ждёт скрипт Telegram
    assert INDEX.index("telegram-web-app.js\" defer") < INDEX.index("window.__clubBoot.start();")


def test_ready_is_sent_immediately_with_raw_fallback():
    # «готово» уходит до запуска app.js — заставка Telegram не ждёт ни скрипт, ни приложение
    assert LOADER.index('postEvent("web_app_ready")') < LOADER.index("boot.start = ")
    assert LOADER.index('postEvent("web_app_ready")') < LOADER.index("setTimeout(watchdog")
    # мост Telegram — все три пути, как telegram-web-app.js
    for bridge in ("TelegramWebviewProxy.postEvent", "window.external.notify", "window.parent.postMessage"):
        assert bridge in LOADER


@pytest.mark.parametrize("name", ["web_app_ready", "TelegramWebviewProxy", "isVersionAtLeast",
                                  "disableVerticalSwipes", "CloudStorage", "HapticFeedback", "BackButton",
                                  "__telegram__", "initParams"])
def test_vendored_script_has_what_the_app_uses(name):
    assert name in VENDORED


def test_update_script_requires_the_same_api():
    src = (Path(__file__).resolve().parent.parent / "scripts" / "update_telegram_js.py").read_text(encoding="utf-8")
    for name in ("web_app_ready", "disableVerticalSwipes", "CloudStorage"):
        assert f'"{name}"' in src


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    async with TestClient(TestServer(create_app("123456:TEST-token", db_factory))) as c:
        yield c


async def test_vendored_script_served_gzipped_and_immutable(client):
    html = await (await client.get("/")).text()
    v = re.search(r"static/telegram-web-app\.js\?v=([0-9a-f]{12})", html).group(1)
    async with ClientSession(auto_decompress=False) as raw:
        resp = await raw.get(client.make_url(f"/static/telegram-web-app.js?v={v}"), headers={"Accept-Encoding": "gzip"})
        body = await resp.read()
    assert resp.status == 200 and resp.headers["Cache-Control"] == IMMUTABLE
    assert resp.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(body).decode("utf-8") == VENDORED
    assert len(body) < len(VENDORED.encode()) / 3          # 120 КБ -> около 30 КБ
