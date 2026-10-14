"""
Пакет 3, релиз 1 (v2.171.0): лента клуба порциями («Показать ещё»), реакция
«какашка», меню клуба выше ленты. Запуск: pytest tests/test_app_home.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.webapp import api_club
from bot.webapp.reactions import REACTIONS, toggle_reaction
from bot.webapp.server import create_app
from tests.conftest import _completed, _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

NOW = datetime.now(timezone.utc).replace(tzinfo=None)
STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
MATCHES = api_club.FEED_PAGE + 5


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.delenv("WEBAPP_AUDIENCE", raising=False)
    async with db_factory() as s:
        a, b = _player(ADMIN, "Алиса", 1050.0), _player(2002, "Боб")
        s.add_all([a, b])
        await s.flush()
        for i in range(MATCHES):
            s.add(_completed(a, b, a.id, 5.0, NOW - timedelta(hours=i + 1)))
        await s.commit()
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        yield c


async def test_home_has_first_feed_page_and_more_flag(client):
    data = await (await client.get("/api/home", headers=client.auth)).json()
    assert len(data["feed"]) == api_club.FEED_PAGE
    assert data["feed_more"] is True


async def test_feed_pages_cover_everything_without_duplicates(client):
    first = await (await client.get("/api/feed?offset=0", headers=client.auth)).json()
    second = await (await client.get(f"/api/feed?offset={api_club.FEED_PAGE}", headers=client.auth)).json()
    ids = [e["id"] for e in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == MATCHES
    assert first["more"] is True and second["more"] is False
    assert all("reactions" in e for e in second["items"])


@pytest.mark.parametrize("offset", ["-1", "x", str(api_club.FEED_LIMIT + 1)])
async def test_feed_bad_offset_404(client, offset):
    assert (await client.get(f"/api/feed?offset={offset}", headers=client.auth)).status == 404


async def test_feed_offset_past_end_is_empty(client):
    data = await (await client.get("/api/feed?offset=200", headers=client.auth)).json()
    assert data == {"items": [], "more": False}


async def test_poo_reaction(db):
    a, b = _player(ADMIN, "Алиса"), _player(2002, "Боб")
    db.add_all([a, b])
    await db.flush()
    m = _completed(a, b, a.id, 5.0, NOW)
    db.add(m)
    await db.commit()
    assert REACTIONS["poo"] == ("poo", "Какашка")
    result = await toggle_reaction(db, m.id, b, "poo")
    assert next(r for r in result if r["kind"] == "poo")["count"] == 1


def test_home_shows_club_menu_before_feed():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    home = js[js.index("function renderHome"):]
    assert home.index("clubTiles(d.links)") < home.index("feedBlock(d.feed")


def test_vertical_swipes_disabled_guarded():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'isVersionAtLeast("7.7") && tg.disableVerticalSwipes' in js
