"""
v2.185.0: исправления по отзыву владельца — «Поставить фото» (бот присылает
просьбу в чат), штамп над победителем, отступ под плитками клуба, «Реванш»
только тем, кому не отыгрался, метки в ленте, нажатие в двух местах.
Запуск: pytest tests/test_v2185.py
"""
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp.test_utils import TestClient, TestServer

import bot.handlers.photo as photo
import bot.webapp.api_return as ret
from bot.handlers.photo import PHOTO_ASK_TEXT, ask_photo_from_app
from bot.states.states import PhotoStates
from bot.webapp.server import create_app
from tests.conftest import _player
from tests.test_come_back import MONDAY, _club, _m
from tests.test_webapp import ADMIN, TOKEN, _init_data

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_cooldown():
    photo._last_app_ask.clear()


def _bot():
    bot = AsyncMock()
    bot.id = 42
    return bot


# ── «Поставить фото» из приложения ───────────────────────────────────────────

async def test_photo_from_app_sends_ask_and_waits_for_photo():
    storage, bot = MemoryStorage(), _bot()
    p = _player(ADMIN, "Алиса")
    assert await ask_photo_from_app(bot, storage, p) == "ok"
    bot.send_message.assert_awaited_once()
    args, kwargs = bot.send_message.await_args
    assert args == (ADMIN, PHOTO_ASK_TEXT) and kwargs["disable_notification"] is True
    state = FSMContext(storage, StorageKey(bot_id=42, chat_id=ADMIN, user_id=ADMIN))
    assert await state.get_state() == PhotoStates.waiting.state


async def test_photo_from_app_double_tap_and_failure():
    bot = _bot()
    p = _player(ADMIN, "Алиса")
    assert await ask_photo_from_app(bot, None, p) == "ok"
    assert await ask_photo_from_app(bot, None, p) == "wait"
    assert bot.send_message.await_count == 1
    photo._last_app_ask.clear()
    bot.send_message.side_effect = RuntimeError("blocked")
    assert await ask_photo_from_app(bot, MemoryStorage(), p) == "fail"


@pytest.fixture
def app_on(monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")


async def test_photo_open_route(db_factory, app_on):
    async with db_factory() as s:
        s.add(_player(ADMIN, "Алиса"))
        await s.commit()
    bot, storage = _bot(), MemoryStorage()
    auth = {"Authorization": "tma " + _init_data(ADMIN)}
    async with TestClient(TestServer(create_app(TOKEN, db_factory, bot, storage))) as c:
        resp = await c.post("/api/photo/open", json={}, headers=auth)
        assert resp.status == 200 and (await resp.json()) == {"ok": True}
        assert (await c.post("/api/photo/open", json={}, headers=auth)).status == 429
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        assert (await c.post("/api/photo/open", json={}, headers=auth)).status == 404


def test_app_asks_bot_for_photo_instead_of_link():
    profile = (SRC / "12-profile.js").read_text(encoding="utf-8")
    assert 'post("photo/open"' in profile and "closeApp()" in profile
    assert "openTelegramLink(url)" not in profile


def test_main_passes_fsm_storage_to_webapp():
    assert "start_webapp(token, async_session, bot, dp.storage)" in (ROOT / "main.py").read_text(encoding="utf-8")


# ── Реванш ────────────────────────────────────────────────────────────────────

async def test_revenge_skips_rival_already_beaten(db):
    a, b, c = await _club(db)
    db.add_all([
        _m(c, a, c.id, [(11, 5)], MONDAY - timedelta(days=3)),   # проиграла Вике — не отыгралась
        _m(b, a, b.id, [(11, 9)], MONDAY - timedelta(days=2)),   # проиграла Бобу…
        _m(a, b, a.id, [(11, 7)], MONDAY - timedelta(days=1)),   # …и отыгралась
    ])
    await db.commit()
    goals = {x["key"]: x for x in (await ret.goals_payload(db, a))["goals"]}
    assert goals["revenge"]["title"] == "Реванш: Вика" and goals["revenge"]["player_id"] == c.id
    assert goals["revenge"]["note"] == "Выиграй у того, кому ещё не отыгрался"


async def test_revenge_all_avenged_or_too_old_gives_wins_goal(db):
    a, b, c = await _club(db)
    db.add_all([
        _m(c, a, c.id, [(11, 5)], MONDAY - timedelta(weeks=ret.REVENGE_WEEKS, days=1)),  # слишком давно
        _m(b, a, b.id, [(11, 9)], MONDAY - timedelta(days=2)),
        _m(a, b, a.id, [(11, 7)], MONDAY - timedelta(days=1)),
    ])
    await db.commit()
    goals = {x["key"]: x for x in (await ret.goals_payload(db, a))["goals"]}
    assert "revenge" not in goals and "wins" in goals


# ── Вид ───────────────────────────────────────────────────────────────────────

def test_layout_fixes_in_css():
    assert ".club-group { margin-bottom: 16px; }" in CSS
    assert ".stamp-a .mood-stamp" in CSS and "rotate(var(--rot))" in CSS
    feed_day = CSS.split(".feed-day {", 1)[1].split("}", 1)[0]
    assert "uppercase" not in feed_day
    name = CSS.split(".versus-name {", 1)[1].split("}", 1)[0]
    assert "-webkit-line-clamp: 2" in name and "nowrap" not in name
    assert "touch-action: manipulation" in CSS


def test_stamp_side_and_feed_tags_in_js():
    match = (SRC / "20-match.js").read_text(encoding="utf-8")
    assert '" stamp-" + d.winner' in match
    home = (SRC / "10-home.js").read_text(encoding="utf-8")
    assert "feedBlock(d.feed, d.feed_more, d.featured)" in home and "function feedTags" in home
    core = (SRC / "01-core.js").read_text(encoding="utf-8")
    assert "function tapGuard" in core and "navLockUntil" in core
