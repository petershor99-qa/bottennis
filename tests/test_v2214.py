"""
v2.214.0: «Твой год» (сторис-карточки с 1 декабря), снег с 1 декабря по 10 января,
«Игрок недели» (значок на аватаре).
Запуск: pytest tests/test_v2214.py
"""
from datetime import date, datetime, timedelta
from pathlib import Path

import bot.webapp.api_month as api_month
import bot.webapp.api_star as api_star
import bot.webapp.api_year as api_year
from tests.test_come_back import _club, _m

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")

DECEMBER = datetime(2026, 12, 5, 9, 0)          # суббота 05.12.2026, 12:00 МСК
OCTOBER = datetime(2026, 10, 15, 9, 0)


def _clock(monkeypatch, now):
    for mod in (api_year, api_month, api_star):
        monkeypatch.setattr(mod, "_now", lambda: now)
    import bot.webapp.api_day as api_day
    monkeypatch.setattr(api_day, "_now", lambda: now)


def test_year_open_and_ready_windows():
    assert not api_year.year_open(date(2026, 11, 30), 2026)
    assert api_year.year_open(date(2026, 12, 1), 2026)
    assert api_year.year_open(date(2026, 3, 1), 2025)
    assert not api_year.year_open(date(2026, 12, 1), 2027)
    assert api_year.ready_year(date(2026, 11, 30)) is None
    assert api_year.ready_year(date(2026, 12, 1)) == 2026
    assert api_year.ready_year(date(2026, 12, 31)) == 2026
    assert api_year.ready_year(date(2027, 1, 10)) == 2026
    assert api_year.ready_year(date(2027, 1, 11)) is None


async def test_year_closed_before_december(db, monkeypatch):
    _clock(monkeypatch, OCTOBER)
    a, b, c = await _club(db)
    db.add(_m(a, b, a.id, [(11, 5), (11, 7)], OCTOBER - timedelta(days=3)))
    await db.commit()
    d = await api_year.year_payload(db, a, "2026")
    assert d["cards"] == [] and d["empty"] == "2026 откроется 1 декабря."
    assert d["next"] is None
    # без года до декабря — прошлый год (здесь в нём матчей нет)
    last = await api_year.year_payload(db, a, None)
    assert last["title"] == "Твой 2025" and last["empty"] == "В 2025 ты ещё не играл."
    assert await api_year.year_ready(db, a) is None


async def test_year_cards_in_december(db, monkeypatch):
    _clock(monkeypatch, DECEMBER)
    a, b, c = await _club(db)
    db.add_all([
        _m(a, b, a.id, [(11, 5), (11, 7)], datetime(2026, 3, 10, 9, 0)),
        _m(a, b, a.id, [(9, 11), (11, 9), (11, 8)], datetime(2026, 3, 11, 9, 0), rc=22.0),
        _m(c, a, c.id, [(11, 3), (11, 4)], datetime(2026, 11, 2, 9, 0)),
        _m(a, c, a.id, [(11, 6), (11, 4)], datetime(2025, 5, 2, 9, 0)),      # прошлый год — не считается
    ])
    await db.commit()
    d = await api_year.year_payload(db, a, None)
    kinds = [c["kind"] for c in d["cards"]]
    assert kinds[0] == "intro" and kinds[1] == "rating" and kinds[-1] == "final" and "outro" in kinds
    assert {"month", "numbers", "match", "rival"} <= set(kinds)
    assert d["title"] == "Твой 2026" and d["base"] == "year" and d["next"] is None and d["prev"] == "2025"
    intro = d["cards"][0]
    assert intro["big"] == "3"
    titles = [c["title"] for c in d["cards"]]
    assert "Лучший месяц" in titles and "Год в цифрах" in titles and "Чаще всего с" in titles
    ready = await api_year.year_ready(db, a)
    assert ready["title"] == "Твой 2026 готов" and ready["route"] == "year/2026"
    old = await api_year.year_payload(db, a, "2025")
    assert old["cards"][0]["big"] == "1" and old["next"] == "2026"
    assert (await api_year.year_payload(db, a, "2027")) is None and (await api_year.year_payload(db, a, "abc")) is None


async def test_year_without_matches_has_text(db, monkeypatch):
    _clock(monkeypatch, DECEMBER)
    a, b, c = await _club(db)
    d = await api_year.year_payload(db, a, None)
    assert d["cards"] == [] and d["empty"] == "В 2026 ты ещё не играл."
    assert await api_year.year_ready(db, a) is None


async def test_week_star_needs_three_matches_and_positive_gain(db, monkeypatch):
    _clock(monkeypatch, datetime(2026, 10, 15, 9, 0))        # четверг; прошлая неделя 5–11 октября
    a, b, c = await _club(db)
    week = datetime(2026, 10, 6, 9, 0)
    db.add_all([
        _m(a, b, a.id, [(11, 5), (11, 7)], week, rc=10.0),
        _m(a, c, a.id, [(11, 5), (11, 7)], week + timedelta(hours=1), rc=12.0),
        _m(a, b, a.id, [(11, 5), (11, 7)], week + timedelta(hours=2), rc=8.0),
        _m(c, b, c.id, [(11, 3), (11, 4)], week + timedelta(hours=3), rc=30.0),     # у Вики только 2 матча
    ])
    await db.commit()
    star = await api_star.week_star(db)
    assert star["id"] == a.id and star["delta"] == "+30.0"


async def test_week_star_none_when_nobody_qualifies(db, monkeypatch):
    _clock(monkeypatch, datetime(2026, 10, 15, 9, 0))
    a, b, c = await _club(db)
    db.add(_m(a, b, a.id, [(11, 5), (11, 7)], datetime(2026, 10, 6, 9, 0), rc=10.0))
    await db.commit()
    assert await api_star.week_star(db) is None


def test_year_route_season_and_badge_wired():
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert "/^year(?:\\/(\\d{4}))?$/" in routes and 'syncSnow(route === "")' in routes
    assert "weekStar = d.week_star" in routes
    month = (SRC / "21-month.js").read_text(encoding="utf-8")
    assert '(d.base || "month")' in month
    core = (SRC / "01-core.js").read_text(encoding="utf-8")
    assert "starBadge(person)" in core
    home = (SRC / "10-home.js").read_text(encoding="utf-8")
    assert "d.year_ready" in home and "m.route ||" in home


def test_snow_styles_respect_reduced_motion():
    assert ".snow i" in CSS and "@keyframes snowfall" in CSS and ".avatar-star" in CSS
    tail = CSS[CSS.index("Сезонное оформление"):]
    assert "prefers-reduced-motion: reduce" in tail and "transform" in tail
    season = (SRC / "28-season.js").read_text(encoding="utf-8")
    assert "!reduceMotion" in season and "snowSeason(new Date())" in season
