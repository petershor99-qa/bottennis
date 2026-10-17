"""
Короткий бот (v2.167.0): у кого есть приложение, глубокая статистика в боте —
за кнопкой в приложение; без приложения бот полный, как раньше (режим для
тех, кто разворачивает бота без Mini App). Запуск: pytest tests/test_bot_slim.py
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from bot.handlers.profile import _build_stats_screen
from bot.keyboards.inline import main_menu_kb, rematch_kb
from bot.webapp.config import WEBAPP_MORE_TEXT, WEBAPP_RATING_TEXT, slim_bot_for, webapp_link
from tests.conftest import _completed, _player

ADMIN = 1001


@pytest.fixture
def app_on(monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")


@pytest.fixture
def app_off(monkeypatch):
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))


def _buttons(kb):
    return [b for row in kb.inline_keyboard for b in row]


# ── Ссылки в приложение ───────────────────────────────────────────────────────

def test_webapp_link_builds_route_and_params(app_on):
    url = webapp_link(5, "rating", **{"from": 3})
    parsed = urlparse(url)
    assert url.startswith("https://club.example/?")
    assert parse_qs(parsed.query) == {"r": ["rating"], "from": ["3"]}


def test_webapp_link_skips_empty_params(app_on):
    assert webapp_link(5, "rating", **{"from": None}) == "https://club.example/?r=rating"
    assert webapp_link(5) == "https://club.example/"


def test_no_app_no_links(app_off):
    assert webapp_link(5, "rating") is None
    assert slim_bot_for(5) is False


def test_admin_only_audience_slims_only_admin(app_on, monkeypatch):
    monkeypatch.setenv("WEBAPP_AUDIENCE", "admin")
    assert slim_bot_for(ADMIN) is True
    assert slim_bot_for(2002) is False


# ── «Мой рейтинг» ─────────────────────────────────────────────────────────────

async def _seed(db):
    me, rival = _player(ADMIN, "Алиса", 1180.0), _player(2002, "Боб", 1100.0)
    db.add_all([me, rival])
    await db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(6):
        winner = me if i % 3 else rival
        db.add(_completed(me, rival, winner.id, 10.0, now - timedelta(days=i)))
    await db.commit()
    return me


async def test_short_stats_screen_with_app(db, app_on):
    me = await _seed(db)
    text, kb = await _build_stats_screen(db, me)
    assert text.startswith("📊 <b>Мой рейтинг — Алиса</b>")
    assert "За неделю:" in text and "Форма:" in text and "(последние 5)" in text
    assert "в приложении" in text
    first = _buttons(kb)[0]
    assert first.text == WEBAPP_MORE_TEXT
    assert first.web_app.url == f"https://club.example/?r=player%2F{me.id}"
    # глубоких кнопок (радар, история, ачивки…) в коротком режиме нет;
    # «Фото в приложении» — с v2.174.0
    callbacks = {b.callback_data for b in _buttons(kb) if b.callback_data}
    assert callbacks == {"photo_start", "menu_leaderboard", "back_to_menu"}


async def test_full_stats_screen_without_app(db, app_off):
    me = await _seed(db)
    text, kb = await _build_stats_screen(db, me)
    assert "Мой профиль" in text
    callbacks = {b.callback_data for b in _buttons(kb) if b.callback_data}
    assert {"style_radar", "my_achievements", "history_0"} <= callbacks
    assert not any(b.web_app for b in _buttons(kb))


async def test_short_stats_without_matches(db, app_on):
    me = _player(ADMIN, "Новичок")
    db.add(me)
    await db.commit()
    text, kb = await _build_stats_screen(db, me)
    assert "ещё не сыграл" in text
    assert _buttons(kb)[0].web_app is not None


# ── После матча: рейтинг в приложении ─────────────────────────────────────────

def test_result_screen_gets_rating_link(app_on):
    url = webapp_link(ADMIN, "rating", **{"from": 4})
    kb = rematch_kb(7, app_url=url)
    app = [b for b in _buttons(kb) if b.web_app]
    assert [b.text for b in app] == [WEBAPP_RATING_TEXT]
    assert "from=4" in app[0].web_app.url


def test_notification_menu_uses_rating_link_instead_of_plain_open(app_on):
    url = webapp_link(ADMIN, "rating", **{"from": 2})
    kb = main_menu_kb(rematch_opponent_id=7, viewer_telegram_id=ADMIN, app_url=url)
    app = [b for b in _buttons(kb) if b.web_app]
    assert app[0].text == WEBAPP_RATING_TEXT and [b.text for b in app[1:]] == ["📊 Мой рейтинг"]   # v2.203.0


def test_result_screen_without_app_has_no_app_button(app_off):
    kb = rematch_kb(7, app_url=webapp_link(ADMIN, "rating"))
    assert not any(b.web_app for b in _buttons(kb))


def test_rating_link_helper_handles_missing_ranks(app_on):
    from bot.handlers.match_result import _rating_link

    assert _rating_link(ADMIN, None, 1) == "https://club.example/?r=rating"
    assert _rating_link(ADMIN, {1: 3}, 1).endswith("from=3")
