"""
Релиз 4 пакета «Бот + Mini App 2» (v2.170.0): «Когда играют», «Использование»
для администратора, итоги года в приложении («Твой год», звания).
Запуск: pytest tests/test_app_insights.py
"""
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.db.models import DigestSnapshot, UsageEvent, YearVote
from bot.utils import MSK_OFFSET
from bot.webapp import api_insights
from bot.webapp.server import create_app
from tests.conftest import _completed, _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _msk_slot(weekday: int, hour: int, minute: int = 10, weeks_ago: int = 1) -> datetime:
    """UTC-время матча, который по МСК пришёлся на weekday/hour `weeks_ago` недель назад."""
    msk_now = NOW + MSK_OFFSET
    monday = (msk_now - timedelta(days=msk_now.weekday(), weeks=weeks_ago)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return monday + timedelta(days=weekday, hours=hour, minutes=minute) - MSK_OFFSET


async def _three(db):
    a, b, c = _player(ADMIN, "Алиса", 1050.0), _player(2002, "Боб"), _player(3003, "Вика")
    db.add_all([a, b, c])
    await db.flush()
    return a, b, c


# ── Когда играют ──────────────────────────────────────────────────────────────

async def test_when_grid_peak_and_filters(db):
    a, b, c = await _three(db)
    db.add_all([
        _completed(a, b, a.id, 5.0, _msk_slot(1, 13)),               # вт 13 — трижды
        _completed(a, b, b.id, 5.0, _msk_slot(1, 13, weeks_ago=2)),
        _completed(b, c, b.id, 5.0, _msk_slot(1, 13, weeks_ago=3)),
        _completed(b, c, c.id, 5.0, _msk_slot(3, 10)),               # чт 10
        _completed(b, c, c.id, 5.0, _msk_slot(5, 12)),               # сб 12 — выходной есть
        _completed(a, c, a.id, 5.0, NOW - timedelta(days=200)),      # вне 90 дней
    ])
    await db.commit()

    club = await api_insights.when_payload(db, a, "club")
    assert club["total"] == "5 матчей"
    assert [r["day"] for r in club["rows"]] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]  # Вс пуст — скрыт
    assert club["hours"][0] == 9 and club["hours"][-1] == 18
    tue = next(r for r in club["rows"] if r["day"] == "Вт")
    cell = next(x for x in tue["cells"] if x["h"] == 13)
    assert cell == {"h": 13, "n": 3, "lvl": 3}
    assert club["facts"][0] == {"label": "Пик", "value": "Вт 13:00–14:00", "note": "3 матча"}
    assert club["facts"][1]["value"] == "Вторник · 3"
    assert any(f["label"] == "Обычно играют" for f in club["facts"])

    mine = await api_insights.when_payload(db, a, "me")
    assert mine["total"] == "2 матча"
    assert [r["day"] for r in mine["rows"]] == ["Пн", "Вт", "Ср", "Чт", "Пт"]
    assert not any(f["label"].startswith("Обычно") for f in mine["facts"])  # меньше 3 дней

    assert await api_insights.when_payload(db, a, "bogus") is None


async def test_when_empty(db):
    a, *_ = await _three(db)
    await db.commit()
    assert (await api_insights.when_payload(db, a, "me"))["empty"] == "У тебя за 90 дней матчей нет."
    assert (await api_insights.when_payload(db, a, "club"))["empty"] == "За 90 дней матчей нет."


async def test_when_grid_grows_for_late_games(db):
    a, b, _ = await _three(db)
    db.add(_completed(a, b, a.id, 5.0, _msk_slot(2, 21)))
    await db.commit()
    data = await api_insights.when_payload(db, a, "club")
    assert data["hours"][-1] == 21


# ── Использование ─────────────────────────────────────────────────────────────

async def test_usage_payload(db):
    db.add_all([
        UsageEvent(user_id=1, action="menu_leaderboard", created_at=NOW - timedelta(hours=1)),
        UsageEvent(user_id=2, action="menu_leaderboard", created_at=NOW - timedelta(days=2)),
        UsageEvent(user_id=1, action="app_home", created_at=NOW - timedelta(hours=2)),
        UsageEvent(user_id=1, action="app_when_me", created_at=NOW - timedelta(hours=2)),
        UsageEvent(user_id=3, action="menu_leaderboard", created_at=NOW - timedelta(days=40)),  # вне окна
    ])
    await db.commit()
    data = await api_insights.usage_payload(db)
    tiles = {t["label"]: t["value"] for t in data["tiles"]}
    assert tiles == {"События": "4", "Игроки": "2", "Доля приложения": "50%", "В приложении": "1"}
    assert len(data["daily"]) == 30
    assert sum(x["bot"] for x in data["daily"]) == 2 and sum(x["app"] for x in data["daily"]) == 2
    assert data["bot"][0]["value"] == "2 · 2 игрока"
    assert {x["label"] for x in data["app"]} == {"Главная «Клуб»", "Когда играют: свои"}
    assert all(x["note"].startswith("последнее ") for x in data["bot"] + data["app"])


async def test_usage_empty(db):
    assert (await api_insights.usage_payload(db))["empty"]


def test_usage_unknown_app_screen_shown_as_is():
    assert api_insights._screen_label("app_new_thing") == "new_thing"


# ── Итоги года ────────────────────────────────────────────────────────────────

async def test_year_extras_mine_without_votes_before_close(db, monkeypatch):
    a, b, c = await _three(db)
    year = (NOW + MSK_OFFSET).year
    db.add_all([
        _completed(a, b, a.id, 10.0, datetime(year, 6, 1, 9, 0)),
        _completed(b, a, b.id, 4.0, datetime(year, 6, 1, 10, 0)),
        _completed(a, c, a.id, 6.0, datetime(year, 6, 1, 11, 0)),
        YearVote(year=year, nomination="gentleman", voter_id=b.id, nominee_id=a.id),
    ])
    await db.commit()
    monkeypatch.setattr(api_insights, "_now", lambda: datetime(year, 12, 30, 8, 0))  # 11:00 МСК
    extras = await api_insights.year_extras(db, a)
    items = {i["label"]: i for i in extras["mine"]["items"]}
    assert extras["mine"]["title"] == f"Твой {year}"
    assert items["Матчей"]["value"] == "3"
    assert items["Побед"]["value"] == "2 · 67%"
    assert items["Рейтинг за год"]["value"] == "+12.0"
    assert items["Чаще всего с"]["value"] == "Боб" and items["Чаще всего с"]["note"] == "2 матча"
    assert "vote" not in extras                            # до 30.12 12:00 — званий нет


async def test_year_extras_votes_after_close_and_tie(db, monkeypatch):
    a, b, c = await _three(db)
    year = (NOW + MSK_OFFSET).year
    db.add_all([
        YearVote(year=year, nomination="gentleman", voter_id=b.id, nominee_id=a.id),
        YearVote(year=year, nomination="excuse", voter_id=a.id, nominee_id=b.id),
        YearVote(year=year, nomination="excuse", voter_id=b.id, nominee_id=c.id),
    ])
    await db.commit()
    monkeypatch.setattr(api_insights, "_now", lambda: datetime(year, 12, 30, 9, 30))  # 12:30 МСК
    extras = await api_insights.year_extras(db, c)
    assert "mine" not in extras                            # у Вики матчей нет
    items = {i["label"]: i for i in extras["vote"]["items"]}
    assert items["Джентльмен года"] == {"label": "Джентльмен года", "value": "Алиса", "note": "1 голос"}
    assert items["Отмазка года"]["note"] == "1 голос у каждого"
    assert set(items["Отмазка года"]["value"].split(" и ")) == {"Боб", "Вика"}
    assert "Комментатор года" not in items                 # без голосов — не показывается


async def test_year_from_snapshot(db):
    db.add(DigestSnapshot(kind="year", text="x", created_at=datetime(2025, 12, 30, 9, 0)))
    await db.commit()
    assert await api_insights._summary_year(db) == 2025


# ── HTTP ──────────────────────────────────────────────────────────────────────

@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")
    async with db_factory() as s:
        a, b, _ = await _three(s)
        s.add(_completed(a, b, a.id, 5.0, NOW - timedelta(hours=1)))
        s.add(UsageEvent(user_id=ADMIN, action="app_home", created_at=NOW))
        await s.commit()
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.admin = {"Authorization": "tma " + _init_data(ADMIN)}
        c.player = {"Authorization": "tma " + _init_data(2002)}
        yield c


@pytest.mark.parametrize("path", ["/api/when", "/api/when?who=me", "/api/usage", "/api/summary/year"])
async def test_r4_routes_answer_admin(client, path):
    resp = await client.get(path, headers=client.admin)
    assert resp.status == 200, await resp.text()


async def test_when_bad_who_404(client):
    assert (await client.get("/api/when?who=all", headers=client.admin)).status == 404


async def test_usage_hidden_from_players(client):
    assert (await client.get("/api/usage", headers=client.player)).status == 404
    home = await (await client.get("/api/home", headers=client.player)).json()
    routes = [x["route"] for x in home["links"]]
    assert "when" in routes and "usage" not in routes
    admin_home = await (await client.get("/api/home", headers=client.admin)).json()
    assert "usage" in [x["route"] for x in admin_home["links"]]


async def test_summary_year_has_my_year_even_without_snapshot(client):
    data = await (await client.get("/api/summary/year", headers=client.admin)).json()
    assert data["empty"] and data["mine"]["items"][0] == {"label": "Матчей", "value": "1"}
