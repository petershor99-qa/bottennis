"""
Релиз 3б пакета «Бот + Mini App 2» (v2.169.0): реакции на матчи (без
уведомлений), единые значки Tabler во всём интерфейсе приложения.
Запуск: pytest tests/test_app_polish.py
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.db.models import Match, MatchStatus
from bot.webapp.reactions import REACTIONS, feed_counts, reactions_for, toggle_reaction
from bot.webapp.server import create_app
from tests.conftest import _completed, _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

NOW = datetime.now(timezone.utc).replace(tzinfo=None)
STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"


async def _pair(db):
    a, b = _player(ADMIN, "Алиса", 1050.0), _player(2002, "Боб", 1000.0)
    db.add_all([a, b])
    await db.flush()
    done = _completed(a, b, a.id, 12.0, NOW - timedelta(hours=1))
    active = Match(challenger_id=a.id, challenged_id=b.id, status=MatchStatus.accepted)
    db.add_all([done, active])
    await db.commit()
    return a, b, done, active


# ── Сервис реакций ────────────────────────────────────────────────────────────

async def test_toggle_reaction_adds_and_removes(db):
    a, b, done, _ = await _pair(db)
    first = await toggle_reaction(db, done.id, a, "fire")
    fire = next(r for r in first if r["kind"] == "fire")
    assert fire == {"kind": "fire", "icon": "flame", "title": "Огонь", "count": 1, "mine": True, "who": ["Алиса"]}
    second = await toggle_reaction(db, done.id, a, "fire")
    assert next(r for r in second if r["kind"] == "fire")["count"] == 0


async def test_reactions_count_players_separately(db):
    a, b, done, _ = await _pair(db)
    await toggle_reaction(db, done.id, a, "like")
    await toggle_reaction(db, done.id, b, "like")
    await toggle_reaction(db, done.id, b, "lol")
    mine = (await reactions_for(db, [done.id], a.id))[done.id]
    by_kind = {r["kind"]: r for r in mine}
    assert by_kind["like"]["count"] == 2 and by_kind["like"]["mine"] is True
    assert by_kind["lol"]["count"] == 1 and by_kind["lol"]["mine"] is False
    assert [r["kind"] for r in mine] == list(REACTIONS)  # все виды, по порядку
    assert feed_counts(mine) == [{"icon": "thumb-up", "count": 2}, {"icon": "mood-happy", "count": 1}]


async def test_toggle_reaction_rejects_bad_kind_and_unfinished_match(db):
    a, b, done, active = await _pair(db)
    assert await toggle_reaction(db, done.id, a, "poop") is None
    assert await toggle_reaction(db, active.id, a, "fire") is None   # матч ещё идёт
    assert await toggle_reaction(db, 99999, a, "fire") is None


async def test_reactions_for_empty_list(db):
    assert await reactions_for(db, [], 1) == {}


# ── HTTP ──────────────────────────────────────────────────────────────────────

@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.delenv("WEBAPP_AUDIENCE", raising=False)
    async with db_factory() as s:
        _, _, done, active = await _pair(s)
        ids = (done.id, active.id)
    bot = SimpleNamespace(send_message=AsyncMock(), get_user_profile_photos=AsyncMock())
    async with TestClient(TestServer(create_app(TOKEN, db_factory, bot))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        c.bot = bot
        c.done_id, c.active_id = ids
        yield c


async def test_react_route_toggles_without_notifications(client):
    url = f"/api/match/{client.done_id}/react"
    data = await (await client.post(url, json={"kind": "fire"}, headers=client.auth)).json()
    assert next(r for r in data["reactions"] if r["kind"] == "fire")["count"] == 1
    screen = await (await client.get(f"/api/match/{client.done_id}", headers=client.auth)).json()
    assert next(r for r in screen["reactions"] if r["kind"] == "fire")["mine"] is True
    home = await (await client.get("/api/home", headers=client.auth)).json()
    feed_match = next(e for e in home["feed"] if e["type"] == "match" and e["id"] == client.done_id)
    assert feed_match["reactions"] == [{"icon": "flame", "count": 1}]
    again = await (await client.post(url, json={"kind": "fire"}, headers=client.auth)).json()
    assert next(r for r in again["reactions"] if r["kind"] == "fire")["count"] == 0
    client.bot.send_message.assert_not_called()  # реакции никого не уведомляют


@pytest.mark.parametrize("body", [{"kind": "poop"}, {}, ["fire"], "fire"])
async def test_react_route_rejects_bad_body(client, body):
    resp = await client.post(f"/api/match/{client.done_id}/react", json=body, headers=client.auth)
    assert resp.status == 404


async def test_react_route_rejects_unknown_or_active_match(client):
    for match_id in (99999, client.active_id):
        resp = await client.post(f"/api/match/{match_id}/react", json={"kind": "fire"}, headers=client.auth)
        assert resp.status == 404
    resp = await client.post("/api/match/x/react", json={"kind": "fire"}, headers=client.auth)
    assert resp.status == 404


async def test_react_route_needs_auth(client):
    resp = await client.post(f"/api/match/{client.done_id}/react", json={"kind": "fire"})
    assert resp.status == 401


async def test_react_get_not_allowed(client):
    resp = await client.get(f"/api/match/{client.done_id}/react", headers=client.auth)
    assert resp.status == 405


# ── Значки: всё, на что ссылается интерфейс, есть в наборе ───────────────────

def test_every_ui_icon_exists_in_sprite():
    sprite = (STATIC / "icons.svg").read_text(encoding="utf-8")
    have = set(re.findall(r'id="ti-([a-z0-9-]+)"', sprite))
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    icons_map = re.search(r"var ICONS = \{(.*?)\};", js, re.S).group(1)
    wanted = set(re.findall(r':\s*"([a-z0-9-]+)"', icons_map))
    wanted |= set(re.findall(r'useIcon\("([a-z0-9-]+)"', js))
    wanted |= {icon for icon, _ in REACTIONS.values()}
    wanted |= {"calendar", "list", "trophy", "crown"}  # icon у строк «Клуб» с сервера
    assert wanted - have == set()


def test_interface_has_no_hand_drawn_paths_left():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'svg("path", { d: ICONS' not in js
