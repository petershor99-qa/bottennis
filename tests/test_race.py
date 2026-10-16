"""
Пакет 4, релиз 6 (v2.184.0): «Гонка рейтинга» — рейтинг каждого игрока на
конец каждого дня за 30 дней. Порядок мест — в Node (raceOrder).
Запуск: pytest tests/test_race.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.utils import MSK_OFFSET
from bot.webapp import api_insights
from bot.webapp.server import create_app
from tests.conftest import _completed, _player, freeze_clock
from tests.test_webapp import ADMIN, TOKEN, _init_data

NOW = datetime.now(timezone.utc).replace(tzinfo=None)
SRC = Path(__file__).resolve().parent.parent / "webapp" / "src" / "app"


@pytest.fixture(autouse=True)
def _clock_at_now(monkeypatch):
    """Часы кода под тестом стоят на NOW: последний день гонки — день NOW, даже
    если прогон пересёк полночь МСК между импортом модуля и тестом."""
    freeze_clock(monkeypatch, NOW)


def _midday(days_ago: int) -> datetime:
    """Полдень по МСК `days_ago` дней назад (UTC-naive) — далеко от границы суток."""
    d = (NOW + MSK_OFFSET).date() - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, 12) - MSK_OFFSET


async def test_race_frames_walk_back_from_current_rating(db):
    a, b, c = _player(ADMIN, "Алиса", 1030.0), _player(2002, "Боб", 990.0), _player(3003, "Вика", 1000.0)
    db.add_all([a, b, c])
    await db.flush()
    db.add_all([_completed(a, b, a.id, 20.0, _midday(10)), _completed(b, a, b.id, 10.0, _midday(2))])
    await db.commit()
    d = await api_insights.race_payload(db)
    assert [p["name"] for p in d["players"]] == ["Алиса", "Боб"]          # Вика без матчей — не в гонке
    assert len(d["days"]) == len(d["frames"]) == api_insights.RACE_DAYS
    first, mid, last = d["frames"][0], d["frames"][-5], d["frames"][-1]
    assert last == [1030.0, 990.0]                                          # сегодня — текущий рейтинг
    assert mid == [1040.0, 980.0]                                           # до матча 2 дня назад
    assert first == [1020.0, 1000.0]                                        # до матча 10 дней назад
    assert d["days"][-1] == (NOW + MSK_OFFSET).strftime("%d.%m")


async def test_race_ignores_older_matches_and_empty(db):
    assert (await api_insights.race_payload(db))["empty"]
    a, b = _player(ADMIN, "Алиса"), _player(2002, "Боб")
    db.add_all([a, b])
    await db.flush()
    db.add(_completed(a, b, a.id, 5.0, NOW - timedelta(days=45)))
    await db.commit()
    assert (await api_insights.race_payload(db))["empty"] == "За 30 дней матчей не было."


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    async with db_factory() as s:
        a, b = _player(ADMIN, "Алиса"), _player(2002, "Боб")
        s.add_all([a, b])
        await s.flush()
        s.add(_completed(a, b, a.id, 5.0, NOW - timedelta(hours=2)))
        await s.commit()
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        yield c


async def test_race_route_and_home_link(client):
    assert (await client.get("/api/race", headers=client.auth)).status == 200
    home = await (await client.get("/api/home", headers=client.auth)).json()
    assert "race" in [x["route"] for x in home["links"]]


def test_race_page_wiring():
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    race = (SRC / "26-race.js").read_text(encoding="utf-8")
    assert '[/^race$/, "home", one("race"), renderRace, "race"]' in routes
    assert "if (!card.isConnected) { stop(); return; }" in race           # ушли с экрана — таймер стоп
    assert "if (reduceMotion) { at = d.days.length - 1; show(at); }" in race
