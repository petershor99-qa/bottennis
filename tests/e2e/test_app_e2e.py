"""
Проход по Mini App в браузере (v2.177.0): каждый экран открывается без ошибок
JS и неудачных запросов, без горизонтальной прокрутки, в обеих темах и на двух
ширинах; основные действия работают. Запуск: RUN_E2E=1 pytest tests/e2e
"""
import pytest

from tests.e2e.conftest import open_page, wait_screen

ROUTES = ["", "rating", "player/1", "player/1/matches", "player/1/achievements", "player/1/style",
          "player/3", "h2h/3", "records", "matches", "activity/club", "throne", "summary/day",
          "summary/week", "summary/month", "when", "chronicle", "scores", "month", "usage"]


@pytest.mark.parametrize("width,dark", [(390, False), (390, True), (1100, False)],
                         ids=["phone-light", "phone-dark", "desktop"])
def test_every_screen_renders_cleanly(browser, base_url, width, dark):
    page = open_page(browser, f"{base_url}/preview{'?dark=1' if dark else ''}#/", width=width, dark=dark)
    problems = []
    for route in ROUTES:
        page.evaluate(f"location.hash = '#/{route}'")
        try:
            wait_screen(page)
        except Exception:
            problems.append(f"{route}: экран не показался")
            continue
        state = page.evaluate("""() => ({
            error: !!document.querySelector('#app .state-error'),
            overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
        })""")
        if state["error"]:
            problems.append(f"{route}: экран ошибки")
        if state["overflow"]:
            problems.append(f"{route}: горизонтальная прокрутка")
    page.context.close()
    assert problems == [] and page.errors == [], problems + page.errors


def test_starts_without_telegram_script(browser, base_url):
    """Скрипт telegram.org не пришёл — приложение всё равно открывается (v2.175.0)."""
    page = open_page(browser, f"{base_url}/preview?tg=none")
    wait_screen(page)
    assert "Клуб" in page.inner_text("#tabbar")
    page.evaluate("location.hash = '#/rating'")
    wait_screen(page)
    assert page.errors == []
    page.context.close()


def test_one_reaction_per_player(browser, base_url):
    page = open_page(browser, f"{base_url}/preview#/")
    wait_screen(page)
    match_id = page.evaluate("""async () => {
        const r = await fetch('api/matches?offset=0', {headers: {Authorization: 'tma ' + window.Telegram.WebApp.initData}});
        return (await r.json()).matches[0].id; }""")
    page.evaluate(f"location.hash = '#/match/{match_id}'")
    wait_screen(page)
    buttons = page.locator(".reactions .reaction")
    buttons.nth(0).click()
    page.wait_for_timeout(400)
    buttons.nth(3).click()
    page.wait_for_timeout(400)
    mine = page.evaluate("[...document.querySelectorAll('.reactions .reaction')].map(b => b.classList.contains('is-mine'))")
    assert mine == [False, False, False, True]
    page.context.close()


def test_history_filter_and_versus_row(browser, base_url):
    page = open_page(browser, f"{base_url}/preview#/player/1/matches")
    wait_screen(page)
    total = page.inner_text(".group-count")
    page.locator(".filters .chip", has_text="7 дней").click()
    assert page.inner_text(".group-count") != total
    page.locator(".versus-row-item").first.click()
    active = page.evaluate("[...document.querySelectorAll('.filters .chip.is-active')].map(c => c.textContent)")
    assert len(active) == 2 and "Все" not in active
    page.context.close()


def test_month_story_advances_and_goes_back(browser, base_url):
    page = open_page(browser, f"{base_url}/preview#/month")
    wait_screen(page)
    box = page.locator(".story").bounding_box()
    first = page.inner_text(".story-card")
    page.mouse.click(box["x"] + box["width"] - 20, box["y"] + 100)
    assert page.inner_text(".story-card") != first
    page.mouse.click(box["x"] + 20, box["y"] + 100)
    assert page.inner_text(".story-card") == first
    page.context.close()


def test_swipe_switches_profile_tab(browser, base_url):
    page = open_page(browser, f"{base_url}/preview#/player/1", touch=True)
    wait_screen(page)
    page.evaluate("""() => {
        const t = document.querySelector('.tab-body');
        const mk = (x, y) => new Touch({identifier: 1, target: t, clientX: x, clientY: y});
        t.dispatchEvent(new TouchEvent('touchstart', {touches: [mk(300, 500)], changedTouches: [mk(300, 500)], bubbles: true}));
        t.dispatchEvent(new TouchEvent('touchend', {touches: [], changedTouches: [mk(120, 510)], bubbles: true}));
    }""")
    page.wait_for_function("location.hash === '#/player/1/matches'", timeout=3000)
    page.context.close()


def test_share_sends_png_to_bot(browser, base_url):
    page = open_page(browser, f"{base_url}/preview#/summary/day")
    wait_screen(page)
    with page.expect_response(lambda r: r.url.endswith("/api/share")) as resp:
        page.locator(".share-btn").click()
    assert resp.value.status == 200
    page.wait_for_selector(".sheet-wrap")
    assert "в чате с ботом" in page.inner_text(".sheet-wrap")
    page.context.close()
