"""
v2.174.0: одна реакция игрока на матч, «Матч, который обсуждали» в итогах дня,
своё фото для приложения через бота (/photo), «ничьи» с пробелом в приложении,
без рекорда «Самый быстрый матч».
Запуск: pytest tests/test_v2174.py
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

import bot.scheduler as scheduler
from bot.db.models import Base, MatchReaction, Player
from bot.handlers import photo as photo_handlers
from bot.keyboards.inline import short_stats_kb
from bot.states.states import PhotoStates
from bot.utils import msk_day_start
from bot.webapp import api
from bot.webapp.photos import PhotoCache, photo_etag, pick_photo_size
from bot.webapp.reactions import most_discussed_line, reactions_for, toggle_reaction
from bot.webapp.server import create_app
from bot.webapp.text import plain
from tests.conftest import _callback, _completed, _player, _state, freeze_clock
from tests.test_webapp import ADMIN, TOKEN, _init_data

# «Сегодня» с запасом от полуночи по МСК (соглашение после v2.170.1)
TODAY = msk_day_start() + timedelta(hours=1)


@pytest.fixture(autouse=True)
def _clock_after_today(monkeypatch):
    """Часы кода под тестом стоят на TODAY + 2 ч: итоги дня считают «сегодня» по
    тем же суткам, что и данные теста, даже если прогон пересёк полночь МСК."""
    freeze_clock(monkeypatch, TODAY + timedelta(hours=2))


async def _pair(db):
    a, b, c = _player(ADMIN, "Алиса"), _player(2002, "Боб"), _player(3003, "Вика")
    db.add_all([a, b, c])
    await db.flush()
    m = _completed(a, b, a.id, 5.0, TODAY)
    db.add(m)
    await db.commit()
    return a, b, c, m


def _mine(items):
    return [r["kind"] for r in items if r["mine"]]


# ── Одна реакция на матч ──────────────────────────────────────────────────────

async def test_other_reaction_replaces_previous(db):
    a, _, _, m = await _pair(db)
    assert _mine(await toggle_reaction(db, m.id, a, "fire")) == ["fire"]
    result = await toggle_reaction(db, m.id, a, "poo")
    assert _mine(result) == ["poo"]
    assert next(r for r in result if r["kind"] == "fire")["count"] == 0
    rows = (await db.execute(select(MatchReaction))).scalars().all()
    assert [(r.player_id, r.kind) for r in rows] == [(a.id, "poo")]


async def test_same_reaction_again_removes_it(db):
    a, _, _, m = await _pair(db)
    await toggle_reaction(db, m.id, a, "like")
    assert _mine(await toggle_reaction(db, m.id, a, "like")) == []


async def test_players_keep_their_own_single_reaction(db):
    a, b, c, m = await _pair(db)
    await toggle_reaction(db, m.id, a, "fire")
    await toggle_reaction(db, m.id, b, "fire")
    await toggle_reaction(db, m.id, c, "lol")
    await toggle_reaction(db, m.id, b, "poo")  # Боб передумал
    counts = {r["kind"]: r["count"] for r in (await reactions_for(db, [m.id], a.id))[m.id]}
    assert counts == {"fire": 1, "like": 0, "lol": 1, "poo": 1}


async def test_unique_index_blocks_second_reaction_of_player(db):
    a, _, _, m = await _pair(db)
    db.add(MatchReaction(match_id=m.id, player_id=a.id, kind="fire"))
    await db.commit()
    db.add(MatchReaction(match_id=m.id, player_id=a.id, kind="lol"))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_migration_keeps_latest_reaction_and_adds_index(monkeypatch):
    """Старая база: ограничение было (match, player, kind) — у игрока могли быть
    несколько реакций. Миграция оставляет последнюю и ставит уникальный индекс."""
    import bot.db.database as database

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE match_reactions"))
        await conn.execute(text(
            "CREATE TABLE match_reactions (id INTEGER PRIMARY KEY, match_id INTEGER, "
            "player_id INTEGER, kind VARCHAR, created_at DATETIME, "
            "CONSTRAINT uq_match_reaction UNIQUE (match_id, player_id, kind))"
        ))
        await conn.execute(text(
            "INSERT INTO match_reactions (id, match_id, player_id, kind) VALUES "
            "(1, 1, 1, 'fire'), (2, 1, 1, 'poo'), (3, 1, 2, 'like')"
        ))
    monkeypatch.setattr(database, "engine", engine)
    await database._migrate_db()
    await database._migrate_db()  # повторный запуск безопасен
    async with engine.begin() as conn:
        rows = (await conn.execute(text("SELECT id, kind FROM match_reactions ORDER BY id"))).all()
        assert rows == [(2, "poo"), (3, "like")]
        with pytest.raises(IntegrityError):
            await conn.execute(text(
                "INSERT INTO match_reactions (match_id, player_id, kind) VALUES (1, 1, 'lol')"
            ))
    async with engine.begin() as conn:
        cols = [r[1] for r in (await conn.execute(text("PRAGMA table_info(players)"))).all()]
    assert "photo_file_id" in cols
    await engine.dispose()


# ── «Матч, который обсуждали» ─────────────────────────────────────────────────

async def test_discussed_line_needs_two_reactions(db):
    a, b, _, m = await _pair(db)
    names = {a.id: "Алиса", b.id: "Боб"}
    assert await most_discussed_line(db, [m], names) is None
    await toggle_reaction(db, m.id, a, "fire")
    assert await most_discussed_line(db, [m], names) is None


async def test_discussed_line_format_and_app_wording(db):
    a, b, c, m = await _pair(db)
    m.sets_data = [{"w": 11, "l": 7}, {"w": 9, "l": 11}, {"w": 11, "l": 5}]
    await db.commit()
    for p, kind in ((a, "fire"), (b, "fire"), (c, "fire")):
        await toggle_reaction(db, m.id, p, kind)
    d = _player(4004, "Гоша")
    db.add(d)
    await db.commit()
    await toggle_reaction(db, m.id, d, "poo")
    line = await most_discussed_line(db, [m], {a.id: "Алиса", b.id: "Боб"})
    assert line == "💬 <b>Матч, который обсуждали:</b> Алиса — Боб (2:1) · 🔥 3 · 💩 1"
    assert plain(line) == "Матч, который обсуждали: Алиса — Боб (2:1) · огонь 3 · какашка 1"


async def test_discussed_line_winner_first_and_tie_goes_to_later_match(db):
    a, b, c, early = await _pair(db)
    late = _completed(a, b, b.id, 5.0, TODAY + timedelta(minutes=30))  # победил Боб
    db.add(late)
    await db.commit()
    for p in (a, c):
        await toggle_reaction(db, early.id, p, "like")
        await toggle_reaction(db, late.id, p, "lol")
    line = await most_discussed_line(db, [early, late], {a.id: "Алиса", b.id: "Боб"})
    assert "Боб — Алиса (1:0)" in line and "😂 2" in line


async def test_daily_summary_shows_discussed_match(db_factory, monkeypatch):
    monkeypatch.setattr(scheduler, "async_session", db_factory)
    async with db_factory() as s:
        a, b, c, m = await _pair(s)
        await toggle_reaction(s, m.id, b, "fire")
        await toggle_reaction(s, m.id, c, "like")
    bot = SimpleNamespace(send_message=AsyncMock())
    await scheduler.send_daily_summary(bot)
    text_ = bot.send_message.call_args_list[0].args[1]
    assert "💬 <b>Матч, который обсуждали:</b> Алиса — Боб (1:0) · 🔥 1 · 👍 1" in text_


async def test_daily_summary_survives_reaction_failure(db_factory, monkeypatch):
    monkeypatch.setattr(scheduler, "async_session", db_factory)
    monkeypatch.setattr(scheduler, "most_discussed_line", AsyncMock(side_effect=RuntimeError("сбой")))
    async with db_factory() as s:
        await _pair(s)
    bot = SimpleNamespace(send_message=AsyncMock())
    await scheduler.send_daily_summary(bot)
    assert "Все матчи" in bot.send_message.call_args_list[0].args[1]


# ── «ничьи» в приложении ──────────────────────────────────────────────────────

@pytest.mark.parametrize("line, expected", [
    ("Счёт: 7–3–1🤝", "Счёт: 7–3–1 ничьи"),
    ("Матчей: 10 (+2 🤝)", "Матчей: 10 (+2 ничьи)"),
    ("⚔️ Чаще всего: <b>Боб</b> (12 матчей, 5–6 🤝1)", "Чаще всего: Боб (12 матчей, 5–6 ничьи 1)"),
    ("🤝 Больше всего ничьих — Боб: 3", "Больше всего ничьих — Боб: 3"),
])
def test_draw_word_has_spaces(line, expected):
    assert plain(line) == expected


# ── Своё фото ─────────────────────────────────────────────────────────────────

class _Bot:
    def __init__(self, profile=True, custom_ok=True):
        self.profile, self.custom_ok = profile, custom_ok
        self.downloads: list[str] = []

    async def get_user_profile_photos(self, user_id, limit):
        sizes = [SimpleNamespace(width=320, file_id="tg-photo")]
        return SimpleNamespace(photos=[sizes] if self.profile else [])

    async def download(self, file_id, destination):
        self.downloads.append(file_id)
        if file_id.startswith("own") and not self.custom_ok:
            raise RuntimeError("file_id устарел")
        destination.write(b"JPEG-" + file_id.encode())
        return destination

    async def me(self):
        return SimpleNamespace(username="club_bot")


async def test_own_photo_wins_over_telegram_and_falls_back():
    cache = PhotoCache()
    assert await cache.get(_Bot(), 1, "own-1") == b"JPEG-own-1"
    assert await cache.get(_Bot(), 1, None) == b"JPEG-tg-photo"
    assert await PhotoCache().get(_Bot(custom_ok=False), 1, "own-1") == b"JPEG-tg-photo"
    assert await PhotoCache().get(_Bot(profile=False), 1, "own-2") == b"JPEG-own-2"


async def test_new_file_id_is_not_served_from_old_cache():
    cache, bot = PhotoCache(), _Bot()
    await cache.get(bot, 1, "own-1")
    assert await cache.get(bot, 1, "own-2") == b"JPEG-own-2"


def test_pick_photo_size_prefers_small_enough():
    sizes = [SimpleNamespace(width=90, file_id="a"), SimpleNamespace(width=320, file_id="b"),
             SimpleNamespace(width=1280, file_id="c")]
    assert pick_photo_size(sizes) == "b"
    assert pick_photo_size([SimpleNamespace(width=90, file_id="a")]) == "a"


@pytest.fixture
async def photo_client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")
    async with db_factory() as s:
        a, b, _, _ = await _pair(s)
        a.photo_file_id = "own-a"
        await s.commit()
        ids = (a.id, b.id)
    async with TestClient(TestServer(create_app(TOKEN, db_factory, _Bot()))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        c.ids = ids
        yield c


async def test_photo_endpoint_serves_own_photo_with_etag(photo_client):
    a_id, _ = photo_client.ids
    resp = await photo_client.get(f"/api/photo/{a_id}", headers=photo_client.auth)
    assert resp.status == 200 and await resp.read() == b"JPEG-own-a"
    assert resp.headers["ETag"] == photo_etag(b"JPEG-own-a")
    assert "no-cache" in resp.headers["Cache-Control"]
    again = await photo_client.get(
        f"/api/photo/{a_id}", headers={**photo_client.auth, "If-None-Match": resp.headers["ETag"]},
    )
    assert again.status == 304 and await again.read() == b""


async def test_photo_link_only_in_own_profile(photo_client):
    a_id, b_id = photo_client.ids
    own = await (await photo_client.get(f"/api/player/{a_id}", headers=photo_client.auth)).json()
    assert own["head"]["photo_link"] == "https://t.me/club_bot?start=photo"
    other = await (await photo_client.get(f"/api/player/{b_id}", headers=photo_client.auth)).json()
    assert "photo_link" not in other["head"]


async def test_player_payload_without_server_has_no_link(db):
    a, _, _, _ = await _pair(db)
    assert "photo_link" not in (await api.player_payload(db, a, a))["head"]


def _message(user_id: int, **fields):
    msg = AsyncMock()
    msg.from_user = SimpleNamespace(id=user_id, username=None, full_name="X")
    msg.chat = SimpleNamespace(id=user_id)
    msg.photo = fields.get("photo")
    msg.text = fields.get("text")
    msg.answer = AsyncMock()
    return msg


async def test_cmd_photo_asks_and_offers_remove_only_when_set(db):
    a, _, _, _ = await _pair(db)
    state = _state(ADMIN)
    msg = _message(ADMIN)
    await photo_handlers.cmd_photo(msg, db, state)
    args, kwargs = msg.answer.call_args
    assert args[0] == photo_handlers.PHOTO_ASK_TEXT
    assert [r[0].text for r in kwargs["reply_markup"].inline_keyboard] == ["Отмена"]
    assert await state.get_state() == PhotoStates.waiting.state
    a.photo_file_id = "own"
    await db.commit()
    await photo_handlers.cmd_photo(msg, db, state)
    assert [r[0].text for r in msg.answer.call_args.kwargs["reply_markup"].inline_keyboard] == ["Убрать фото", "Отмена"]


async def test_cmd_photo_for_stranger(db):
    msg = _message(999)
    await photo_handlers.cmd_photo(msg, db, _state(999))
    assert msg.answer.call_args.args[0] == "Сначала напиши /start"


async def test_got_photo_saves_file_id_and_offers_profile(db, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")
    a, _, _, _ = await _pair(db)
    state = _state(ADMIN)
    await state.set_state(PhotoStates.waiting)
    sizes = [SimpleNamespace(width=90, file_id="s"), SimpleNamespace(width=320, file_id="m")]
    msg = _message(ADMIN, photo=sizes)
    await photo_handlers.got_photo(msg, db, state)
    await db.refresh(a)
    assert a.photo_file_id == "m"
    assert await state.get_state() is None
    args, kwargs = msg.answer.call_args
    assert args[0] == "Готово, фото стоит в профиле приложения."
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "📱 Открыть профиль" and f"player%2F{a.id}" in button.web_app.url


async def test_not_a_photo_asks_again():
    msg = _message(ADMIN, text="привет")
    await photo_handlers.not_a_photo(msg)
    assert msg.answer.call_args.args[0] == (
        "Нужна именно фотография, не файл и не стикер. Пришли ещё раз или нажми «Отмена»."
    )


async def test_score_while_waiting_goes_to_score_entry(db, monkeypatch):
    called = AsyncMock()
    import bot.handlers.match_result as match_result
    monkeypatch.setattr(match_result, "handle_direct_score", called)
    state = _state(ADMIN)
    await state.set_state(PhotoStates.waiting)
    msg = _message(ADMIN, text="11:7 11:9")
    await photo_handlers.score_while_waiting(msg, db, state)
    assert await state.get_state() is None
    called.assert_awaited_once()


async def test_remove_and_cancel(db):
    a, _, _, _ = await _pair(db)
    a.photo_file_id = "own"
    await db.commit()
    state = _state(ADMIN)
    await state.set_state(PhotoStates.waiting)
    cb = _callback(ADMIN, "photo_remove")
    await photo_handlers.photo_remove(cb, db, state)
    await db.refresh(a)
    assert a.photo_file_id is None and await state.get_state() is None
    assert cb.message.edit_text.call_args.args[0] == "Фото убрано. В приложении снова фото из Telegram или буква."
    await state.set_state(PhotoStates.waiting)
    cancel = _callback(ADMIN, "photo_cancel")
    await photo_handlers.photo_cancel(cancel, state)
    assert await state.get_state() is None


async def test_start_with_photo_payload_opens_photo_flow(db):
    from bot.handlers.start import cmd_start

    await _pair(db)
    msg = _message(ADMIN)
    state = _state(ADMIN)
    await cmd_start(msg, SimpleNamespace(args="photo"), db, state, AsyncMock())
    assert msg.answer.call_args.args[0] == photo_handlers.PHOTO_ASK_TEXT
    assert await state.get_state() == PhotoStates.waiting.state


def test_short_stats_has_photo_button():
    rows = short_stats_kb("https://club.example/").inline_keyboard
    assert any(b.text == "📷 Фото в приложении" and b.callback_data == "photo_start" for r in rows for b in r)


def test_player_model_has_photo_column():
    assert "photo_file_id" in Player.__table__.columns

