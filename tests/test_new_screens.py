"""
Пакет 4, релиз 5 (v2.183.0): рейтинг за 90 дней, «Кто с кем» (матрица пар),
цифры-табло рейтинга, «Твой месяц» во весь экран, мячик «потяни, чтобы
обновить», контраст подписей в светлой теме. Табло — в Node.
Запуск: pytest tests/test_new_screens.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.webapp import api_club, api_insights
from bot.webapp.server import create_app
from tests.conftest import _completed, _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


async def _club(db):
    a, b, c, d = (_player(ADMIN, "Алиса", 1100.0), _player(2002, "Боб", 1050.0),
                  _player(3003, "Вика", 1000.0), _player(4004, "Гоша", 990.0))
    db.add_all([a, b, c, d])
    await db.flush()
    db.add_all([
        _completed(a, b, a.id, 5.0, NOW - timedelta(days=1)),
        _completed(b, a, b.id, 5.0, NOW - timedelta(days=2)),
        _completed(a, b, a.id, 5.0, NOW - timedelta(days=3)),
        _completed(a, c, c.id, 5.0, NOW - timedelta(days=60)),
    ])
    await db.commit()
    return a, b, c, d


async def test_pairs_matrix_counts_and_wins(db):
    a, b, c, d = await _club(db)
    p = await api_insights.pairs_payload(db)
    names = [x["name"] for x in p["players"]]
    assert names == ["Алиса", "Боб", "Вика"]                 # Гоша не играл — не в матрице
    m = p["matrix"]
    assert m[0][0] is None
    assert m[0][1] == {"n": 3, "wins": 2, "lvl": 3} and m[1][0] == {"n": 3, "wins": 1, "lvl": 3}
    assert m[0][2]["n"] == 1 and m[2][0]["wins"] == 1 and m[0][2]["lvl"] == 1
    assert m[1][2] == {"n": 0, "wins": 0, "lvl": 0}
    facts = {f["label"]: f for f in p["facts"]}
    assert facts["Чаще всех"] == {"label": "Чаще всех", "value": "Алиса — Боб", "note": "3 матча"}
    assert facts["Ещё не играли"]["value"] == "1" and facts["Ещё не играли"]["note"] == "Боб — Вика"


async def test_pairs_empty(db):
    assert (await api_insights.pairs_payload(db))["empty"]


async def test_quarter_period_counts_90_days(db):
    a, *_ = await _club(db)
    d = await api_club.period_leaderboard_payload(db, a, "quarter")
    assert d["label"] == "за 90 дней"
    gains = {r["name"]: r["gain"] for r in d["rows"]}
    assert "Вика" in gains                                   # матч 60 дней назад — внутри 90


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    async with db_factory() as s:
        await _club(s)
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        yield c


async def test_routes_and_home_link(client):
    assert (await client.get("/api/pairs", headers=client.auth)).status == 200
    assert (await client.get("/api/leaderboard?period=quarter", headers=client.auth)).status == 200
    home = await (await client.get("/api/home", headers=client.auth)).json()
    assert "pairs" in [link["route"] for link in home["links"]]


def test_page_wiring():
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    month = (SRC / "21-month.js").read_text(encoding="utf-8")
    gestures = (SRC / "22-gestures.js").read_text(encoding="utf-8")
    profile = (SRC / "12-profile.js").read_text(encoding="utf-8")
    rating = (SRC / "11-rating.js").read_text(encoding="utf-8")
    assert '[/^pairs$/, "home", one("pairs"), renderPairs, "pairs"]' in routes
    assert "enterFullscreen();" in month and "leaveFullscreen();" in routes
    assert 'tg.onEvent("fullscreenChanged", syncFullscreenClass)' in routes
    assert "return Promise.all(routeJobs(route))" in routes and "dropScene(mark, true)" in gestures
    assert 'ratingNumber("hero-value", h.rating, playerId)' in profile
    assert '["quarter", "90 дней"]' in rating


def test_light_theme_hint_contrast_rule():
    assert ':root:not([data-theme="dark"]) { --hint: color-mix(in srgb, var(--tg-theme-hint-color, #6D6D72) 80%, #000); }' in CSS
    assert "--tg-content-safe-area-inset-top" in CSS                  # полный экран — отступ под системную панель
