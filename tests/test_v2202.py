"""
v2.202.0: «Итоги» недели, месяца, квартала и года на лету (листание, табло со
сравнением, тумба, «Твоя неделя», герои карточками, матч периода) и «Твой месяц»
(календарь, лучший и худший день, цифры, характер, прирост в клубе, рекорды,
цели недели, «На ноябрь», финальная карточка, автолистание), «Твой октябрь
готов» на главной.
Запуск: pytest tests/test_v2202.py
"""
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.webapp.api_month as api_month
import bot.webapp.api_period as api_period
import bot.webapp.api_return as ret
from bot.db.models import PersonalRecordEarned
from bot.webapp.share import SHARE_CAPTIONS
from tests.test_come_back import _club, _m

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
NOW = datetime(2026, 10, 15, 9, 0)            # четверг 15.10.2026, 12:00 МСК
MONDAY = datetime(2026, 10, 11, 21, 0)         # 12.10 00:00 МСК


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    for mod in (api_period, api_month, ret):
        monkeypatch.setattr(mod, "_now", lambda: NOW)


def test_period_bounds_and_labels():
    d = date(2026, 10, 15)
    assert api_period.period_bounds("week", d) == (date(2026, 10, 12), date(2026, 10, 19))
    assert api_period.period_bounds("month", d) == (date(2026, 10, 1), date(2026, 11, 1))
    assert api_period.period_bounds("quarter", d) == (date(2026, 10, 1), date(2027, 1, 1))
    assert api_period.period_bounds("year", d) == (date(2026, 1, 1), date(2027, 1, 1))
    assert api_period.period_label("week", date(2026, 10, 12), date(2026, 10, 19)) == "Неделя 12–18 октября"
    assert api_period.period_label("week", date(2026, 9, 28), date(2026, 10, 5)) == "Неделя 28 сентября – 4 октября"
    assert api_period.period_label("month", date(2026, 10, 1), date(2026, 11, 1)) == "Октябрь 2026"
    assert api_period.period_label("quarter", date(2026, 7, 1), date(2026, 10, 1)) == "III квартал 2026"


async def test_week_payload_board_standings_heroes_you(db):
    a, b, c = await _club(db)
    db.add_all([
        _m(a, b, a.id, [(11, 5), (11, 7)], MONDAY + timedelta(hours=10)),
        _m(a, b, a.id, [(9, 11), (11, 9), (11, 8)], MONDAY + timedelta(hours=11), rc=22.0),
        _m(c, b, c.id, [(11, 3)], MONDAY + timedelta(hours=12)),
        _m(a, c, c.id, [(11, 6), (11, 4)], MONDAY - timedelta(days=3)),     # прошлая неделя
    ])
    await db.commit()
    d = await api_period.period_payload(db, a, "week", None)
    assert d["label"] == "Неделя 12–18 октября" and d["prev"] == "2026-10-05" and d["next"] is None
    assert d["board"][0] == {"label": "Матчей", "value": "3", "delta": "+1100%"}
    assert d["compare_note"] == "к среднему за 4 недели"
    assert [r["name"] for r in d["standings"]] == ["Алиса", "Вика", "Боб"]
    assert d["standings"][0]["record"] == "2–0 (100%)" and d["standings"][0]["form"] == ["w", "w"]
    keys = {h["key"]: h for h in d["heroes"]}
    assert keys["active"]["title"] == "Главный теннисист недели" and keys["active"]["players"][0]["name"] in ("Алиса", "Боб")
    assert keys["derby"]["players"][0]["name"] == "Алиса" and keys["derby"]["value"] == "2 матча"
    assert keys["streak"]["title"] == "Нагибатель недели"
    assert d["heroes_title"] == "Герои недели"
    assert d["match"]["title"] == "Матч недели"
    assert d["you"]["title"] == "Твоя неделя" and "2 победы · 0 поражений" in d["you"]["lines"]


async def test_period_navigation_and_empty(db):
    a, b, _ = await _club(db)
    db.add(_m(a, b, a.id, [(11, 5)], MONDAY - timedelta(days=40)))
    await db.commit()
    # в текущей неделе матчей нет — показывается прошлая
    d = await api_period.period_payload(db, a, "week", None)
    assert d["label"] == "Неделя 5–11 октября" and d["empty"] == "За этот период матчей не было."
    month = await api_period.period_payload(db, a, "month", "2026-09-05")
    assert month["label"] == "Сентябрь 2026" and month["board"][0]["value"] == "1"
    assert month["you"]["title"] == "Твой сентябрь" and month["you"]["story"] == "202609"
    assert await api_period.period_payload(db, a, "week", "2027-01-01") is None   # будущее
    assert await api_period.period_payload(db, a, "decade", None) is None


def _mm(sets, winner=1, boss=False, n=0):
    return SimpleNamespace(sets_data=[{"w": w, "l": lo} for w, lo in sets], winner_id=winner, is_boss_fight=boss,
                           id=n, challenger_id=1, challenged_id=2)


def test_match_mood_and_month_character():
    assert api_month.match_mood(_mm([(6, 11), (8, 11), (11, 5), (11, 4), (11, 9)])) == "comeback"
    assert api_month.match_mood(_mm([(6, 11), (11, 5), (11, 4)])) == "rally"
    assert api_month.match_mood(_mm([(11, 2), (11, 3)])) == "blowout"
    assert api_month.match_mood(_mm([(12, 10), (13, 11)])) == "deuce"
    assert api_month.match_mood(_mm([(11, 9)], boss=True)) == "boss"
    deuces = [_mm([(12, 10), (13, 11)]) for _ in range(4)]
    plain = [_mm([(11, 9), (11, 9)]) for _ in range(5)]
    assert api_month.month_character(deuces + plain, 1) == ("deuce", 4)
    assert api_month.month_character(deuces[:3] + plain, 1) is None              # меньше 4
    assert api_month.month_character(deuces + plain * 2, 1) is None              # не больше трети


async def test_month_cards(db):
    a, b, c = await _club(db)
    oct1 = datetime(2026, 9, 30, 21, 0)       # 01.10 00:00 МСК
    db.add_all([
        _m(a, b, a.id, [(11, 5), (11, 7)], oct1 + timedelta(days=1, hours=10), rc=15.0),
        _m(a, b, b.id, [(11, 5)], oct1 + timedelta(days=2, hours=10), rc=9.0),
        _m(a, c, a.id, [(12, 10), (11, 7)], oct1 + timedelta(days=5, hours=10), rc=8.0),
    ])
    db.add(PersonalRecordEarned(player_id=a.id, metric="win_streak", value=2, earned_at=oct1 + timedelta(days=5, hours=10)))
    await db.commit()
    d = await api_month.month_payload(db, a, "202610")
    cards = {c["kind"]: c for c in d["cards"]}
    cal = cards["calendar"]
    assert cal["offset"] == 3 and len(cal["days"]) == 31 and cal["days"][1] == "w" and cal["days"][2] == "l"
    assert cal["lines"] == ["Игровых дней: 3"]
    assert "Дьюсов выиграно: 1 из 1" in cards["numbers"]["lines"]
    day_titles = [c["title"] for c in d["cards"] if c["kind"] == "day"]
    assert day_titles == ["Лучший день", "День, который лучше забыть"]
    assert next(c for c in d["cards"] if c["kind"] == "day")["route"] == "summary/day/20261002"
    assert cards["gain"]["big"] == "#1" and cards["gain"]["lines"] == ["из 3 по приросту рейтинга"]
    assert cards["records"]["lines"] == ["Серия побед: 2"]
    assert cards["goals"]["big_label"] == "недель закрыто"
    assert cards["next"]["title"] == "На ноябрь"
    final = d["cards"][-1]
    assert final["kind"] == "final" and final["title"] == "Твой октябрь"
    assert [s[0] for s in final["stats"]][:3] == ["Матчей", "Побед", "Рейтинг"]


async def test_month_ready_first_days(db, monkeypatch):
    a, b, _ = await _club(db)
    db.add(_m(a, b, a.id, [(11, 5)], datetime(2026, 10, 20, 9, 0)))
    await db.commit()
    assert await api_month.month_ready(db, a) is None                              # 15-е число
    monkeypatch.setattr(api_month, "_now", lambda: datetime(2026, 11, 2, 9, 0))
    m = await api_month.month_ready(db, a)
    assert m == {"ym": "202610", "title": "Твой октябрь готов",
                 "sub": "Месяц в карточках — посмотри, как он прошёл"}
    assert await api_month.month_ready(db, b) is not None
    _, _, c = await _club_extra(db)
    assert await api_month.month_ready(db, c) is None


async def _club_extra(db):
    from tests.conftest import _player
    c = _player(7007, "Гоша")
    db.add(c)
    await db.flush()
    return None, None, c


async def test_closed_weeks_counts_full_weeks(db):
    a, b, _ = await _club(db)
    await db.commit()
    closed, total = await ret.closed_weeks(db, a, datetime(2026, 9, 30, 21, 0), datetime(2026, 10, 31, 21, 0))
    assert total == 1 and closed == 0            # 05.10–11.10 закончилась, 12.10 ещё идёт


def test_share_kinds_and_page_wiring():
    assert "period" in SHARE_CAPTIONS and "month" in SHARE_CAPTIONS
    summary = (SRC / "15-summary.js").read_text(encoding="utf-8")
    assert "function renderPeriod(c, d, kind)" in summary and 'shareButton("period"' in summary
    assert 'go("month/" + you.story)' in summary and '"race"' in summary
    month = (SRC / "21-month.js").read_text(encoding="utf-8")
    assert "var STORY_MS = 5000;" in month and "Удержи — пауза. Справа — дальше, слева — назад" in month
    assert 'shareButton("month"' in month and "storyCalendar(card)" in month
    home = (SRC / "10-home.js").read_text(encoding="utf-8")
    assert "if (d.month_ready) monthReady(c, podiumNode, d.month_ready);" in home
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert 'api("summary/" + m[1] + (date ? "?date=" + date : ""))' in routes


def test_story_hold_does_not_swallow_next_tap_and_day_tabs_active():
    # v2.202.1: после удержания пальца следующий тап листает; вкладки итогов не бледные
    month = (SRC / "21-month.js").read_text(encoding="utf-8")
    assert "if (suppress) { suppress = false; return; }" in month and "held = false;" in month
    server = (ROOT / "bot" / "webapp" / "server.py").read_text(encoding="utf-8")
    assert 'tab["available"] = True' in server
