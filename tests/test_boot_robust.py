"""
v2.175.0: приложение стартует без скрипта Telegram (у игрока на Linux telegram.org
не открывался — оставалась «Загрузка…»), запросы с ограничением по времени,
отчёт «не запустилось» и клиент Telegram в журнале запуска.
Запуск: pytest tests/test_boot_robust.py
"""
import logging
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import bot.webapp.usage as usage
from bot.webapp.server import USAGE_KEY, create_app
from bot.webapp.usage import log_boot_failure, log_boot_time
from tests.conftest import _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"


@pytest.fixture(autouse=True)
def _fresh_failures():
    usage._last_failure.clear()
    yield
    usage._last_failure.clear()


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    async with db_factory() as s:
        s.add(_player(ADMIN, "Алиса"))
        await s.commit()
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        yield c


# ── Загрузчик и страница ──────────────────────────────────────────────────────

def test_loader_starts_app_without_waiting_for_telegram():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    body = html[html.index("(function () {"):]
    # app.js грузится сразу; скрипт Telegram — своя копия, без попыток к telegram.org (v2.178.0)
    assert "bootApp();" in body and "attempt()" not in body
    # сторож: нет первого экрана — сообщение, «Обновить» и отчёт на сервер
    assert "WATCHDOG_MS" in body and "api/boot_fail" in body and "Обновить" in body


def test_app_reads_launch_params_and_attaches_telegram_later():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "__telegram__initParams" in js           # скрипт Telegram, пришедший позже, найдёт параметры
    assert "window.__clubOnTelegram = attachTelegram" in js
    assert "tg && tg.initData;" not in js           # подпись — через initDataNow()
    assert "FETCH_TIMEOUT_MS" in js and "timedFetch(" in js
    # tg.ready() и прочее — внутри try: сбой старого клиента не роняет старт
    attach = js[js.index("function attachTelegram()"):js.index("window.__clubOnTelegram = attachTelegram")]
    assert attach.index("try {") < attach.index("tg.ready()")


# ── Журнал запуска ────────────────────────────────────────────────────────────

def test_boot_time_logs_client_and_telegram_script(caplog):
    with caplog.at_level(logging.INFO, logger="bot.webapp.usage"):
        log_boot_time("home", 1234.5, {"platform": "tdesktop", "version": "7.10", "tg_ms": 410})
        log_boot_time("rating", 900, {"platform": "android", "version": "8.0", "tg_ms": None})
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0] == ("Mini App: загрузка 1234 мс до первого экрана (home), клиент tdesktop 7.10, "
                        "скрипт Telegram 410 мс")
    assert lines[1].endswith("клиент android 8.0, скрипт Telegram не пришёл")


def test_boot_time_sanitizes_client_and_ignores_bad_time(caplog):
    with caplog.at_level(logging.INFO, logger="bot.webapp.usage"):
        log_boot_time("home", 500, {"platform": "<script>", "version": "x" * 40})
        log_boot_time("home", "много")
        log_boot_time("home", 10**9)
        log_boot_time("home", 300)  # старая страница без info
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 2
    assert "клиент ? ?" in lines[0] and "<script>" not in lines[0]
    assert "клиент ? ?" in lines[1]


def test_boot_failure_logged_once_per_minute(caplog, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(usage.time, "monotonic", lambda: clock[0])
    body = {"stage": "app_js_pending", "error": "TypeError:  x\nis undefined", "tg": False,
            "platform": "tdesktop", "version": "6.0"}
    with caplog.at_level(logging.WARNING, logger="bot.webapp.usage"):
        assert log_boot_failure(42, body) is True
        assert log_boot_failure(42, body) is False      # повтор в течение минуты
        assert log_boot_failure(43, body) is True       # другой игрок — свой отсчёт
        clock[0] += 61
        assert log_boot_failure(42, {"error": "y" * 1000}) is True
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0] == ("Mini App: не запустилось у игрока 42 (клиент tdesktop 6.0): этап «app_js_pending», "
                        "скрипт Telegram не пришёл, ошибка: TypeError: x is undefined")
    assert len(lines) == 3 and len(lines[2]) < 500


async def test_boot_fail_endpoint_needs_signature(client, caplog):
    with caplog.at_level(logging.WARNING, logger="bot.webapp.usage"):
        denied = await client.post("/api/boot_fail", json={"stage": "x"})
        ok = await client.post("/api/boot_fail", json={"stage": "no_first_screen", "tg": True}, headers=client.auth)
        broken = await client.post("/api/boot_fail", data="не json", headers=client.auth)
    assert denied.status == 401
    assert ok.status == 200 and (await ok.json()) == {"ok": True}
    assert broken.status == 200 and (await broken.json()) == {"ok": False}  # повтор в течение минуты
    assert any("этап «no_first_screen», скрипт Telegram пришёл" in r.getMessage() for r in caplog.records)


async def test_track_passes_client_info_to_boot_log(client, caplog):
    with caplog.at_level(logging.INFO, logger="bot.webapp.usage"):
        resp = await client.post("/api/track", json={
            "screen": "home", "boot_ms": 777, "platform": "tdesktop", "version": "7.10", "tg_ms": 120,
        }, headers=client.auth)
        assert resp.status == 200
        await client.app[USAGE_KEY].drain()
    assert any("777 мс до первого экрана (home), клиент tdesktop 7.10, скрипт Telegram 120 мс" in r.getMessage()
               for r in caplog.records)
