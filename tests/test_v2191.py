"""
v2.191.0: трон в «Зале славы» — картинка владельца с версией в адресе, живой
таймер правления, претендент (или ближайший к трону), летопись трона,
анимация смены чемпиона.
Запуск: pytest tests/test_v2191.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.db.models import ChampionReign, Match, MatchStatus
from bot.utils import NEWCOMER_THRESHOLD
from bot.webapp import api, server
from bot.webapp.assets import IMMUTABLE, Assets
from tests.conftest import _player

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "webapp" / "static"
SRC = ROOT / "webapp" / "src" / "app"
CSS = (STATIC / "app.css").read_text(encoding="utf-8")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _club(db, *, challenger_matches=NEWCOMER_THRESHOLD, challenger_rating=1150.0):
    champ, rival, third = _player(1, "Вика", 1100.0), _player(2, "Боб", challenger_rating), _player(3, "Ева", 990.0)
    champ.is_champion = True
    db.add_all([champ, rival, third])
    await db.flush()
    now = _now()
    for i in range(challenger_matches):
        db.add(Match(challenger_id=rival.id, challenged_id=third.id, status=MatchStatus.completed,
                     winner_id=rival.id, sets_data=[{"w": 11, "l": 5}], rating_change=1.0,
                     completed_at=now - timedelta(days=30, minutes=i)))
    db.add(Match(challenger_id=champ.id, challenged_id=third.id, status=MatchStatus.completed,
                 winner_id=champ.id, sets_data=[{"w": 11, "l": 5}], rating_change=1.0,
                 completed_at=now - timedelta(days=2)))
    db.add(ChampionReign(player_id=third.id, started_at=now - timedelta(days=20), ended_at=now - timedelta(days=12)))
    db.add(ChampionReign(player_id=champ.id, started_at=now - timedelta(days=12)))
    await db.commit()
    return champ, rival, third


async def test_since_ts_and_timeline(db):
    champ, _, third = await _club(db)
    d = await api.hall_of_fame_payload(db)
    started = _now() - timedelta(days=12)
    assert abs(d["current"]["since_ts"] - started.replace(tzinfo=timezone.utc).timestamp() * 1000) < 5000
    tl = d["timeline"]
    assert [r["player_id"] for r in tl] == [third.id, champ.id]          # по порядку, текущее — последним
    assert tl[0]["end_ts"] is not None and tl[1]["end_ts"] is None
    assert tl[1]["period"].endswith("– сейчас") and tl[0]["duration"] == "8 дней"


async def test_contender_is_boss_fight_challenger(db):
    champ, rival, _ = await _club(db)
    k = (await api.hall_of_fame_payload(db))["contender"]
    assert k["kind"] == "challenger" and k["player_id"] == rival.id and k["champion_id"] == champ.id
    assert k["title"] == "Претендент" and k["text"].startswith("на 50.0 очка выше чемпиона")


async def test_nearest_when_rating_below(db):
    await _club(db, challenger_rating=1080.0)
    k = (await api.hall_of_fame_payload(db))["contender"]
    assert k["kind"] == "nearest" and k["name"] == "Боб" and k["text"] == "до претендента 20.0 очка"


async def test_nearest_when_matches_missing(db):
    await _club(db, challenger_matches=NEWCOMER_THRESHOLD - 3)
    k = (await api.hall_of_fame_payload(db))["contender"]
    assert k["kind"] == "nearest" and k["text"] == "рейтинг уже выше чемпиона, до претендента — 3 матча"


async def test_no_contender_without_champion(db):
    a = _player(1, "Вика")
    db.add(a)
    await db.flush()
    db.add(ChampionReign(player_id=a.id, started_at=_now() - timedelta(days=5), ended_at=_now() - timedelta(days=1)))
    await db.commit()
    d = await api.hall_of_fame_payload(db)
    assert d["current"] is None and d["contender"] is None


async def test_route_adds_versioned_art(db):
    champ, _, _ = await _club(db)

    class Req:
        app = {server.ASSETS_KEY: Assets(STATIC)}

    d = await server.throne(Req(), db, champ)
    version = Assets(STATIC).get("throne.webp").version
    assert d["art"] == f"static/throne.webp?v={version}"


def test_throne_image_served_immutable_without_gzip():
    from bot.webapp.assets import _NO_GZIP, ASSET_TYPES
    assert ASSET_TYPES["throne.webp"] == "image/webp" and "throne.webp" in _NO_GZIP
    data = (STATIC / "throne.webp").read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) < 100 * 1024
    assert IMMUTABLE.startswith("public")


def test_client_wiring():
    club = (SRC / "14-club.js").read_text(encoding="utf-8")
    for s in ("function reignClock(ms)", "function timelineShares(items, now)", "function contenderCard(k)",
              "function timelineCard(items)", 'var SEEN_CHAMPION_KEY = "seen_champion_v1";',
              '"Вызвать чемпиона на боссфайт"', "if (!card.isConnected) { clearInterval(tick); return; }"):
        assert s in club
    for sel in (".throne-img", ".throne-glow", ".throne-spark", ".is-new-king .throne-sitter .crown-big",
                ".timeline-seg", "@keyframes crown-fall", "@keyframes spark-rise"):
        assert sel in CSS
    tail = CSS[CSS.index("/* Трон (v2.191.0)"):]
    assert "prefers-reduced-motion: reduce" in tail and ".throne-spark { display: none; }" in tail
