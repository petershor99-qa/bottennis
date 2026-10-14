"""
РЕГРЕССИЯ v2.178.0 в настоящем браузере: настоящая index.html, мост Telegram
подменён (записывает события), telegram.org не отвечает. Telegram убирает свою
заставку по web_app_ready — оно должно уйти сразу, что бы ни было с сетью.
До исправления при висящем telegram.org web_app_ready не уходил никогда.
"""
from urllib.parse import quote

import pytest

from tests.e2e.conftest import playwright_api  # noqa: F401  (пропуск без Playwright)
from tests.test_webapp import _init_data

PREVIEW_TOKEN = "123456:PREVIEW"   # тот же, что в scripts/preview_miniapp.py
BRIDGE = """
window.__ev = [];
window.TelegramWebviewProxy = { postEvent: function (t) { window.__ev.push([t, performance.now()]); } };
"""


def _launch_url(base_url):
    init = quote(_init_data(1001, token=PREVIEW_TOKEN), safe="")
    return f"{base_url}/#tgWebAppData={init}&tgWebAppVersion=8.0&tgWebAppPlatform=android"


@pytest.mark.parametrize("broken", ["telegram_org_hangs", "own_script_fails", "app_js_slow"])
def test_splash_released_at_once(browser, base_url, broken):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.add_init_script(BRIDGE)
    page = ctx.new_page()
    page.route("https://telegram.org/**", lambda route: None)          # никогда не отвечает
    if broken == "own_script_fails":
        page.route("**/static/telegram-web-app.js*", lambda route: route.abort())
    if broken == "app_js_slow":
        page.route("**/static/app.js*", lambda route: page.wait_for_timeout(4000) or route.continue_())
    page.goto(_launch_url(base_url), wait_until="commit")
    page.wait_for_function("window.__ev && window.__ev.some(e => e[0] === 'web_app_ready')", timeout=3000)
    ready_ms = page.evaluate("window.__ev.find(e => e[0] === 'web_app_ready')[1]")
    assert ready_ms < 1500, f"заставка Telegram убралась бы только через {ready_ms:.0f} мс"
    page.wait_for_selector("text=Клуб сегодня", timeout=15000)         # и приложение запустилось
    tg_requests = page.evaluate("performance.getEntriesByType('resource').filter(e => e.name.includes('telegram.org')).length")
    assert tg_requests == 0
    ctx.close()
