"""
Пакет 3, релиз 3 (v2.173.0): кто поставил реакцию (экран матча), реакции на
карточке «Матч дня». Уведомлений по-прежнему нет.
Запуск: pytest tests/test_app_reactions2.py
"""
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.webapp.reactions import reactions_for, toggle_reaction
from bot.webapp.server import create_app
from tests.conftest import _completed, _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


async def _setup(db):
    a, b, c = _player(ADMIN, "Алиса"), _player(2002, "Боб"), _player(3003, "Вика")
    db.add_all([a, b, c])
    await db.flush()
    m = _completed(a, b, a.id, 5.0, NOW - timedelta(minutes=5))
    db.add(m)
    await db.commit()
    return a, b, c, m


async def test_who_lists_names_in_order_only_with_names(db):
    a, b, c, m = await _setup(db)
    await toggle_reaction(db, m.id, c, "fire")
    await toggle_reaction(db, m.id, a, "fire")
    await toggle_reaction(db, m.id, b, "poo")
    named = {r["kind"]: r for r in (await reactions_for(db, [m.id], a.id, with_names=True))[m.id]}
    assert named["fire"]["who"] == ["Вика", "Алиса"]
    assert named["poo"]["who"] == ["Боб"] and named["like"]["who"] == []
    plain = (await reactions_for(db, [m.id], a.id))[m.id]
    assert all("who" not in r for r in plain)               # лента — только счётчики


async def test_removed_reaction_disappears_from_who(db):
    a, b, c, m = await _setup(db)
    await toggle_reaction(db, m.id, b, "lol")
    result = await toggle_reaction(db, m.id, b, "lol")
    assert next(r for r in result if r["kind"] == "lol")["who"] == []


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")
    async with db_factory() as s:
        _, _, _, m = await _setup(s)
        match_id = m.id
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.alice = {"Authorization": "tma " + _init_data(ADMIN)}
        c.bob = {"Authorization": "tma " + _init_data(2002)}
        c.match_id = match_id
        yield c


async def test_match_screen_shows_who_and_home_featured_has_counts(client):
    await client.post(f"/api/match/{client.match_id}/react", json={"kind": "like"}, headers=client.bob)
    screen = await (await client.get(f"/api/match/{client.match_id}", headers=client.alice)).json()
    like = next(r for r in screen["reactions"] if r["kind"] == "like")
    assert like["who"] == ["Боб"] and like["mine"] is False
    home = await (await client.get("/api/home", headers=client.alice)).json()
    assert home["featured"]["id"] == client.match_id
    assert home["featured"]["reactions"] == [{"icon": "thumb-up", "count": 1}]
