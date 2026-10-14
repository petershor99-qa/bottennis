"""
v2.177.0: личные рекорды, коронный счёт и главный соперник в профиле приложения,
карточка игрока, прогноз в чужом профиле, история дуэли, летопись и счета клуба,
«Твой месяц», «Поделиться» на экране матча, ошибки JS и время запуска в журнале,
кэш статистики профиля.
Запуск: pytest tests/test_v2177.py
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

import bot.webapp.api as api_mod
import bot.webapp.usage as usage
from bot.db.models import AchievementEarned, AppBoot, ChampionReign, ClubRecordPing, PersonalRecordEarned
from bot.utils import msk_day_start
from bot.webapp.api_chronicle import chronicle_payload, duel_history, scores_payload
from bot.webapp.api_insights import boot_summary
from bot.webapp.api_month import month_payload
from bot.webapp.api_personal import main_rival, personal_bests, personal_payload, signature_score
from bot.webapp.server import USAGE_KEY, create_app
from bot.webapp.share import SHARE_CAPTIONS
from bot.webapp.usage import log_client_error
from tests.conftest import _completed, _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

TODAY = msk_day_start() + timedelta(hours=1)
STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"


def _m(a, b, winner, sets, when, rc=5.0):
    m = _completed(a, b, winner, rc, when)
    m.sets_data = [{"w": w, "l": lo} for w, lo in sets]
    return m


async def _club(db):
    a, b, c = _player(ADMIN, "Алиса", 1100.0), _player(2002, "Боб", 1000.0), _player(3003, "Вика", 1050.0)
    db.add_all([a, b, c])
    await db.flush()
    return a, b, c


# ── Личные рекорды, коронный счёт, соперник ──────────────────────────────────

def test_personal_bests_values():
    a, b = SimpleNamespace(id=1), SimpleNamespace(id=2)
    t = datetime(2026, 10, 1, 9)
    ms = [
        _m(a, b, 1, [(11, 3), (11, 2)], t),
        _m(a, b, 1, [(11, 9)], t + timedelta(hours=1)),
        _m(b, a, 2, [(13, 11), (9, 11), (12, 10)], t + timedelta(days=1)),
        _m(a, b, 1, [(11, 7)], t + timedelta(days=2)),
    ]
    got = {k: (v, text) for k, _title, v, text in personal_bests(ms, 1)}
    assert got["win_streak"] == (2, "2 победы подряд")
    assert got["wins_per_day"] == (2, "2 победы")
    assert got["set_margin"] == (17, "+17 очков")
    assert got["longest_match"] == (3, "3 партии")
    assert got["most_points"][0] == 66
    assert got["streak_vs_opponent"][0] == 2


def test_signature_score_needs_repeats_and_counts_deuce():
    a, b = SimpleNamespace(id=1), SimpleNamespace(id=2)
    t = datetime(2026, 10, 1)
    ms = [_m(a, b, 1, [(11, 7)], t + timedelta(hours=i)) for i in range(3)]
    ms.append(_m(b, a, 2, [(12, 10)], t + timedelta(days=1)))   # проиграл на дьюсе
    sig = signature_score(ms, 1)
    assert sig == {"score": "11:7", "times": "3 раза", "deuce": "25%", "sets": "4 партии"}
    assert signature_score(ms[:2], 1)["score"] == ""             # два раза — ещё не «коронный»
    assert signature_score([], 1) is None


def test_main_rival_most_meetings_then_latest():
    me = 1
    b, c = SimpleNamespace(id=2, display_name="Боб", rating=1000.0), SimpleNamespace(id=3, display_name="Вика", rating=1000.0)
    t = datetime(2026, 10, 1)
    ms = [SimpleNamespace(challenger_id=1, challenged_id=2, winner_id=1, completed_at=t),
          SimpleNamespace(challenger_id=3, challenged_id=1, winner_id=3, completed_at=t + timedelta(days=1)),
          SimpleNamespace(challenger_id=1, challenged_id=3, winner_id=None, completed_at=t + timedelta(days=2))]
    rival = main_rival(ms, me, {2: b, 3: c})
    assert rival["name"] == "Вика" and rival["score"] == "0–1–1" and rival["tone"] == "loss"
    assert main_rival([], me, {}) is None


async def test_personal_payload_dates_from_record_history(db):
    a, b, _ = await _club(db)
    m1 = _m(a, b, a.id, [(11, 3)], TODAY - timedelta(days=3))
    m2 = _m(a, b, a.id, [(11, 4)], TODAY - timedelta(days=2))
    db.add_all([m1, m2])
    db.add(PersonalRecordEarned(player_id=a.id, metric="win_streak", value=2, match_id=None,
                                earned_at=TODAY - timedelta(days=2)))
    await db.commit()
    d = await personal_payload(db, a)
    streak = next(x for x in d["bests"] if x["key"] == "win_streak")
    assert streak["value"] == "2 победы подряд" and "date" in streak
    assert all("date" not in x for x in d["bests"] if x["key"] != "win_streak")
    assert d["rival"]["name"] == "Боб"
    stranger = _player(9, "Пусто")
    db.add(stranger)
    await db.commit()
    assert await personal_payload(db, stranger) == {"bests": [], "signature": None, "rival": None}


# ── История дуэли ────────────────────────────────────────────────────────────

def test_duel_history_balance_streaks_and_best_match():
    a, b = _player(1, "Я"), _player(2, "Боб")
    a.id, b.id = 1, 2
    t = datetime(2026, 9, 1)

    def m(winner, day, sets=((11, 5),), mid=0):
        x = _m(a, b, winner, list(sets), t + timedelta(days=day))
        x.id = mid or day + 1
        x.challenger, x.challenged = a, b
        return x
    ms = [m(1, 0), m(1, 1), m(2, 2), m(2, 3), m(2, 35), m(None, 36), m(1, 37, ((11, 13), (13, 11), (12, 10)))]
    d = duel_history(ms, 1)
    assert [p["v"] for p in d["balance"]] == [1, 2, 1, 0, -1, -1, 0]
    assert d["best_streak_me"] == 2 and d["best_streak_them"] == 3
    assert d["best_match"]["id"] == ms[-1].id
    assert [(x["me"], x["them"]) for x in d["months"]] == [(2, 2), (1, 1)]   # сентябрь, октябрь
    assert duel_history(ms[:1], 1) is None


# ── Летопись и счета клуба ───────────────────────────────────────────────────

async def test_chronicle_events(db):
    a, b, c = await _club(db)
    t = datetime(2026, 7, 1, 10)
    db.add_all([_m(a, b, a.id, [(11, 5)], t), _m(c, a, c.id, [(11, 9)], t + timedelta(days=40))])
    db.add(ChampionReign(player_id=c.id, started_at=t + timedelta(days=41), ended_at=None))
    db.add(ClubRecordPing(kind="streak", player_id=c.id, value=5, pinged_at=t + timedelta(days=42)))
    await db.commit()
    d = await chronicle_payload(db)
    titles = [it["title"] for mo in d["months"] for it in mo["items"]]
    assert titles[0].startswith("Серия побед")
    assert "Вика — первый чемпион клуба" in titles
    assert "Вика: первый матч в клубе" in titles and "Первый матч клуба" in titles
    assert "Боб: первый матч в клубе" not in titles      # первый матч клуба — уже событие
    assert [mo["title"] for mo in d["months"]] == ["Август 2026", "Июль 2026"]


async def test_chronicle_and_scores_empty(db):
    assert (await chronicle_payload(db))["empty"]
    assert (await scores_payload(db))["empty"]


async def test_scores_payload(db):
    a, b, _ = await _club(db)
    t = TODAY - timedelta(days=1)
    db.add_all([_m(a, b, a.id, [(11, 7), (11, 7), (13, 11)], t), _m(b, a, b.id, [(11, 0), (7, 11), (11, 7)], t)])
    await db.commit()
    d = await scores_payload(db)
    assert d["top"][0] == {"score": "11:7", "count": 4, "share": 67}
    facts = {f["label"]: f["value"] for f in d["facts"]}
    assert facts["На дьюсе"] == "17%" and facts["Всухую"] == "1" and facts["Самая длинная"] == "13:11"
    assert d["match_scores"] == [{"score": "3:0", "count": 1}, {"score": "2:1", "count": 1}]


# ── «Твой месяц» ─────────────────────────────────────────────────────────────

async def test_month_payload_cards_and_navigation(db):
    a, b, c = await _club(db)
    now = TODAY
    db.add_all([_m(a, b, a.id, [(11, 5)], now, rc=12.0), _m(c, a, c.id, [(11, 9)], now + timedelta(minutes=5), rc=8.0),
                _m(a, b, a.id, [(11, 8)], now + timedelta(minutes=10), rc=6.0)])
    db.add(AchievementEarned(player_id=a.id, achievement_id="first_blood", earned_at=now))
    await db.commit()
    d = await month_payload(db, a, None)
    kinds = [x["kind"] for x in d["cards"]]
    assert kinds[:4] == ["intro", "rating", "match", "rival"] and "achievements" in kinds and "place" in kinds
    intro = d["cards"][0]
    assert intro["big"] == "3" and intro["lines"][0] == "2 победы · 1 поражение"
    assert d["cards"][1]["big"] == "+10.0"
    assert next(x for x in d["cards"] if x["kind"] == "rival")["player"]["name"] == "Боб"
    assert d["next"] is None and d["label"].endswith(d["ym"][:4])
    assert (await month_payload(db, a, d["prev"]))["empty"].startswith("В ")
    assert await month_payload(db, a, "202613") is None
    assert await month_payload(db, a, "abc") is None
    assert await month_payload(db, a, "209901") is None


async def test_month_falls_back_to_previous_when_nothing_this_month(db):
    a, b, _ = await _club(db)
    old = msk_day_start() - timedelta(days=40)
    db.add(_m(a, b, a.id, [(11, 5)], old))
    await db.commit()
    d = await month_payload(db, b, None)
    # в этом месяце не играл — показывается прошлый месяц, а не пустой текущий
    assert d["ym"] != (TODAY + timedelta(hours=3)).strftime("%Y%m")


# ── Кэш статистики и карточка игрока ─────────────────────────────────────────

async def test_stats_cache_reuses_and_invalidates(db, monkeypatch):
    a, b, _ = await _club(db)
    db.add_all([_m(a, b, a.id, [(11, 5)], TODAY - timedelta(days=i)) for i in range(6)])
    await db.commit()
    api_mod._stats_cache.clear()
    calls = []
    real = api_mod._compute_player_stats
    monkeypatch.setattr(api_mod, "_compute_player_stats", lambda p, m: calls.append(1) or real(p, m))
    first = await api_mod.player_payload(db, a, a)
    await api_mod.player_payload(db, a, a)
    assert len(calls) == 1
    assert first["card"]["stats"][0]["short"] == "ВИН" and len(first["card"]["stats"]) == 6
    db.add(_m(b, a, b.id, [(11, 5)], TODAY + timedelta(minutes=1)))
    await db.commit()
    await api_mod.player_payload(db, a, a)
    assert len(calls) == 2            # новый матч — новый ключ


# ── Ошибки JS и время запуска ────────────────────────────────────────────────

def test_client_error_logged_with_limit(caplog, monkeypatch):
    usage._client_errors.clear()
    clock = [100.0]
    monkeypatch.setattr(usage.time, "monotonic", lambda: clock[0])
    with caplog.at_level(logging.WARNING, logger="bot.webapp.usage"):
        results = [log_client_error(7, {"message": "x is undefined", "where": "app.js:10", "route": "rating",
                                        "platform": "tdesktop", "version": "7.10"}) for _ in range(6)]
        clock[0] += 61
        assert log_client_error(7, {"message": "снова"}) is True
    assert results == [True] * 5 + [False]
    assert "экран «rating»: x is undefined @ app.js:10" in caplog.records[0].getMessage()
    usage._client_errors.clear()


async def test_boot_summary_medians(db):
    db.add_all([AppBoot(ms=1000, platform="tdesktop"), AppBoot(ms=3000, platform="tdesktop"),
                AppBoot(ms=500, platform="android"), AppBoot(ms=9000, platform="",
                                                             created_at=datetime(2020, 1, 1))])
    await db.commit()
    rows = await boot_summary(db)
    assert rows[0] == {"label": "Все клиенты, 7 дней", "value": "1,0 с", "note": "3 запуска"}
    assert {r["label"]: r["value"] for r in rows[1:]} == {"tdesktop": "2,0 с", "android": "0,5 с"}
    assert await boot_summary(SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))) == []


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")
    usage._client_errors.clear()
    async with db_factory() as s:
        a, b, c = await _club(s)
        s.add_all([_m(a, b, a.id, [(11, 5), (11, 7)], TODAY), _m(b, a, b.id, [(11, 9)], TODAY + timedelta(minutes=3))])
        await s.commit()
        ids = {"a": a.id, "b": b.id}
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        c.ids, c.factory = ids, db_factory
        yield c


async def test_new_endpoints(client):
    for path in ("/api/month", "/api/chronicle", "/api/scores", f"/api/h2h/{client.ids['b']}"):
        resp = await client.get(path, headers=client.auth)
        assert resp.status == 200, path
    assert (await client.get("/api/month?ym=19", headers=client.auth)).status == 404
    h2h = await (await client.get(f"/api/h2h/{client.ids['b']}", headers=client.auth)).json()
    assert h2h["duel"]["balance"][-1]["v"] == 0
    other = await (await client.get(f"/api/player/{client.ids['b']}", headers=client.auth)).json()
    own = await (await client.get(f"/api/player/{client.ids['a']}", headers=client.auth)).json()
    assert "forecast" in other and "forecast" not in own
    assert own["personal"]["rival"]["name"] == "Боб"
    home = await (await client.get("/api/home", headers=client.auth)).json()
    routes = [x["route"] for x in home["links"]]
    assert routes[:2] == ["summary/day", "month"] and "chronicle" in routes and "scores" in routes


async def test_track_stores_boot_time_and_client_error_endpoint(client):
    await client.post("/api/track", json={"screen": "home", "boot_ms": 1500, "platform": "tdesktop"},
                      headers=client.auth)
    await client.app[USAGE_KEY].drain()
    async with client.factory() as s:
        rows = (await s.execute(select(AppBoot))).scalars().all()
    assert [(r.ms, r.platform) for r in rows] == [(1500, "tdesktop")]
    ok = await client.post("/api/client_error", json={"message": "boom"}, headers=client.auth)
    assert ok.status == 200 and (await ok.json()) == {"ok": True}
    assert (await client.post("/api/client_error", json={"message": "x"})).status == 401


async def test_usage_command_shows_boot_line(db, monkeypatch):
    from bot.db.models import UsageEvent
    from bot.handlers import admin

    monkeypatch.setattr(admin, "_is_admin", lambda m: True)
    db.add(UsageEvent(user_id=1, action="menu_leaderboard"))
    db.add(AppBoot(ms=1200, platform="android"))
    await db.commit()
    msg = SimpleNamespace(answer=AsyncMock())
    sent = []
    monkeypatch.setattr(admin, "_send", AsyncMock(side_effect=lambda m, text: sent.append(text)))
    await admin.cmd_usage(msg, db)
    assert "🚀 <b>Запуск приложения</b>" in sent[0] and "android: 1,2 с (1 запуск)" in sent[0]


def test_share_match_caption_and_page_features():
    assert SHARE_CAPTIONS["match"].startswith("Матч")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    for needle in ("function renderMonth(", "function renderChronicle(", "function renderScores(",
                   "function duelCard(", "function personalBlocks(", "function cardStats(",
                   'shareButton("match"', "api/client_error", "function nextTab(", "function tableArt("):
        assert needle in js, needle
    assert "Scoreboard-Digits.woff2" in css and ".player-card" in css and ".mood-stamp" in css
    assert (STATIC / "fonts" / "Scoreboard-Digits.woff2").stat().st_size < 10_000
    assert (STATIC / "fonts" / "OFL-Oswald.txt").exists()
