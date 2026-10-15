"""
v2.190.0: жесты — «назад» свайпом от левого края, долгое нажатие на игрока
(быстрое меню), нажатие на активную вкладку наверху обновляет экран.
Логика — в Node (tests/js), поведение — в браузере (tests/e2e/test_app_e2e.py).
Запуск: pytest tests/test_v2190.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")


def test_player_links_marked_for_long_press():
    core = (SRC / "01-core.js").read_text(encoding="utf-8")
    assert 'node.setAttribute("data-route", route);' in core
    # после долгого нажатия глотается ровно следующий клик, новое касание сбрасывает
    assert "if (swallowClick) { swallowClick = false;" in core
    assert "swallowClick = false; noteTouches(e);" in core


def test_gestures_present():
    g = (SRC / "22-gestures.js").read_text(encoding="utf-8")
    assert "function playerSheet(id)" in g and "challengeButton(id)" in g
    assert 'document.addEventListener("contextmenu"' in g
    assert "isRootRoute(currentRoute(), viewerId)" in g and "history.back();" in g
    assert "touch.swipe = false" in g          # от края — не листание разделов профиля


def test_active_tab_refreshes_at_top():
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert 'b.classList.add("is-refreshing");' in routes and "refresh().then(" in routes


def test_styles_and_reduced_motion():
    for sel in (".edge-back {", ".edge-back.is-ready", ".tap.is-pressing", ".quick-actions", ".tab.is-refreshing .ico"):
        assert sel in CSS
    assert "@keyframes tab-spin" in CSS
    tail = CSS[CSS.index("/* Жесты 2 (v2.190.0)"):]
    assert "prefers-reduced-motion: reduce" in tail
