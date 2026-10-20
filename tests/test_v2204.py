"""
v2.204.0: исправления по проверке 20.10 (docs/CHECK_2026-10-20.md) — итоги
незаконченного периода сравниваются с тем же сроком прошлых, слово в плитке не
рвётся, длинные имена и названия ачивок не вылезают, индекс легенды — среди
игравших, «Твой месяц» у того, кто ещё не играл, пустые «Script error» не
шлются, «Ты против себя» ведёт в профиль, «Кто вёл» без «пилы».
Запуск: pytest tests/test_v2204.py
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import bot.webapp.api_month as api_month
import bot.webapp.api_period as api_period
from bot.services.stats import _legend_index_with_rank
from tests.conftest import _player
from tests.test_come_back import _club, _m

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 13, 9, 0)            # вторник 13.10.2026, 12:00 МСК
MONDAY = datetime(2026, 10, 11, 21, 0)         # 12.10 00:00 МСК


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    for mod in (api_period, api_month):
        monkeypatch.setattr(mod, "_now", lambda: NOW)


async def test_week_in_progress_compared_with_same_span(db):
    a, b, _ = await _club(db)
    # в текущей неделе к вторнику полудню — 2 матча
    db.add_all([_m(a, b, a.id, [(11, 5)], MONDAY + timedelta(hours=11)),
                _m(a, b, b.id, [(5, 11)], MONDAY + timedelta(hours=33))])
    # в каждой из 4 прошлых недель: 2 матча до вторника полудня и 8 — позже
    for w in range(1, 5):
        ws = MONDAY - timedelta(days=7 * w)
        for h in (10, 30):
            db.add(_m(a, b, a.id, [(11, 7)], ws + timedelta(hours=h)))
        for k in range(8):
            db.add(_m(a, b, b.id, [(7, 11)], ws + timedelta(days=3, hours=k)))
    await db.commit()
    d = await api_period.period_payload(db, a, "week", None)
    # раньше: 2 против среднего 10 за целую неделю — «−80%»; теперь — к тому же сроку: 2 против 2
    assert d["board"][0] == {"label": "Матчей", "value": "2", "delta": "0%"}
    assert d["compare_note"] == "к тому же сроку 4 прошлых недель"


async def test_finished_week_compared_with_full_weeks(db):
    a, b, _ = await _club(db)
    prev = MONDAY - timedelta(days=7)
    db.add_all([_m(a, b, a.id, [(11, 5)], prev + timedelta(days=1)),
                _m(a, b, a.id, [(11, 5)], prev - timedelta(days=3))])
    await db.commit()
    d = await api_period.period_payload(db, a, "week", "2026-10-06")
    assert d["label"] == "Неделя 5–11 октября" and d["compare_note"] == "к среднему за 4 недели"


async def test_month_in_progress_compared_with_same_days(db):
    a, b, _ = await _club(db)
    db.add_all([_m(a, b, a.id, [(11, 5)], datetime(2026, 10, 2, 9)),
                _m(a, b, a.id, [(11, 5)], datetime(2026, 9, 3, 9)),
                _m(a, b, a.id, [(11, 5)], datetime(2026, 9, 25, 9))])     # позже 13 сентября — не в счёт
    await db.commit()
    d = await api_period.period_payload(db, a, "month", None)
    assert d["board"][0]["delta"] == "0%" and d["compare_note"] == "к тому же сроку прошлого месяца"


async def test_month_story_for_player_without_matches(db):
    a, b, _ = await _club(db)
    newbie = _player(9009, "Новичок")
    db.add(newbie)
    db.add(_m(a, b, a.id, [(11, 5)], MONDAY))
    await db.commit()
    d = await api_month.month_payload(db, newbie, None)
    assert d["title"] == "Твой октябрь" and d["cards"] == [] and d["next"] is None
    assert d["empty"].startswith("Ты ещё не сыграл ни одного матча.")
    # сыгравший, но не в этом месяце — по-прежнему прошлый месяц
    db.add(_m(newbie, b, newbie.id, [(11, 5)], datetime(2026, 9, 10, 9)))
    await db.commit()
    assert (await api_month.month_payload(db, newbie, None))["title"] == "Твой сентябрь"


async def test_legend_rank_only_among_players_with_matches(db):
    a, b, c = await _club(db)
    idle = _player(9009, "Без матчей")
    db.add(idle)
    db.add_all([_m(a, b, a.id, [(11, 5)], MONDAY), _m(c, b, c.id, [(11, 5)], MONDAY)])
    await db.commit()
    players = [a, b, c, idle]
    _, _, total = await _legend_index_with_rank(db, a, players)
    assert total == 3
    _, rank, total = await _legend_index_with_rank(db, idle, players)   # свой профиль без матчей — себя считает
    assert total == 4 and rank == 4


def test_client_fixes_wiring():
    core = (SRC / "01-core.js").read_text(encoding="utf-8")
    assert "function fitWord(node, minPx)" in core and 'n.classList.add("is-word"); fitWord(n);' in core
    assert ".tile-value.is-word { overflow-wrap: normal; word-break: normal; white-space: nowrap; }" in CSS
    assert ".hero-name, .versus-name, .hero-tile .rec-name, .quick-who .sheet-title { overflow-wrap: anywhere; }" in CSS
    assert ".badge-name { hyphens: auto;" in CSS
    assert "if (isOpaqueScriptError(e.message, e.filename)) return;" in (SRC / "02-api.js").read_text(encoding="utf-8")
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert "var selfPair = selfVersusTarget(route, viewerId);" in routes
    assert "points = thinPoints(points, 60);" in (SRC / "13-matches.js").read_text(encoding="utf-8")
