"""
v2.203.0: с приложением бот — вызов и рейтинг (решение владельца по /usage):
«Мой рейтинг» открывает профиль в приложении, «С кем сыграть?» и «Справка» из
меню убраны, третья кнопка снизу — «📱 Приложение»; /start одним сообщением;
кнопки «Цели недели» и «Твой октябрь» в сводках.
Запуск: pytest tests/test_v2203.py
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.keyboards.inline import REPLY_KB_TEXT, REPLY_KB_VERSION, main_menu_kb, main_reply_kb
from bot.services.digest_delivery import deliver_digest
from bot.utils import REPLY_KB_APP, REPLY_KB_PROFILE
from tests.conftest import _player, _state

ROOT = Path(__file__).resolve().parent.parent
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


def _texts(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def test_slim_menu_with_app(app_on):
    kb = main_menu_kb(viewer_telegram_id=ADMIN)
    texts = _texts(kb)
    assert "🎯 С кем сыграть?" not in texts and "❓ Справка" not in texts
    me = next(b for row in kb.inline_keyboard for b in row if b.text == "📊 Мой рейтинг")
    assert me.web_app.url == "https://club.example/?r=me" and me.callback_data is None
    assert "🏆 Рейтинг клуба" in texts


def test_full_menu_without_app(app_off):
    texts = _texts(main_menu_kb(viewer_telegram_id=ADMIN))
    assert "🎯 С кем сыграть?" in texts and "❓ Справка" in texts and "📊 Мой рейтинг" in texts


def test_reply_keyboard_third_button(app_on):
    third = main_reply_kb(ADMIN).keyboard[0][2]
    assert third.text == REPLY_KB_APP and third.web_app.url == "https://club.example/"
    assert main_reply_kb().keyboard[0][2].text == REPLY_KB_PROFILE           # без зрителя — как раньше
    assert REPLY_KB_VERSION == 3


def test_reply_keyboard_without_app(app_off):
    third = main_reply_kb(ADMIN).keyboard[0][2]
    assert third.text == REPLY_KB_PROFILE and third.web_app is None


async def test_start_sends_keyboard_only_when_outdated(db, app_on):
    from bot.handlers.start import cmd_start
    from tests.test_handlers import _message

    p = _player(1, "Alice")
    p.reply_kb_version = REPLY_KB_VERSION
    db.add(p)
    await db.commit()
    msg = _message(1, "/start")
    await cmd_start(msg, SimpleNamespace(args=None), db, _state(1), AsyncMock())
    assert all(c.args[0] != REPLY_KB_TEXT for c in msg.answer.call_args_list)   # одно сообщение — меню
    p.reply_kb_version = 2
    await db.commit()
    msg = _message(1, "/start")
    await cmd_start(msg, SimpleNamespace(args=None), db, _state(1), AsyncMock())
    assert any(c.args[0] == REPLY_KB_TEXT for c in msg.answer.call_args_list)
    assert p.reply_kb_version == REPLY_KB_VERSION


async def test_digest_extra_buttons(app_on, monkeypatch):
    sent = []

    async def fake_send(bot, chat_id, text, **kw):
        sent.append(kw.get("reply_markup"))

    monkeypatch.setattr("bot.services.digest_delivery.safe_send", fake_send)
    player = SimpleNamespace(telegram_id=ADMIN)
    await deliver_digest(None, player, "month", "full", "short", extra=[("📅 Твой октябрь", "month/202610")])
    rows = sent[0].inline_keyboard
    assert rows[1][0].text == "📅 Твой октябрь" and rows[1][0].web_app.url.endswith("?r=month%2F202610")


def test_digest_buttons_wired_in_scheduler():
    src = (ROOT / "bot" / "scheduler.py").read_text(encoding="utf-8")
    assert 'WEEK_GOALS_BUTTON = "🎯 Цели недели"' in src and "extra=[(WEEK_GOALS_BUTTON, \"\")]" in src
    assert 'f"📅 Твой {MONTH_NAMES_NOM[month_start_msk.month]}"' in src and "if p_matches else None" in src


def test_fullscreen_mark_set_on_launch_and_safe_area():
    # v2.203.1: открылось сразу во весь экран — отметка и отступ под камерой всё равно ставятся
    src = (ROOT / "webapp" / "src" / "app" / "25-new.js").read_text(encoding="utf-8")
    setup = src[src.index("function setupFullscreen()"):]
    assert setup.index("syncFullscreenClass();") < setup.index("if (!fullscreenAvailable()) return;")
    routes = (ROOT / "webapp" / "src" / "app" / "90-routes.js").read_text(encoding="utf-8")
    assert 'tg.onEvent("safeAreaChanged", syncFullscreenClass);' in routes
    assert "var target = relaunchRoute(location.hash, location.search);" in routes
