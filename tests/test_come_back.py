"""
Пакет 4, релиз 3 (v2.181.0): «Пока тебя не было», «Цели недели» (только в
приложении), предложение иконки клуба на экране телефона.
Запуск: pytest tests/test_come_back.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import bot.webapp.api_return as ret
from bot.db.models import AchievementEarned, ChampionReign, Match, MatchStatus
from bot.webapp.server import create_app
from tests.conftest import _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

SRC = Path(__file__).resolve().parent.parent / "webapp" / "src" / "app"
# Четверг 15.10.2026, 12:00 МСК — середина недели (понедельник 12.10)
NOW = datetime(2026, 10, 15, 9, 0)
MONDAY = datetime(2026, 10, 11, 21, 0)       # 12.10 00:00 МСК в UTC


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(ret, "_now", lambda: NOW)


def _m(a, b, winner, sets, when, rc=10.0):
    """Матч; sets — в перспективе победителя, при ничьей — challenger."""
    return Match(challenger_id=a.id, challenged_id=b.id, status=MatchStatus.completed, winner_id=winner,
                 sets_data=[{"w": w, "l": lo} for w, lo in sets], rating_change=rc, completed_at=when)


async def _club(db):
    a, b, c = _player(ADMIN, "Алиса", 1050.0), _player(2002, "Боб", 1040.0), _player(3003, "Вика", 1000.0)
    db.add_all([a, b, c])
    await db.flush()
    return a, b, c


# ── Пока тебя не было ─────────────────────────────────────────────────────────

async def test_since_shows_place_rating_matches_achievements_champion(db):
    a, b, c = await _club(db)
    since = NOW - timedelta(days=2)
    db.add_all([
        _m(b, a, b.id, [(11, 5)], since - timedelta(days=1), rc=10.0),   # до: Боб выше Алисы
        _m(a, b, a.id, [(11, 7)], since + timedelta(hours=1), rc=12.0),
        _m(a, c, a.id, [(11, 9)], since + timedelta(hours=2), rc=8.0),
        _m(c, a, c.id, [(11, 6)], since + timedelta(hours=3), rc=5.0),
        AchievementEarned(player_id=c.id, achievement_id="first_blood", earned_at=since + timedelta(hours=3)),
        ChampionReign(player_id=a.id, started_at=since + timedelta(hours=4)),
    ])
    await db.commit()
    d = await ret.since_payload(db, a, int(since.replace(tzinfo=timezone.utc).timestamp()))
    items = {i["label"]: i for i in d["items"]}
    assert d["title"] == "Пока тебя не было" and d["since"] == "с 13.10, 12:00"
    assert items["Твой рейтинг"] == {"label": "Твой рейтинг", "value": "+15.0", "note": "2 победы, 1 поражение"}
    assert items["Матчей в клубе"]["value"] == "3"
    assert items["Новые ачивки"] == {"label": "Новые ачивки", "value": "1", "note": "Вика"}
    assert items["Новый чемпион"]["value"] == "Алиса"
    assert "Твоё место" in items


async def test_since_nothing_happened_and_bad_time(db):
    a, *_ = await _club(db)
    await db.commit()
    ts = int((NOW - timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())
    assert (await ret.since_payload(db, a, ts))["items"] == []
    future = int((NOW + timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())
    assert await ret.since_payload(db, a, future) is None


async def test_since_looks_back_at_most_30_days(db):
    a, b, _ = await _club(db)
    db.add(_m(a, b, a.id, [(11, 5)], NOW - timedelta(days=40)))
    await db.commit()
    d = await ret.since_payload(db, a, 1)                      # «с 1970 года»
    assert d["items"] == [] and d["since"].startswith("с 15.09")


# ── Цели недели ───────────────────────────────────────────────────────────────

def test_week_starts_monday_msk():
    assert ret.week_start(NOW) == MONDAY
    assert ret.week_start(MONDAY) == MONDAY
    assert ret.week_start(MONDAY - timedelta(seconds=1)) == MONDAY - timedelta(weeks=1)


async def test_goals_from_history_and_progress_this_week(db):
    a, b, c = await _club(db)
    # 4 недели до понедельника — по 4 матча: цель «Сыграй 5 матчей»
    for w in range(4):
        for k in range(4):
            db.add(_m(a, c, a.id, [(11, 5)], MONDAY - timedelta(weeks=w, hours=k + 2)))
    db.add(_m(b, a, b.id, [(11, 9)], MONDAY - timedelta(hours=1)))          # последний проигрыш — Бобу
    db.add_all([
        _m(a, b, a.id, [(9, 11), (11, 8), (12, 10)], MONDAY + timedelta(days=1)),  # реванш + камбэк + дьюс
        _m(a, c, a.id, [(11, 4)], MONDAY + timedelta(days=2)),
    ])
    await db.commit()
    g = await ret.goals_payload(db, a)
    goals = {x["key"]: x for x in g["goals"]}
    assert goals["matches"]["title"] == "Сыграй 5 матчей" and goals["matches"]["progress"] == 2
    assert goals["revenge"]["title"] == "Реванш: Боб" and goals["revenge"]["done"] is True
    assert goals["revenge"]["player_id"] == b.id
    assert len(g["goals"]) == 3 and g["left"] == "осталось 3 дня"
    skill = g["goals"][2]
    week_no = int((MONDAY + timedelta(hours=3)).strftime("%V"))
    assert skill["key"] == ret.SKILLS[week_no % 3][0]


async def test_goals_without_losses_ask_for_wins_and_cap_progress(db):
    a, _, c = await _club(db)
    db.add_all([_m(a, c, a.id, [(11, 5)], MONDAY + timedelta(hours=h + 1)) for h in range(8)])
    await db.commit()
    goals = {x["key"]: x for x in (await ret.goals_payload(db, a))["goals"]}
    assert "revenge" not in goals and goals["wins"]["title"] == "Выиграй 2 матча"
    assert goals["matches"]["progress"] == goals["matches"]["target"] == 3 and goals["matches"]["done"]


def test_skill_counters():
    a, b = _player(1, "A"), _player(2, "B")
    a.id, b.id = 1, 2
    m = _m(a, b, 1, [(9, 11), (12, 10), (13, 11)], NOW)
    assert ret._deuce_set_wins(m, 1) == 2 and ret._deuce_set_wins(m, 2) == 0
    assert ret._comeback(m, 1) and not ret._converted(m, 1)
    lost = _m(a, b, 2, [(11, 5), (11, 7)], NOW)                 # B выиграл, sets — в перспективе B
    assert ret._sets(lost, 1) == [(5, 11), (7, 11)]


async def test_streak_counts_fully_closed_weeks(db, monkeypatch):
    a, b, c = await _club(db)
    await db.commit()
    done_weeks = {MONDAY - timedelta(weeks=1), MONDAY - timedelta(weeks=2)}

    def fake(history, week, pid, start, names):
        ok = start in done_weeks
        return [{"key": "x", "title": "x", "target": 1, "progress": int(ok), "done": ok}]

    monkeypatch.setattr(ret, "_goals_for_week", fake)
    db.add(_m(a, b, a.id, [(11, 5)], MONDAY - timedelta(weeks=5)))
    await db.commit()
    assert (await ret.goals_payload(db, a))["streak"] == 2


# ── HTTP и страница ───────────────────────────────────────────────────────────

@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    async with db_factory() as s:
        a, b, _ = await _club(s)
        s.add(_m(a, b, a.id, [(11, 5)], NOW - timedelta(hours=1)))
        await s.commit()
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        yield c


async def test_home_has_goals_and_since_route(client):
    home = await (await client.get("/api/home", headers=client.auth)).json()
    assert home["goals"]["title"] == "Цели недели" and len(home["goals"]["goals"]) == 3
    ts = int((NOW - timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())
    since = await (await client.get(f"/api/since?t={ts}", headers=client.auth)).json()
    assert since["title"] == "Пока тебя не было"


@pytest.mark.parametrize("t", ["", "abc", "0", "-5"])
async def test_since_bad_time_404(client, t):
    assert (await client.get(f"/api/since?t={t}", headers=client.auth)).status == 404


def test_page_wiring():
    home = (SRC / "10-home.js").read_text(encoding="utf-8")
    part = (SRC / "23-return.js").read_text(encoding="utf-8")
    assert "if (d.goals) c.appendChild(goalsBlock(d.goals));" in home
    assert "checkSince(c);" in home and "homeIconOffer(c);" in home
    assert "if (sinceChecked) return;" in part and "SINCE_MIN_GAP_MS = 2 * 3600 * 1000" in part
    assert 'status !== "missed"' in part and 'isVersionAtLeast("8.0")' in part
    assert "homeScreenAdded" in part
