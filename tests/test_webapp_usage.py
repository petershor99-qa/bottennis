"""
Счётчик экранов Mini App (v2.166.0): POST /api/track пишет событие app_<экран>
в usage_events фоном, время загрузки — только в лог. Запуск:
pytest tests/test_webapp_usage.py
"""
import logging

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from bot.db.models import UsageEvent
from bot.services.usage import ACTION_LABELS
from bot.webapp.server import USAGE_KEY, create_app
from bot.webapp.usage import UsageWriter
from tests.conftest import _player
from tests.test_webapp import ADMIN, TOKEN, _init_data


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    async with db_factory() as s:
        s.add(_player(ADMIN, "Алиса"))
        await s.commit()
    app = create_app(TOKEN, db_factory)
    async with TestClient(TestServer(app)) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        c.writer = app[USAGE_KEY]
        yield c


async def _events(db_factory):
    async with db_factory() as s:
        return [(e.user_id, e.action) for e in (await s.execute(select(UsageEvent))).scalars().all()]


async def test_track_writes_app_event(client, db_factory):
    resp = await client.post("/api/track", json={"screen": "me_matches"}, headers=client.auth)
    assert (await resp.json()) == {"ok": True}
    await client.writer.drain()
    assert await _events(db_factory) == [(ADMIN, "app_me_matches")]


@pytest.mark.parametrize("screen", ["", "Me", "player/12", "a" * 41, "drop table", None])
async def test_track_rejects_odd_screen_names(client, db_factory, screen):
    resp = await client.post("/api/track", json={"screen": screen}, headers=client.auth)
    assert (await resp.json()) == {"ok": False}
    await client.writer.drain()
    assert await _events(db_factory) == []


async def test_track_requires_init_data(client, db_factory):
    resp = await client.post("/api/track", json={"screen": "home"})
    assert resp.status == 401
    assert await _events(db_factory) == []


async def test_track_bad_json_is_ignored(client):
    resp = await client.post("/api/track", data="не json", headers={**client.auth, "Content-Type": "application/json"})
    assert (await resp.json()) == {"ok": False}


async def test_boot_time_goes_to_log_not_db(client, db_factory, caplog):
    with caplog.at_level(logging.INFO, logger="bot.webapp.usage"):
        await client.post("/api/track", json={"screen": "home", "boot_ms": 842}, headers=client.auth)
    assert any("загрузка 842 мс" in r.message for r in caplog.records)
    await client.writer.drain()
    assert await _events(db_factory) == [(ADMIN, "app_home")]


async def test_absurd_boot_time_not_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="bot.webapp.usage"):
        await client.post("/api/track", json={"screen": "home", "boot_ms": 10**9}, headers=client.auth)
        await client.post("/api/track", json={"screen": "home", "boot_ms": True}, headers=client.auth)
    assert not any("загрузка" in r.message for r in caplog.records)


async def test_write_retries_then_gives_up_quietly(monkeypatch, caplog):
    import bot.webapp.usage as usage_module

    monkeypatch.setattr(usage_module, "WRITE_RETRY_DELAY", 0)
    calls = {"n": 0}

    def broken_factory():
        calls["n"] += 1
        raise RuntimeError("база недоступна")

    writer = UsageWriter(broken_factory)
    with caplog.at_level(logging.WARNING):
        assert writer.record(1, "home") is True
        await writer.drain()
    assert calls["n"] == usage_module.WRITE_ATTEMPTS
    assert any("не удалось записать" in r.message for r in caplog.records)


def test_every_app_screen_has_a_label():
    """Экраны, которые шлёт страница, подписаны по-русски в /usage."""
    screens = ["home", "rating", "me", "me_matches", "me_achievements", "me_style", "player", "player_matches",
               "player_achievements", "player_style", "h2h", "records", "matches", "activity_club", "throne"]
    for s in screens:
        assert ACTION_LABELS.get(f"app_{s}"), s
