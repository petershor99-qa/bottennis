"""
Пакет 4, релиз 1 (v2.179.0): обновление постоянной клавиатуры у тех, у кого она
старая; «Вызвать на матч» из приложения (бот присылает экран вызова в чат);
«Личные встречи» на уведомлении о вызове; «Матч» после записи результата.
Запуск: pytest tests/test_buttons_challenge.py
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

import bot.webapp.challenge_open as challenge_open
from bot.db.models import Base, Match, MatchStatus, Player, UsageEvent
from bot.handlers.challenge import send_challenge
from bot.keyboards.inline import (
    REPLY_KB_TEXT,
    REPLY_KB_VERSION,
    active_match_kb,
    main_menu_kb,
    rematch_kb,
)
from bot.middleware import ReplyKeyboardMiddleware
from bot.webapp.config import WEBAPP_H2H_TEXT, WEBAPP_MATCH_TEXT, WEBAPP_RATING_TEXT
from bot.webapp.server import USAGE_KEY, create_app
from tests.conftest import _callback, _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp"


@pytest.fixture
def app_on(monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")


@pytest.fixture(autouse=True)
def _no_cooldown():
    challenge_open._last_open.clear()


# ── Постоянная клавиатура ─────────────────────────────────────────────────────

def _private_message(user_id: int) -> Message:
    return Message.model_validate({
        "message_id": 1, "date": 0, "text": "11:7",
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": "A"},
    })


async def _run_middleware(db, event, bot):
    handler = AsyncMock(return_value="done")
    result = await ReplyKeyboardMiddleware()(handler, event, {"session": db, "bot": bot})
    assert result == "done"
    return handler


async def test_old_keyboard_is_replaced_once(db):
    p = _player(1, "Alice")
    db.add(p)
    await db.commit()
    bot = AsyncMock()
    await _run_middleware(db, _private_message(1), bot)
    args, kwargs = bot.send_message.call_args
    assert args == (1, REPLY_KB_TEXT)
    assert kwargs["disable_notification"] is True
    assert kwargs["reply_markup"].is_persistent is True
    assert p.reply_kb_version == REPLY_KB_VERSION
    await _run_middleware(db, _private_message(1), bot)          # второй раз — не присылает
    assert bot.send_message.await_count == 1


async def test_current_keyboard_and_strangers_untouched(db):
    p = _player(1, "Alice")
    p.reply_kb_version = REPLY_KB_VERSION
    db.add(p)
    await db.commit()
    bot = AsyncMock()
    await _run_middleware(db, _private_message(1), bot)          # уже новая
    await _run_middleware(db, _private_message(99), bot)         # не игрок
    bot.send_message.assert_not_called()


async def test_keyboard_refresh_after_callback_and_not_in_groups(db):
    db.add(_player(1, "Alice"))
    await db.commit()
    bot = AsyncMock()
    group = SimpleNamespace(from_user=SimpleNamespace(id=1), message=SimpleNamespace(chat=SimpleNamespace(type="group")))
    await _run_middleware(db, group, bot)
    bot.send_message.assert_not_called()
    cb = SimpleNamespace(from_user=SimpleNamespace(id=1), message=SimpleNamespace(chat=SimpleNamespace(type="private")))
    await _run_middleware(db, cb, bot)
    assert bot.send_message.await_count == 1


async def test_failed_send_does_not_break_handler_and_retries_later(db):
    p = _player(1, "Alice")
    db.add(p)
    await db.commit()
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("telegram down")
    await _run_middleware(db, _private_message(1), bot)
    assert p.reply_kb_version == 0                               # повторит при следующем действии


async def test_start_records_keyboard_version(db):
    from bot.handlers.start import cmd_start
    from tests.conftest import _state
    from tests.test_handlers import _message

    db.add(_player(1, "Alice"))
    await db.commit()
    await cmd_start(_message(1, "/start"), SimpleNamespace(args=None), db, _state(1), AsyncMock())
    player = (await db.execute(select(Player).where(Player.telegram_id == 1))).scalar_one()
    assert player.reply_kb_version == REPLY_KB_VERSION


async def test_migration_adds_keyboard_version(monkeypatch, tmp_path):
    import bot.db.database as database

    sync = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(sync)
    with sync.begin() as conn:
        conn.execute(text("ALTER TABLE players DROP COLUMN reply_kb_version"))
    assert "reply_kb_version" not in [c["name"] for c in inspect(sync).get_columns("players")]
    sync.dispose()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    monkeypatch.setattr(database, "engine", engine)
    await database._migrate_db()
    async with engine.begin() as conn:
        cols = [r[1] for r in (await conn.execute(text("PRAGMA table_info(players)"))).all()]
    await engine.dispose()
    assert "reply_kb_version" in cols


# ── Кнопки приложения в боте ──────────────────────────────────────────────────

def _buttons(kb):
    return [[b.text for b in row] for row in kb.inline_keyboard]


def test_active_match_kb_h2h_button_first():
    assert _buttons(active_match_kb(5, h2h_url="https://x/?r=h2h%2F2"))[0] == [WEBAPP_H2H_TEXT]
    assert WEBAPP_H2H_TEXT not in sum(_buttons(active_match_kb(5)), [])


def test_after_match_rating_and_match_in_one_row():
    rows = _buttons(rematch_kb(2, app_url="https://x/a", match_url="https://x/m"))
    assert [WEBAPP_RATING_TEXT, WEBAPP_MATCH_TEXT] in rows
    rows = _buttons(main_menu_kb(rematch_opponent_id=2, app_url="https://x/a", match_url="https://x/m"))
    assert [WEBAPP_RATING_TEXT, WEBAPP_MATCH_TEXT] in rows
    assert [WEBAPP_RATING_TEXT] in _buttons(rematch_kb(2, app_url="https://x/a"))


async def test_challenge_notification_has_h2h_for_both(db, app_on):
    a, b = _player(ADMIN, "Alice"), _player(2002, "Bob")
    db.add_all([a, b])
    await db.commit()
    cb, bot = _callback(ADMIN, f"challenge_{b.id}"), AsyncMock()
    await send_challenge(cb, db, bot)
    to_opponent = bot.send_message.await_args_list[0]
    kb = to_opponent.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text == WEBAPP_H2H_TEXT
    assert "h2h%2F" + str(a.id) in kb.inline_keyboard[0][0].web_app.url
    own = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert "h2h%2F" + str(b.id) in own.inline_keyboard[0][0].web_app.url


async def test_challenge_notification_without_app_has_no_h2h(db, monkeypatch):
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    a, b = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([a, b])
    await db.commit()
    cb, bot = _callback(1, f"challenge_{b.id}"), AsyncMock()
    await send_challenge(cb, db, bot)
    kb = bot.send_message.await_args_list[0].kwargs["reply_markup"]
    assert WEBAPP_H2H_TEXT not in sum(_buttons(kb), [])


# ── «Вызвать на матч» из приложения ───────────────────────────────────────────

async def _pair(db):
    a, b = _player(ADMIN, "Alice"), _player(2002, "Bob <b>")
    db.add_all([a, b])
    await db.commit()
    return a, b


async def test_open_with_opponent_sends_confirmation(db):
    a, b = await _pair(db)
    bot = AsyncMock()
    assert await challenge_open.open_challenge(bot, db, a, b.id) == "ok"
    args, kwargs = bot.send_message.call_args
    assert args[0] == ADMIN and "Bob &lt;b&gt;" in args[1]
    assert kwargs["disable_notification"] is True
    callbacks = [btn.callback_data for row in kwargs["reply_markup"].inline_keyboard for btn in row]
    assert callbacks == [f"challenge_{b.id}", "menu_play"]


async def test_open_without_opponent_sends_challenge_screen(db):
    a, b = await _pair(db)
    bot = AsyncMock()
    assert await challenge_open.open_challenge(bot, db, a, None) == "ok"
    callbacks = [btn.callback_data for row in bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard for btn in row]
    assert f"challenge_{b.id}" in callbacks


async def test_open_when_busy_shows_active_match(db):
    a, b = await _pair(db)
    db.add(Match(challenger_id=a.id, challenged_id=b.id, status=MatchStatus.accepted))
    await db.commit()
    bot = AsyncMock()
    assert await challenge_open.open_challenge(bot, db, a, b.id) == "ok"
    assert challenge_open.CONFIRM_TEXT.split("<b>")[0] not in bot.send_message.call_args.args[1]


async def test_open_rejects_self_unknown_and_double_tap(db):
    a, b = await _pair(db)
    bot = AsyncMock()
    assert await challenge_open.open_challenge(bot, db, a, a.id) == "bad"
    assert await challenge_open.open_challenge(bot, db, a, 99999) == "bad"
    assert await challenge_open.open_challenge(bot, db, a, b.id) == "ok"
    assert await challenge_open.open_challenge(bot, db, a, b.id) == "wait"
    assert bot.send_message.await_count == 1


async def test_open_reports_telegram_failure(db):
    a, b = await _pair(db)
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("blocked")
    assert await challenge_open.open_challenge(bot, db, a, b.id) == "fail"


@pytest.fixture
async def client(db_factory, app_on):
    async with db_factory() as s:
        a, b = await _pair(s)
        ids = (a.id, b.id)
    bot = AsyncMock()
    async with TestClient(TestServer(create_app(TOKEN, db_factory, bot))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        c.bot, c.ids, c.factory = bot, ids, db_factory
        yield c


async def test_route_opens_challenge_and_counts_it(client):
    resp = await client.post("/api/challenge/open", json={"opponent_id": client.ids[1]}, headers=client.auth)
    assert resp.status == 200 and (await resp.json()) == {"ok": True}
    assert client.bot.send_message.await_count == 1
    await client.app[USAGE_KEY].drain()
    async with client.factory() as s:
        actions = (await s.execute(select(UsageEvent.action))).scalars().all()
    assert actions == ["app_challenge_open"]


@pytest.mark.parametrize("body", [{"opponent_id": "2"}, {"opponent_id": True}, {"opponent_id": 1.5}])
async def test_route_rejects_bad_opponent(client, body):
    resp = await client.post("/api/challenge/open", json=body, headers=client.auth)
    assert resp.status == 400
    client.bot.send_message.assert_not_called()


async def test_route_double_tap_is_429(client):
    await client.post("/api/challenge/open", json={}, headers=client.auth)
    resp = await client.post("/api/challenge/open", json={}, headers=client.auth)
    assert resp.status == 429


async def test_route_without_bot_is_404(db_factory, app_on):
    async with db_factory() as s:
        await _pair(s)
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        resp = await c.post("/api/challenge/open", json={}, headers={"Authorization": "tma " + _init_data(ADMIN)})
        assert resp.status == 404


def test_app_has_challenge_buttons_and_closes_itself():
    src = {p.name: p.read_text(encoding="utf-8") for p in (STATIC / "src" / "app").glob("*.js")}
    assert "c.appendChild(challengeButton(null));" in src["10-home.js"]
    assert "cols.main.appendChild(challengeButton(playerId))" in src["12-profile.js"]
    assert "challengeButton(d.them.id)" in src["13-matches.js"]
    home = src["10-home.js"]
    assert 'post("challenge/open"' in home and "web_app_close" in home and "tg.close()" in home
