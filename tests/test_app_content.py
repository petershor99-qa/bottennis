"""
Релиз 3а пакета «Бот + Mini App 2» (v2.168.0): короткие сводки + экран
«Итоги», лента клуба, экран матча, рейтинг за период, график, ачивки
(редкость, даты, ближайшие). Запуск: pytest tests/test_app_content.py
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

import bot.scheduler as scheduler
from bot.db.models import AchievementEarned, ChampionReign, ClubRecordPing, DigestSnapshot, Match, MatchStatus
from bot.services.digest_delivery import deliver_digest, save_snapshot, short_part
from bot.services.stats import _compute_player_stats, _nearest_achievement_progress, nearest_achievements
from bot.utils import get_career_matches, msk_day_start
from bot.webapp import api_club
from bot.webapp.server import create_app
from bot.webapp.text import plain
from tests.conftest import _completed, _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

# «Сейчас», но не раньше часа после полуночи МСК: события «N минут назад» в
# первый час суток иначе уехали бы во «вчера» (CI, запущенный в 00:00 МСК, падал).
# Запросы «сегодня» ограничены только снизу, событие чуть в будущем им не мешает.
NOW = max(datetime.now(timezone.utc).replace(tzinfo=None), msk_day_start() + timedelta(hours=1))

DAY_TEXT = (
    "📅 <b>Итоги дня — 13.10</b>\n\n"
    "⚡ Сыграно: <b>4 матча</b>, <b>11 партий</b>, <b>209 очков</b>\n\n"
    "🏆 <b>Топ дня:</b>\n"
    "🥇 <b>Боб &amp; Ко</b> — 2–0  🟩🟩\n"
    "🥈 <b>Вика</b> — 1–1  🟩🟥\n"
    "🥉 <b>Алиса</b> — 1–0  🟩\n"
    "4. <b>Гоша</b> — 0–2  🟥🟥\n\n"
    "📈 Лучший рост: <b>Боб</b> (+20.1 pts)\n\n"
    "📋 <b>Все матчи:</b>\n<b>Боб</b> vs Вика  <i>11:7, 11:9</i>"
)
WEEK_CLUB = (
    "🏆 <b>Топ недели:</b>\n🥇 <b>Боб</b> — 5–1\n🥈 <b>Вика</b> — 3–3\n\n"
    "🦸 <b>Герои недели:</b>\n📈 Лучший рост: <b>Боб</b>"
)


@pytest.fixture
def app_on(monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.delenv("WEBAPP_AUDIENCE", raising=False)  # приложение только у админа


# ── Короткие сводки ───────────────────────────────────────────────────────────

def test_short_part_stops_after_ranking():
    short = short_part(DAY_TEXT)
    assert short.endswith("🟥🟥")  # таблица целиком
    assert "Лучший рост" not in short and "Все матчи" not in short
    assert short.startswith("📅 <b>Итоги дня")


def test_short_part_week_club_block_is_ranking_only():
    assert short_part(WEEK_CLUB).startswith("🏆 <b>Топ недели:</b>")
    assert "Герои" not in short_part(WEEK_CLUB)


def test_short_part_without_ranking_takes_two_paragraphs():
    assert short_part("a\n\nb\n\nc") == "a\n\nb"


async def test_deliver_digest_with_app_sends_short_and_button(app_on):
    bot = SimpleNamespace(send_message=AsyncMock())
    await deliver_digest(bot, SimpleNamespace(telegram_id=ADMIN), "day", "ПОЛНЫЙ", "КОРОТКИЙ")
    args, kwargs = bot.send_message.call_args
    assert args[1] == "КОРОТКИЙ"
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "📊 Итоги дня в приложении"
    assert button.web_app.url == "https://club.example/?r=summary%2Fday"


async def test_deliver_digest_without_app_sends_full(app_on):
    bot = SimpleNamespace(send_message=AsyncMock())
    await deliver_digest(bot, SimpleNamespace(telegram_id=2002), "week", "ПОЛНЫЙ", "КОРОТКИЙ")
    args, kwargs = bot.send_message.call_args
    assert args[1] == "ПОЛНЫЙ" and "reply_markup" not in kwargs


async def test_daily_summary_saves_snapshot_and_shortens_for_app_users(db_factory, monkeypatch, app_on):
    monkeypatch.setattr(scheduler, "async_session", db_factory)
    async with db_factory() as s:
        a, b = _player(ADMIN, "Алиса"), _player(2002, "Боб")
        s.add_all([a, b])
        await s.flush()
        s.add(_completed(a, b, a.id, 12.0, NOW - timedelta(minutes=5)))
        await s.commit()
    bot = SimpleNamespace(send_message=AsyncMock())
    await scheduler.send_daily_summary(bot)
    sent = {c.args[0]: c for c in bot.send_message.call_args_list}
    assert "reply_markup" in sent[ADMIN].kwargs          # админ: коротко + кнопка
    assert "Все матчи" not in sent[ADMIN].args[1]
    assert "Все матчи" in sent[2002].args[1]             # без приложения — полностью
    async with db_factory() as s:
        snaps = (await s.execute(select(DigestSnapshot))).scalars().all()
    assert [x.kind for x in snaps] == ["day"] and "Все матчи" in snaps[0].text


async def test_snapshot_failure_does_not_break(db):
    class Broken:
        def add(self, _):
            raise RuntimeError("нет базы")

        async def rollback(self):
            return None

    await save_snapshot(Broken(), "day", "x")  # не падает


# ── Экран «Итоги» ─────────────────────────────────────────────────────────────

def test_parse_summary_day():
    data = api_club.parse_summary(DAY_TEXT, "day")
    assert data["title"] == "Итоги дня — 13.10"
    assert data["played"] == ["Сыграно: 4 матча, 11 партий, 209 очков"]
    assert data["standings_title"] == "Топ дня"
    first = data["standings"][0]
    assert first == {"place": 1, "name": "Боб & Ко", "record": "2–0", "form": ["w", "w"]}
    assert [r["place"] for r in data["standings"]] == [1, 2, 3, 4]
    assert data["groups"]


def test_parse_summary_week_uses_kind_title():
    data = api_club.parse_summary(WEEK_CLUB, "week")
    assert data["title"] == "Итоги недели"
    assert [r["name"] for r in data["standings"]] == ["Боб", "Вика"]


async def test_summary_payload_latest_and_tabs(db):
    db.add(DigestSnapshot(kind="day", text="📅 <b>Итоги дня — 12.10</b>", created_at=NOW - timedelta(days=1)))
    db.add(DigestSnapshot(kind="day", text=DAY_TEXT, created_at=NOW))
    await db.commit()
    data = await api_club.summary_payload(db, "day")
    assert data["title"] == "Итоги дня — 13.10"
    assert {t["kind"]: t["available"] for t in data["tabs"]} == {
        "day": True, "week": False, "month": False, "quarter": False, "year": False}
    empty = await api_club.summary_payload(db, "year")
    assert empty["empty"]
    assert await api_club.summary_payload(db, "bogus") is None


# ── Лента ─────────────────────────────────────────────────────────────────────

async def _club(db):
    a, b, c = _player(ADMIN, "Алиса", 1080.0), _player(2002, "Боб", 1120.0), _player(3003, "Вика", 1050.0)
    db.add_all([a, b, c])
    await db.flush()
    epic = Match(challenger_id=a.id, challenged_id=b.id, status=MatchStatus.completed, winner_id=a.id,
                 sets_data=[{"w": 9, "l": 11}, {"w": 7, "l": 11}, {"w": 13, "l": 11}, {"w": 14, "l": 12}, {"w": 12, "l": 10}],
                 rating_change=20.0, completed_at=NOW - timedelta(minutes=30))
    plain_m = Match(challenger_id=b.id, challenged_id=c.id, status=MatchStatus.completed, winner_id=b.id,
                    sets_data=[{"w": 11, "l": 3}], rating_change=4.0, completed_at=NOW - timedelta(minutes=20))
    old = _completed(a, c, a.id, 5.0, NOW - timedelta(days=20))
    db.add_all([epic, plain_m, old])
    db.add(AchievementEarned(player_id=a.id, achievement_id="hat_trick", earned_at=NOW - timedelta(hours=1)))
    db.add(AchievementEarned(player_id=a.id, achievement_id="phoenix", earned_at=NOW - timedelta(hours=1)))  # скрытая
    db.add(ClubRecordPing(kind="streak", player_id=b.id, value=6, pinged_at=NOW - timedelta(hours=2)))
    db.add(ChampionReign(player_id=b.id, started_at=NOW - timedelta(days=1)))
    a.achievements = json.dumps(["hat_trick", "press_start"])
    b.achievements = json.dumps(["press_start"])
    await db.commit()
    return a, b, c, epic, plain_m


async def test_feed_events(db):
    a, b, c, epic, plain_m = await _club(db)
    feed = await api_club.feed_events(db, days=7)
    types = [e["type"] for e in feed]
    assert types.count("match") == 2                       # старый матч вне окна
    assert [e["type"] for e in await api_club.feed_events(db)].count("match") == 3  # окно по умолчанию — 30 дней
    assert "achievement" in types and "record" in types and "throne" in types
    names = [e.get("name") for e in feed if e["type"] == "achievement"]
    assert names == ["Хет-трик"]                           # скрытая не раскрыта
    by_id = {e.get("id"): e for e in feed if e["type"] == "match"}
    assert by_id[epic.id].get("report")                     # драматичный — с репортажем
    assert "report" not in by_id[plain_m.id]                 # обычный — без
    assert feed[0]["day"] == "Сегодня"
    times = [e["time"] for e in feed if e["day"] == "Сегодня"]
    assert times == sorted(times, reverse=True)


async def test_today_line(db):
    a, *_ = await _club(db)
    assert (await api_club.today_line(db, a)).startswith("Сегодня: 1 матч, 1 победа · +")
    lonely = _player(9, "Никто")
    db.add(lonely)
    await db.commit()
    assert await api_club.today_line(db, lonely) == ""


# ── Матч, период, график ──────────────────────────────────────────────────────

async def test_match_payload(db):
    a, b, c, epic, _ = await _club(db)
    data = await api_club.match_payload(db, epic.id)
    assert data["winner"] == "a" and data["a"]["name"] == "Алиса"
    assert data["sets"][0] == {"a": 9, "b": 11} and data["sets"][-1] == {"a": 12, "b": 10}
    assert data["a"]["delta"] == "+20.0" and data["b"]["delta"] == "−20.0"
    assert data["report"] and data["reason"]
    assert data["h2h"]["a"] == 1 and data["h2h"]["total"] == "1 встреча"
    assert await api_club.match_payload(db, 99999) is None


async def test_period_leaderboard(db):
    a, b, c, *_ = await _club(db)
    data = await api_club.period_leaderboard_payload(db, a, "week")
    gains = {r["name"]: r["gain"] for r in data["rows"]}
    assert gains == {"Алиса": "+20.0", "Боб": "−16.0", "Вика": "−4.0"}  # старый матч вне 7 дней
    assert data["rows"][0]["name"] == "Алиса" and data["rows"][0]["is_viewer"] is True
    month = await api_club.period_leaderboard_payload(db, a, "month")
    assert {r["name"]: r["gain"] for r in month["rows"]}["Алиса"] == "+25.0"
    assert await api_club.period_leaderboard_payload(db, a, "year") is None


async def test_chart_payload(db):
    a, *_ = await _club(db)
    data = await api_club.chart_payload(db, a)
    assert len(data["points"]) == 2
    assert data["points"][-1]["v"] == 1080.0
    lonely = _player(9, "Никто")
    db.add(lonely)
    await db.commit()
    assert (await api_club.chart_payload(db, lonely))["points"] == []


# ── Ачивки ────────────────────────────────────────────────────────────────────

async def test_achievement_extras_rarity_date_nearest(db):
    a, b, c, *_ = await _club(db)
    extras = await api_club.achievements_extras(db, a)
    assert extras["per_ach"]["press_start"]["rarity"] == "есть у 2 из 3"
    assert extras["per_ach"]["hat_trick"]["rarity"] == "есть у 1 из 3"
    assert "date" in extras["per_ach"]["hat_trick"]
    assert 1 <= len(extras["nearest"]) <= 3
    assert all(0 <= n["progress"] <= 1 for n in extras["nearest"])


async def test_nearest_list_matches_bot_goal_line(db):
    a, *_ = await _club(db)
    matches = await get_career_matches(db, a.id, with_opponents=True)
    s = _compute_player_stats(a, matches)
    first = nearest_achievements(a, s, 3)[0]
    line = _nearest_achievement_progress(a, s, 3)
    assert f"{first[2]}/{first[3]} {first[4]}" in line


def test_plain_does_not_leave_separator_after_removed_emoji():
    assert plain("⏳ Цель: 💯 Рейтинг 1200: 1098/1200 pts") == "Цель: Рейтинг 1200: 1098/1200 pts"


# ── HTTP ──────────────────────────────────────────────────────────────────────

@pytest.fixture
async def client(db_factory, app_on):
    async with db_factory() as s:
        await _club(s)
        s.add(DigestSnapshot(kind="day", text=DAY_TEXT))
        await s.commit()
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        yield c


@pytest.mark.parametrize("path", ["/api/summary/day", "/api/match/1", "/api/player/1/chart",
                                  "/api/leaderboard?period=week", "/api/leaderboard?period=month", "/api/home",
                                  "/api/player/1/achievements"])
async def test_new_routes_answer(client, path):
    resp = await client.get(path, headers=client.auth)
    assert resp.status == 200, await resp.text()


@pytest.mark.parametrize("path", ["/api/summary/bogus", "/api/match/9999", "/api/match/x", "/api/leaderboard?period=year"])
async def test_new_routes_404(client, path):
    assert (await client.get(path, headers=client.auth)).status == 404


async def test_home_has_feed_today_and_summary_link(client):
    data = await (await client.get("/api/home", headers=client.auth)).json()
    assert data["feed"] and data["me"]["today"]
    assert data["links"][0]["route"] == "summary/day"


async def test_achievements_route_merges_extras_without_leaking_hidden(client):
    data = await (await client.get("/api/player/1/achievements", headers=client.auth)).json()
    assert data["nearest"]
    items = [it for c in data["categories"] for it in c["items"]]
    assert any(it.get("rarity") for it in items)
    hidden_locked = [it for it in items if it.get("hidden")]
    assert all("id" not in it and "rarity" not in it for it in hidden_locked)
