"""
Бюджет загрузки Mini App (v2.180.0) — в настоящем браузере, на настоящей
index.html. Чтобы новые фичи не раздули первый запуск незаметно:
- первый запуск (пустой кэш) передаёт не больше FIRST_LOAD_KB статики;
- повторный — только index.html (остальное в кэше навсегда, адреса с версией);
- на «быстром 3G» главная появляется не позже FAST_3G_MS;
- мгновенный старт: при повторном запуске главная из снимка видна, даже если
  сервер не отвечает; чужой снимок другому игроку не показывается.
Байты — главный критерий (точный); время — с эмуляцией сети и запасом.
"""
from urllib.parse import quote

import pytest

from tests.e2e.conftest import playwright_api  # noqa: F401  (пропуск без Playwright)
from tests.test_webapp import _init_data

PREVIEW_TOKEN = "123456:PREVIEW"   # тот же, что в scripts/preview_miniapp.py
# статика первого запуска; было ~160 КБ до v2.180.0, стало ~135; v2.188.0 — 180:
# +26 КБ картинки заставки (решение владельца), сейчас ~168
FIRST_LOAD_KB = 180
REPEAT_STATIC_KB = 8               # повторный запуск: только index.html
FAST_3G_MS = 3000                  # главная на «быстром 3G» (1,6 Мбит/с, 150 мс); замер — ~1,5 с
BRIDGE = "window.TelegramWebviewProxy = { postEvent: function () {} };"
STATIC_JS = """performance.getEntriesByType('resource')
  .filter(e => !e.name.includes('/api/'))
  .reduce((a, e) => a + (e.transferSize || 0), 0) + (performance.getEntriesByType('navigation')[0].transferSize || 0)"""


def _url(base_url, user_id=1001):
    init = quote(_init_data(user_id, token=PREVIEW_TOKEN), safe="")
    return f"{base_url}/#tgWebAppData={init}&tgWebAppVersion=8.0&tgWebAppPlatform=android"


def _context(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.add_init_script(BRIDGE)
    return ctx


def test_first_and_repeat_load_fit_budget(browser, base_url):
    ctx = _context(browser)
    page = ctx.new_page()
    page.goto(_url(base_url))
    page.wait_for_selector("text=Клуб сегодня")
    page.wait_for_timeout(1500)                       # шрифты и предзагрузка вкладок
    first_kb = page.evaluate(STATIC_JS) / 1024
    page.close()
    page = ctx.new_page()
    page.goto(_url(base_url))
    page.wait_for_selector("text=Клуб сегодня")
    page.wait_for_timeout(1500)
    repeat_kb = page.evaluate(STATIC_JS) / 1024
    ctx.close()
    print(f"первый запуск: {first_kb:.0f} КБ статики, повторный: {repeat_kb:.1f} КБ")
    assert first_kb <= FIRST_LOAD_KB, f"первый запуск {first_kb:.0f} КБ > бюджета {FIRST_LOAD_KB} КБ"
    assert repeat_kb <= REPEAT_STATIC_KB, f"повторный запуск {repeat_kb:.1f} КБ — статика не из кэша?"


def test_home_on_fast_3g(browser, base_url):
    ctx = _context(browser)
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 150,
             "downloadThroughput": 1600 * 1000 / 8, "uploadThroughput": 750 * 1000 / 8})
    page.goto(_url(base_url), wait_until="commit")
    page.wait_for_selector("text=Клуб сегодня", timeout=20000)
    ms = page.evaluate("Math.round(performance.now())")
    ctx.close()
    print(f"главная на быстром 3G: {ms} мс")
    assert ms <= FAST_3G_MS, f"главная на быстром 3G за {ms} мс > {FAST_3G_MS} мс"


def test_snapshot_shows_home_when_server_is_silent(browser, base_url):
    ctx = _context(browser)
    page = ctx.new_page()
    page.goto(_url(base_url))
    page.wait_for_selector("text=Клуб сегодня")
    page.wait_for_function("!!localStorage.getItem('snap_home_v1')")
    page.close()
    page = ctx.new_page()
    page.route("**/api/home", lambda route: None)     # главная с сервера не приходит
    page.goto(_url(base_url), wait_until="commit")
    page.wait_for_selector("text=Клуб сегодня", timeout=3000)
    assert page.locator("#snap-note").inner_text() == "Обновляется…"
    page.close()
    page = ctx.new_page()                             # другой игрок на том же устройстве
    page.route("**/api/**", lambda route: route.abort())
    page.goto(_url(base_url, user_id=2002), wait_until="commit")
    page.wait_for_timeout(1500)
    assert "Клуб сегодня" not in page.locator("#app").inner_text()
    ctx.close()


@pytest.mark.parametrize("dark", [False, True])
def test_profile_stat_colors_render(browser, base_url, dark):
    from tests.e2e.conftest import open_page

    page = open_page(browser, f"{base_url}/preview{'?dark=1' if dark else ''}#/player/1", dark=dark)
    page.wait_for_selector(".card-stat-value.tier")
    tiers = page.evaluate("[...document.querySelectorAll('.card-stat-value')]"
                          ".map(e => e.className.split(' ').find(c => c.startsWith('tier-')).slice(5))")
    page.context.close()
    assert len(tiers) == 6 and set(tiers) <= {"gray", "white", "green", "gold", "red"}
