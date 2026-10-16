"""
v2.201.0: исправления по обходу приложения (прокрутка по записи истории, узкий
телефон, «0.0», «Отыгрался», «в N раза»), анимации (рейтинг с прошлого захода,
подиум, огонь серии, прыжок победителя, конфетти апсета) и новое (точка на «Я»,
отметки на графике, своя цель на мастерство, «Лучший день»).
Запуск: pytest tests/test_v2201.py
"""
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

import bot.webapp.api_return as ret
from bot.db.models import AchievementEarned, ChampionReign, PersonalRecordEarned
from bot.webapp import api, api_club
from bot.webapp.api_personal import best_day, chart_marks
from bot.webapp.server import create_app
from tests.test_come_back import ADMIN, NOW, TOKEN, _clock, _club, _init_data, _m  # noqa: F401 — фикстура часов

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")


def test_signed_zero_has_no_sign():
    assert api._signed(0) == "0.0" and api._signed(-0.04) == "0.0"
    assert api._signed(12.34) == "+12.3" and api._signed(-5) == "−5.0"


def _mm(pid, opp, winner, rc, when):
    return SimpleNamespace(challenger_id=pid, challenged_id=opp, winner_id=winner, rating_change=rc,
                           completed_at=when, is_boss_fight=False)


def test_best_day_picks_biggest_gain_with_note():
    day1, day2 = NOW - timedelta(days=3), NOW - timedelta(days=1)
    ms = [_mm(1, 2, 1, 10.0, day1), _mm(1, 2, 1, 12.0, day1 + timedelta(hours=1)),
          _mm(1, 2, 2, 9.0, day1 + timedelta(hours=2)), _mm(1, 2, 1, 8.0, day2)]
    d = best_day(ms, 1)
    assert d["delta"] == "+13.0" and d["note"] == "3 матча · 2 победы"
    assert d["ymd"] == (day1 + timedelta(hours=3)).strftime("%Y%m%d")
    assert best_day([_mm(1, 2, 1, 3.0, day1)], 1) is None          # меньше BEST_DAY_MIN — не показываем


async def test_chart_marks_attach_events_to_match_points(db):
    a, b, _ = await _club(db)
    m1 = _m(a, b, a.id, [(11, 5)], NOW - timedelta(days=2))
    m2 = _m(a, b, a.id, [(11, 7)], NOW - timedelta(days=1))
    db.add_all([m1, m2])
    await db.flush()
    db.add_all([
        AchievementEarned(player_id=a.id, achievement_id="first_blood", earned_at=m1.completed_at),
        ChampionReign(player_id=a.id, started_at=m2.completed_at),
        PersonalRecordEarned(player_id=a.id, metric="win_streak", value=2, match_id=m2.id, earned_at=m2.completed_at),
    ])
    await db.commit()
    marks = await chart_marks(db, a.id, [m1, m2])
    assert [mk["i"] for mk in marks] == [0, 1]
    assert marks[1]["kind"] == "throne" and "Взял трон" in marks[1]["texts"]
    assert "Личный рекорд: Серия побед" in marks[1]["texts"]


async def test_choose_skill_once_per_week(db):
    a, b, _ = await _club(db)
    db.add(_m(a, b, a.id, [(11, 5)], NOW - timedelta(days=10)))
    await db.commit()
    g = await ret.goals_payload(db, a)
    skill = next(x for x in g["goals"] if x.get("skill"))
    assert len(g["skill_options"]) == len(ret.SKILLS) - 1
    assert all(o["key"] != skill["key"] for o in g["skill_options"])
    pick = g["skill_options"][0]["key"]
    after = await ret.choose_skill(db, a, pick)
    chosen = next(x for x in after["goals"] if x.get("skill"))
    assert chosen["key"] == pick and chosen["note"] == ret.CHOSEN_NOTE
    assert after["skill_options"] == []
    assert await ret.choose_skill(db, a, g["skill_options"][1]["key"]) is None   # второй раз — нельзя
    assert await ret.choose_skill(db, b, "nope") is None


async def test_match_upset_flag(db):
    a, b, _ = await _club(db)
    big = _m(a, b, b.id, [(11, 5), (11, 7)], NOW - timedelta(hours=2), rc=24.0)
    small = _m(a, b, a.id, [(11, 5), (11, 7)], NOW - timedelta(hours=1), rc=8.0)
    db.add_all([big, small])
    await db.commit()
    assert (await api_club.match_payload(db, big.id))["upset"] is True
    assert (await api_club.match_payload(db, small.id))["upset"] is False


async def test_leaderboard_and_home_carry_streak_and_awards(db):
    a, b, _ = await _club(db)
    for i in range(3):
        db.add(_m(a, b, a.id, [(11, 5)], NOW - timedelta(hours=5 - i)))
    await db.commit()
    lb = await api.leaderboard_payload(db, a.id)
    assert next(r for r in lb["rows"] if r["player_id"] == a.id)["streak"] == 3
    home = await api.home_payload(db, a)
    assert home["podium"][0]["streak"] >= 0
    assert isinstance(home["me"]["achievements"], list) and home["me"]["record_ts"] == 0


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    async with db_factory() as s:
        a, b, _ = await _club(s)
        s.add(_m(a, b, a.id, [(11, 5)], NOW - timedelta(days=10)))
        await s.commit()
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        yield c


async def test_goals_skill_endpoint(client):
    home = await (await client.get("/api/home", headers=client.auth)).json()
    key = home["goals"]["skill_options"][0]["key"]
    r = await client.post("/api/goals/skill", json={"key": key}, headers=client.auth)
    assert r.status == 200 and (await r.json())["goals"]["skill_options"] == []
    again = await client.post("/api/goals/skill", json={"key": key}, headers=client.auth)
    assert again.status == 409


def test_page_wiring():
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert 'history.scrollRestoration = "manual"' in routes and "history.replaceState({ navId: id }" in routes
    home = (SRC / "10-home.js").read_text(encoding="utf-8")
    assert 'mood !== "rally"' in home and "podiumMoves(podiumNode, d.podium);" in home
    assert "me.appendChild(meRating(me, d.me.rating));" in home and "checkMeDot(d.me);" in home
    assert "hotAvatar(row" in (SRC / "11-rating.js").read_text(encoding="utf-8")
    assert "winnerJump(row, d, replay);" in (SRC / "20-match.js").read_text(encoding="utf-8")
    assert '"chart-mark mark-" + mk.kind' in (SRC / "03-charts.js").read_text(encoding="utf-8")
    part = (SRC / "23-return.js").read_text(encoding="utf-8")
    assert 'post("goals/skill", { key: o.key })' in part and 'var REC_SEEN_KEY = "seen_record_ts_v1";' in part
    assert '"summary/day/" + bd.ymd' in (SRC / "12-profile.js").read_text(encoding="utf-8")
    for rule in (".tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); }", ".avatar.is-hot",
                 ".tab.has-dot::after", ".podium-stand { max-width: 440px;"):
        assert rule in CSS


def test_scroll_to_top_survives_mobile_chrome():
    # v2.201.1: без «якоря прокрутки», наверх до каркаса загрузки и повтор после кадра
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert "if (!nav.restore) window.scrollTo(0, 0);" in routes
    assert "renderedNav === id && Math.abs(window.scrollY - target) > 2" in routes
    assert "scrollPos[currentNav.id] = window.scrollY;" in routes          # снимок главной
    assert "overflow-anchor: none;" in CSS
