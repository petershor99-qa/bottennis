"""
Пакет 4, релиз 4 (v2.182.0): живой дизайн — мини-графики рейтинга в таблице,
место и уровень звания в профиле (рамка карточки, заставка «Новое звание»),
переходы по направлению, «переезд» строк, конфетти, скелеты по форме экрана.
Логика переходов и мини-графиков — в Node (tests/js/app_logic.test.js).
Запуск: pytest tests/test_live_design.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.utils import RANK_TITLE_BANDS
from bot.webapp import api
from tests.conftest import _completed, _player

SRC = Path(__file__).resolve().parent.parent / "webapp" / "src" / "app"
CSS = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.css").read_text(encoding="utf-8")
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


async def _two(db):
    a, b = _player(1, "Alice", 1030.0), _player(2, "Bob", 1000.0)
    db.add_all([a, b])
    await db.flush()
    return a, b


async def test_sparkline_walks_back_from_current_rating(db):
    a, b = await _two(db)
    db.add_all([
        _completed(a, b, a.id, 10.0, NOW - timedelta(hours=3)),
        _completed(b, a, b.id, 5.0, NOW - timedelta(hours=2)),
        _completed(a, b, a.id, 25.0, NOW - timedelta(hours=1)),
    ])
    await db.commit()
    sparks = await api._sparklines(db, {a.id: 1030.0, b.id: 1000.0})
    # Алиса: 1030 сейчас; до последнего матча 1005, до него 1010, в начале 1000
    assert sparks[a.id] == [1000.0, 1010.0, 1005.0, 1030.0]
    assert sparks[b.id] == [1030.0, 1020.0, 1025.0, 1000.0]


async def test_sparkline_limited_and_absent_without_matches(db):
    a, b = await _two(db)
    c = _player(3, "Cara")
    db.add(c)
    db.add_all([_completed(a, b, a.id, 1.0, NOW - timedelta(minutes=k + 1)) for k in range(15)])
    await db.commit()
    sparks = await api._sparklines(db, {a.id: 1030.0, b.id: 1000.0, 3: 1000.0})
    assert len(sparks[a.id]) == api.SPARK_POINTS and sparks[a.id][-1] == 1030.0
    assert 3 not in sparks                                   # без матчей — без графика


async def test_leaderboard_rows_have_spark(db):
    a, b = await _two(db)
    db.add(_completed(a, b, a.id, 10.0, NOW - timedelta(hours=1)))
    await db.commit()
    d = await api.leaderboard_payload(db, a.id)
    assert all(isinstance(r["spark"], list) for r in d["rows"])
    assert d["rows"][0]["spark"][-1] == 1030.0


async def test_profile_head_has_rank_and_title_level(db):
    a, b = await _two(db)
    db.add(_completed(a, b, a.id, 10.0, NOW - timedelta(hours=1)))
    await db.commit()
    head = (await api.player_payload(db, a, a))["head"]
    assert head["rank"] == 1 and head["title"] == "Джун"
    assert head["title_level"] == sum(1 for t, _ in RANK_TITLE_BANDS if 1030.0 >= t) == 0


def test_page_wiring_and_reduced_motion():
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    rating = (SRC / "11-rating.js").read_text(encoding="utf-8")
    profile = (SRC / "12-profile.js").read_text(encoding="utf-8")
    assert routes.count("enterScreen(route, quiet);") == 2           # список и профиль (v2.200.0 — через enterScreen)
    assert "app.appendChild(skeletonFor(route));" in routes
    assert "boardFlip(board.list, ids)" in rating and "sparkline(row.spark)" in rating
    assert '" frame frame-" + h.rank' in profile and "celebrateTitle(h)" in profile
    assert "confetti(fresh[0]" in profile
    reduced = CSS.split("v2.182.0: живой дизайн")[1]
    assert "prefers-reduced-motion" in reduced and ".confetti-bit" in reduced
