"""
v2.199.0: «Рекорды клуба» витриной — главный рекорд, «Под угрозой», плитки,
«Рекордсмены»; строки, ставшие карточками, из списка ниже убраны.
Запуск: pytest tests/test_v2199.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bot.webapp.api_records as rec
from bot.db.models import ChampionReign
from bot.webapp import api
from tests.conftest import _completed, _player

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


async def _club(db):
    a, b, c = _player(1, "Алиса", 1100.0), _player(2, "Боб", 1050.0), _player(3, "Вика", 1000.0)
    a.peak_rating = 1180.0
    db.add_all([a, b, c])
    await db.flush()
    t = NOW - timedelta(days=60)
    ms = []
    for i in range(7):                                   # у Алисы серия 7 (рекорд)
        ms.append(_completed(a, c, a.id, 8.0, t + timedelta(hours=i)))
    ms.append(_completed(a, c, c.id, 8.0, t + timedelta(hours=8)))
    for i in range(5):                                   # у Боба текущая серия 5
        ms.append(_completed(b, c, b.id, 8.0, NOW - timedelta(days=5 - i)))
    ms.append(_completed(a, b, a.id, 30.0, NOW - timedelta(days=6)))   # апсет +30, свежий (до серии Боба)
    db.add_all(ms)
    db.add_all([ChampionReign(player_id=a.id, started_at=NOW - timedelta(days=100), ended_at=NOW - timedelta(days=50)),
                ChampionReign(player_id=b.id, started_at=NOW - timedelta(days=30))])
    await db.commit()
    return a, b, c


async def test_showcase_hero_tiles_threats_holders(db):
    a, b, c = await _club(db)
    s = await rec.showcase(db)
    assert s["hero"]["title"] == "Высший рейтинг в истории" and s["hero"]["value"] == "1180.0"
    keys = [t["key"] for t in s["tiles"]]
    assert keys[:3] == ["matches", "streak", "reign"] and "upset" in keys
    upset = next(t for t in s["tiles"] if t["key"] == "upset")
    assert upset["value"] == "+30.0" and upset["new"] and upset["route"].startswith("match/")
    labels = [t["label"] for t in s["threats"]]
    assert "Серия побед: Боб 5 из 7" in labels
    streak = next(t for t in s["threats"] if t["tone"] == "streak")
    assert streak["note"] == "ещё 3 победы — и рекорд его (сейчас рекорд — Алиса)" and streak["progress"] == round(5 / 7, 3)
    reign = next(t for t in s["threats"] if t["label"].startswith("Правление"))
    assert reign["label"] == "Правление: Боб 30 из 50 дней" and reign["note"] == "трон держит 30 дней, рекорд — 50"
    assert s["holders"][0]["name"] == "Алиса" and s["holders"][0]["count"] >= 3


async def test_records_payload_drops_carded_lines_and_has_summary(db):
    await _club(db)
    d = await api.records_payload(db)
    labels = [it.get("label") or it.get("text") for c in d["categories"] for it in c["items"]]
    assert not any(lbl.startswith(("Больше всего матчей", "Лучшая серия побед", "Высший рейтинг")) for lbl in labels)
    assert d["summary"].startswith(tuple(f"{n} рекорд" for n in range(1, 40)))
    assert "больше всех — Алиса" in d["summary"]


def test_held_text_and_threat_threshold():
    now = datetime(2026, 10, 16, 12)
    assert rec.held_text(now - timedelta(days=12), now).startswith("держится 12 дней · с ")
    assert rec.held_text(now - timedelta(days=125), now).startswith("держится 4 мес · с ")
    assert rec.held_text(None, now) == ""
    assert rec.summary_line(17, [{"name": "Вика", "count": 5}, {"name": "Боб", "count": 2}]) == \
        "17 рекордов · больше всех — Вика, 5"
    assert rec.summary_line(3, [{"name": "Вика", "count": 1}]) == "3 рекорда"


def test_page_wiring():
    club = (ROOT / "webapp" / "src" / "app" / "14-club.js").read_text(encoding="utf-8")
    for s in ("Главный рекорд", "Под угрозой", "Рекордсмены", '"новый"', "function recordHero"):
        assert s in club
    css = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")
    assert ".rec-tiles {" in css and ".rec-hero {" in css
