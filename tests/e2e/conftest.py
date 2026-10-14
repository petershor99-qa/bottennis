"""
Тесты Mini App в настоящем браузере (v2.177.0, Playwright). Не входят в обычный
`pytest`: запускаются с `RUN_E2E=1` (скилл `app-check`, задача e2e в CI).
Страница поднимается превью-сервером (scripts/preview_miniapp.py) на временной
базе с вымышленными игроками.

Браузер: в CI — Chromium из Playwright, локально можно указать установленный
(`E2E_CHANNEL=msedge` или `chrome`), если скачать Chromium нельзя.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

if os.environ.get("RUN_E2E") != "1":
    pytest.skip("тесты в браузере — только с RUN_E2E=1", allow_module_level=True)

playwright_api = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    port = _free_port()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "preview_miniapp.py"), str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8")
    deadline = time.time() + 60
    ready = False
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        if line.startswith("ready"):
            ready = True
            break
    if not ready:
        proc.kill()
        pytest.fail("превью-сервер не поднялся")
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def browser():
    with playwright_api.sync_playwright() as p:
        channel = os.environ.get("E2E_CHANNEL") or None
        b = p.chromium.launch(channel=channel, headless=True)
        yield b
        b.close()


def open_page(browser, url: str, *, width=390, height=844, dark=False, touch=False):
    """Новая страница с записью ошибок JS и неудачных запросов (кроме фото)."""
    ctx = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch,
                              color_scheme="dark" if dark else "light", reduced_motion="reduce")
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(f"JS: {e}"))
    page.on("response", lambda r: page.errors.append(f"{r.status} {r.url}")
            if r.status >= 400 and "/api/photo/" not in r.url else None)
    page.goto(url)
    return page


def wait_screen(page):
    """Экран показан: нет «Загрузки», нет скелетов, есть текст."""
    page.wait_for_function(
        "() => { const a = document.getElementById('app');"
        " return a && !document.getElementById('boot-state') && !a.querySelector('.sk')"
        " && a.innerText.length > 30; }",
        timeout=15000,
    )
    page.wait_for_timeout(150)
