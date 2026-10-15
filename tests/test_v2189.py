"""
v2.189.0: скорость на большом клубе — кэш профиля и рекордов по версии данных
клуба с прогревом в фоне, рейтинг и спарклайны без объектов ORM, длинные
списки матчей порциями.
Запуск: pytest tests/test_v2189.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.webapp import api, payload_cache, server
from tests.conftest import _completed, _player


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"


async def _club(db, n=12):
    a, b, c = _player(1, "A", 1050.0), _player(2, "B", 990.0), _player(3, "C", 960.0)
    db.add_all([a, b, c])
    await db.flush()
    t = _now() - timedelta(days=3)
    for i in range(n):
        x, y = [(a, b), (b, c), (a, c)][i % 3]
        m = _completed(x, y, x.id if i % 2 else y.id, 8.0, t + timedelta(minutes=10 * i))
        m.sets_data = [{"w": 11, "l": 7}, {"w": 11, "l": 9}]
        db.add(m)
    await db.commit()
    return a, b, c


async def test_cached_returns_copy_and_rebuilds_on_new_match(db):
    a, b, c = await _club(db)
    calls = []

    async def build():
        calls.append(1)
        return {"head": {"name": "A"}}

    first = await payload_cache.cached(db, ("x",), build)
    first["head"]["photo_link"] = "mutated"        # вызывающий дописывает поля — кэш не портится
    second = await payload_cache.cached(db, ("x",), build)
    assert len(calls) == 1 and "photo_link" not in second["head"]

    db.add(_completed(a, b, a.id, 5.0, _now()))
    await db.commit()
    await payload_cache.cached(db, ("x",), build)
    assert len(calls) == 2


async def test_rating_change_without_match_rebuilds(db):
    a, _, _ = await _club(db)
    calls = []

    async def build():
        calls.append(1)
        return {}

    await payload_cache.cached(db, ("x",), build)
    a.rating = 1100.0                               # правка рейтинга админом
    await db.commit()
    await payload_cache.cached(db, ("x",), build)
    assert len(calls) == 2


async def test_ttl_expires(db, monkeypatch):
    await _club(db)
    calls = []

    async def build():
        calls.append(1)
        return {}

    now = [1000.0]
    monkeypatch.setattr(payload_cache.time, "monotonic", lambda: now[0])
    await payload_cache.cached(db, ("x",), build)
    now[0] += payload_cache.TTL_S - 1
    await payload_cache.cached(db, ("x",), build)
    now[0] += 2
    await payload_cache.cached(db, ("x",), build)
    assert len(calls) == 2


async def test_warm_builds_profiles_once_per_version(db, monkeypatch):
    a, b, c = await _club(db)
    seen = []
    real = api.player_payload

    async def spy(session, player, viewer):
        seen.append((player.id, player.id == viewer.id))
        return await real(session, player, viewer)

    monkeypatch.setattr(api, "player_payload", spy)
    assert await payload_cache.warm(db) is True
    assert sorted(seen) == sorted((p, own) for p in (a.id, b.id, c.id) for own in (True, False))
    assert await payload_cache.warm(db) is False    # версия та же — ничего не пересчитывается

    seen.clear()
    payload = await payload_cache.cached(db, ("player", b.id, False), lambda: spy(db, b, a))
    assert seen == [] and payload["head"]["name"] == "B"


async def test_player_route_uses_cache(db, monkeypatch):
    a, b, _ = await _club(db)
    calls = []
    real = api.player_payload

    async def spy(session, player, viewer):
        calls.append(player.id)
        return await real(session, player, viewer)

    monkeypatch.setattr(api, "player_payload", spy)

    class Req:
        match_info = {"player_id": str(b.id)}
        app: dict = {}

    first = await server.player(Req(), db, a)
    second = await server.player(Req(), db, a)
    assert calls == [b.id] and first["head"] == second["head"]


async def test_records_route_uses_cache(db, monkeypatch):
    a, _, _ = await _club(db)
    calls = []
    real = api.records_payload

    async def spy(session):
        calls.append(1)
        return await real(session)

    monkeypatch.setattr(api, "records_payload", spy)
    r1 = await server.records(None, db, a)
    r2 = await server.records(None, db, a)
    assert calls == [1] and r1 == r2


async def test_leaderboard_and_sparklines_from_columns(db):
    a, b, c = await _club(db)
    lb = await api.leaderboard_payload(db, a.id)
    names = [r["name"] for r in lb["rows"]]
    assert set(names) == {"A", "B", "C"}
    spark = await api._sparklines(db, {a.id: a.rating, b.id: b.rating})
    assert spark[a.id][-1] == round(a.rating, 1) and len(spark[a.id]) >= 2


def test_warm_runs_only_in_real_server():
    src = (ROOT / "bot" / "webapp" / "server.py").read_text(encoding="utf-8")
    start = src.index("async def start_webapp")
    assert "payload_cache.warm_forever(session_factory)" in src[start:]
    assert "warm_forever" not in src[:start]           # create_app (и тесты) — без фоновой задачи


def test_long_lists_render_in_chunks():
    core = (SRC / "01-core.js").read_text(encoding="utf-8")
    assert "function appendInChunks(list, items, make, step)" in core and "var LIST_STEP = 40;" in core
    assert "Показать ещё (" in core
    assert "appendInChunks(s.list, shown," in (SRC / "12-profile.js").read_text(encoding="utf-8")
    assert "appendInChunks(s.list, d.matches," in (SRC / "13-matches.js").read_text(encoding="utf-8")
