"""
v2.176.0: итоги дня в приложении (на лету, листание дней), «Против каждого» и
фильтр истории, реакции на ачивки и рекорды в ленте, «Поделиться» картинкой,
две колонки на широком окне.
Запуск: pytest tests/test_v2176.py
"""
import base64
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

import bot.webapp.share as share
from bot.db.models import AchievementEarned, ChampionReign, ClubRecordPing, FeedReaction
from bot.utils import msk_day_start
from bot.webapp import api_day
from bot.webapp.api import versus_table
from bot.webapp.api_day import day_label, day_payload, usual_line
from bot.webapp.reactions import feed_reactions_for, toggle_feed_reaction, toggle_reaction
from bot.webapp.server import create_app
from bot.webapp.share import decode_png, send_share
from tests.conftest import _completed, _player, freeze_clock
from tests.test_webapp import ADMIN, TOKEN, _init_data

TODAY = msk_day_start() + timedelta(hours=1)   # «сегодня» с запасом от полуночи по МСК


@pytest.fixture(autouse=True)
def _clock_after_today(monkeypatch):
    """Часы кода под тестом стоят на TODAY + 2 ч. TODAY — полночь по МСК + 1 ч,
    а экраны дня и месяца берут матчи «до сейчас»: в первый час суток
    «сегодняшние» матчи теста оказывались в будущем (15.10, 00:48 МСК). Было
    max(сейчас, TODAY + 2 ч) — не спасало, если прогон пересёк полночь МСК между
    импортом и тестом: «сейчас» уже следующий день, а TODAY — вчера (CI 15.10, 00:00 МСК)."""
    freeze_clock(monkeypatch, TODAY + timedelta(hours=2))


STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


def _today():
    return api_day._msk_today()


async def _club(db):
    a, b, c = _player(ADMIN, "Алиса", 1100.0), _player(2002, "Боб", 1000.0), _player(3003, "Вика", 1050.0)
    db.add_all([a, b, c])
    await db.flush()
    return a, b, c


# ── Подписи ───────────────────────────────────────────────────────────────────

def test_day_label():
    today = date(2026, 10, 14)
    assert day_label(today, today) == "Сегодня, 14 октября"
    assert day_label(date(2026, 10, 13), today) == "Вчера, 13 октября"
    assert day_label(date(2026, 3, 1), today) == "1 марта"
    assert day_label(date(2025, 12, 30), today) == "30 декабря 2025"


def test_usual_line():
    assert usual_line(5, None) == ""
    assert usual_line(7, 5.0) == "Жарче обычного на 40% (обычно 5)"
    assert usual_line(4, 1.1) == "Жарче обычного в 3,6 раза (обычно 1,1)"
    assert usual_line(10, 2.0) == "Жарче обычного в 5 раз (обычно 2)"
    assert usual_line(3, 6.0) == "Тише обычного на 50% (обычно 6)"
    assert usual_line(5, 4.8) == "Как обычно (обычно 4,8)"


# ── Итоги дня ─────────────────────────────────────────────────────────────────

async def test_day_payload_board_heroes_and_navigation(db):
    a, b, c = await _club(db)
    db.add_all([
        _completed(a, b, a.id, 10.0, TODAY - timedelta(days=2)),
        _completed(b, c, b.id, 12.0, TODAY),
        _completed(b, a, b.id, 9.0, TODAY + timedelta(minutes=40)),
    ])
    await db.commit()
    d = await day_payload(db, a, None)
    assert d["label"].startswith("Сегодня") and d["empty"] == ""
    assert d["board"]["matches"] == 2 and d["board"]["sets"] == 2 and d["board"]["points"] == 32
    assert d["board"]["usual"] == "Жарче обычного в 2 раза (обычно 1)"   # v2.201.0
    assert d["hero"]["name"] == "Боб" and d["hero"]["delta"] == "+21.0" and d["hero"]["record"] == "2–0"
    assert d["slump"]["name"] == "Вика" and d["slump"]["delta"] == "−12.0"
    assert d["next"] is None and d["prev"] == (_today() - timedelta(days=2)).isoformat()
    assert [m["id"] for m in d["timeline"]] == sorted(m["id"] for m in d["timeline"])
    assert sum(h["n"] for h in d["hours"]) == 2

    past = await day_payload(db, a, d["prev"])
    assert past["board"]["matches"] == 1 and past["next"] == _today().isoformat() and past["prev"] is None


async def test_day_payload_movement_uses_rating_at_that_moment(db):
    """Боб был последним, за день набрал 60 — и поднялся на два места."""
    a, b, c = await _club(db)
    b.rating = 1120.0  # текущий рейтинг уже после матчей дня
    db.add_all([
        _completed(a, c, a.id, 5.0, TODAY - timedelta(days=1)),
        _completed(b, c, c.id, 1.0, TODAY - timedelta(days=1)),   # Боб уже играл до этого дня
        _completed(b, a, b.id, 30.0, TODAY),
        _completed(b, c, b.id, 30.0, TODAY + timedelta(minutes=10)),
    ])
    await db.commit()
    d = await day_payload(db, a, None)
    moves = {r["name"]: (r["place"], r["change"]) for r in d["movement"]}
    assert moves["Боб"] == (1, 2)
    assert moves["Алиса"][1] == -1 and moves["Вика"][1] == -1
    # v2.211.0: справа в таблице — рейтинг за день, а не только сдвиг места
    deltas = {r["name"]: r["delta"] for r in d["movement"]}
    assert deltas["Боб"].startswith("+") and deltas["Алиса"].startswith(("−", "-"))


async def test_day_payload_champion_pinned_and_newcomer(db):
    a, b, c = await _club(db)
    db.add(ChampionReign(player_id=b.id, started_at=TODAY - timedelta(days=5), ended_at=None))
    db.add_all([_completed(a, b, a.id, 5.0, TODAY - timedelta(days=1)), _completed(c, a, c.id, 5.0, TODAY)])
    await db.commit()
    d = await day_payload(db, a, None)
    moves = {r["name"]: r for r in d["movement"]}
    assert moves["Боб"]["place"] == 1                       # чемпион закреплён
    assert moves["Вика"]["new"] is True                      # раньше не играла
    assert moves["Боб"]["played"] is False


async def test_day_payload_match_of_day_discussed_and_achievements(db):
    a, b, c = await _club(db)
    m1 = _completed(a, b, a.id, 25.0, TODAY)
    m1.sets_data = [{"w": 11, "l": 13}, {"w": 13, "l": 11}, {"w": 12, "l": 10}]
    m2 = _completed(b, c, b.id, 5.0, TODAY + timedelta(minutes=5))
    db.add_all([m1, m2])
    db.add(AchievementEarned(player_id=a.id, achievement_id="first_blood", earned_at=TODAY))
    db.add(AchievementEarned(player_id=b.id, achievement_id="phoenix", earned_at=TODAY))   # скрытая
    db.add(ClubRecordPing(kind="streak", player_id=a.id, value=5, pinged_at=TODAY))
    await db.commit()
    for p, kind in ((b, "fire"), (c, "poo")):
        await toggle_reaction(db, m2.id, p, kind)
    d = await day_payload(db, a, None)
    assert d["match_of_day"]["id"] == m1.id and d["match_of_day"]["report"]
    assert d["discussed"]["id"] == m2.id and d["discussed"]["total"] == 2
    assert [x["name"] for x in d["achievements"]] != [] and all(x["name"] != "Феникс" for x in d["achievements"])
    assert len(d["achievements"]) == 1
    assert d["records"][0]["title"].startswith("Серия побед")


async def test_day_payload_empty_bad_and_future(db):
    a, _, _ = await _club(db)
    await db.commit()
    assert (await day_payload(db, a, None))["empty"] == "Матчей ещё не было."
    assert await day_payload(db, a, "вчера") is None
    assert await day_payload(db, a, (_today() + timedelta(days=1)).isoformat()) is None
    assert (await day_payload(db, a, "2026-01-05"))["empty"] == "В этот день не играли."


# ── «Против каждого» ──────────────────────────────────────────────────────────

def test_versus_table_counts_and_order():
    me, b, c = SimpleNamespace(id=1, display_name="Я", rating=1000.0), \
        SimpleNamespace(id=2, display_name="Боб", rating=1000.0), SimpleNamespace(id=3, display_name="Вика", rating=1000.0)

    def m(opp, winner, day):
        return SimpleNamespace(challenger=me, challenged=opp, challenger_id=1, challenged_id=opp.id,
                               winner_id=winner, completed_at=TODAY - timedelta(days=day))
    rows = versus_table([m(b, 1, 0), m(c, 3, 1), m(b, None, 2), m(b, 2, 3)], 1)
    assert [r["name"] for r in rows] == ["Боб", "Вика"]
    bob = rows[0]
    assert (bob["w"], bob["l"], bob["d"], bob["score"], bob["rate"]) == (1, 1, 1, "1–1–1", "33%")
    assert bob["tone"] == "" and rows[1]["tone"] == "loss"


# ── Реакции на ачивки и рекорды ───────────────────────────────────────────────

async def test_feed_reaction_toggle_and_validation(db):
    a, b, _ = await _club(db)
    ach = AchievementEarned(player_id=b.id, achievement_id="first_blood", earned_at=TODAY)
    hidden = AchievementEarned(player_id=b.id, achievement_id="phoenix", earned_at=TODAY)
    rec = ClubRecordPing(kind="peak", player_id=b.id, value=1200, pinged_at=TODAY)
    db.add_all([ach, hidden, rec])
    await db.commit()
    first = await toggle_feed_reaction(db, f"ach:{ach.id}", a, "fire")
    assert [r["kind"] for r in first if r["mine"]] == ["fire"]
    swapped = await toggle_feed_reaction(db, f"ach:{ach.id}", a, "like")
    assert {r["kind"]: r["count"] for r in swapped}["fire"] == 0 and [r["kind"] for r in swapped if r["mine"]] == ["like"]
    removed = await toggle_feed_reaction(db, f"ach:{ach.id}", a, "like")
    assert all(not r["mine"] for r in removed)
    assert await toggle_feed_reaction(db, f"rec:{rec.id}", a, "poo") is not None
    assert await toggle_feed_reaction(db, f"ach:{hidden.id}", a, "fire") is None   # скрытая не в ленте
    assert await toggle_feed_reaction(db, "ach:999", a, "fire") is None
    assert await toggle_feed_reaction(db, "match:1", a, "fire") is None
    assert await toggle_feed_reaction(db, f"rec:{rec.id}", a, "boo") is None
    counts = await feed_reactions_for(db, [f"rec:{rec.id}", "rec:0"], b.id)
    assert {r["kind"]: r["count"] for r in counts[f"rec:{rec.id}"]}["poo"] == 1
    assert all(r["count"] == 0 for r in counts["rec:0"])
    assert len((await db.execute(select(FeedReaction))).scalars().all()) == 1


# ── «Поделиться» ──────────────────────────────────────────────────────────────

def test_decode_png():
    assert decode_png(PNG_URL) == PNG
    assert decode_png("data:image/jpeg;base64,AAAA") is None
    assert decode_png("data:image/png;base64," + base64.b64encode(b"GIF89a").decode()) is None
    assert decode_png("data:image/png;base64,не base64") is None
    assert decode_png(None) is None
    big = "data:image/png;base64," + base64.b64encode(PNG + b"\x00" * (3 * 1024 * 1024)).decode()
    assert decode_png(big) is None


async def test_send_share_once_per_cooldown(monkeypatch):
    share._last_share.clear()
    clock = [100.0]
    monkeypatch.setattr(share.time, "monotonic", lambda: clock[0])
    bot = SimpleNamespace(send_photo=AsyncMock())
    assert await send_share(bot, 1, "day", PNG_URL) == "ok"
    assert await send_share(bot, 1, "day", PNG_URL) == "wait"
    assert await send_share(bot, 1, "week", PNG_URL) == "bad"
    clock[0] += 11
    assert await send_share(bot, 1, "profile", PNG_URL) == "ok"
    args, kwargs = bot.send_photo.call_args
    assert args[0] == 1 and kwargs["caption"].startswith("Твой профиль")
    failing = SimpleNamespace(send_photo=AsyncMock(side_effect=RuntimeError("telegram")))
    assert await send_share(failing, 2, "day", PNG_URL) == "fail"
    share._last_share.clear()


# ── Через сервер ──────────────────────────────────────────────────────────────

@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")
    share._last_share.clear()
    async with db_factory() as s:
        a, b, c = await _club(s)
        s.add_all([_completed(a, b, a.id, 10.0, TODAY), _completed(c, a, c.id, 8.0, TODAY + timedelta(minutes=5))])
        ach = AchievementEarned(player_id=a.id, achievement_id="first_blood", earned_at=TODAY)
        s.add(ach)
        await s.commit()
        ids = {"a": a.id, "ach": ach.id}
    bot = SimpleNamespace(send_photo=AsyncMock(), me=AsyncMock(return_value=SimpleNamespace(username="club_bot")),
                          get_user_profile_photos=AsyncMock(return_value=SimpleNamespace(photos=[])))
    async with TestClient(TestServer(create_app(TOKEN, db_factory, bot))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        c.ids, c.bot = ids, bot
        yield c
    share._last_share.clear()


async def test_day_endpoint(client):
    resp = await client.get("/api/day", headers=client.auth)
    d = await resp.json()
    assert resp.status == 200 and d["board"]["matches"] == 2
    assert (await client.get("/api/day?date=плохо", headers=client.auth)).status == 404


async def test_history_has_versus_and_iso(client):
    d = await (await client.get(f"/api/player/{client.ids['a']}/history", headers=client.auth)).json()
    assert {r["name"] for r in d["versus"]} == {"Боб", "Вика"}
    assert all(len(m["iso"]) == 10 for m in d["matches"])


async def test_feed_has_reactions_on_achievement_and_react_endpoint(client):
    home = await (await client.get("/api/home", headers=client.auth)).json()
    item = next(e for e in home["feed"] if e["type"] == "achievement")
    assert item["target"] == f"ach:{client.ids['ach']}" and len(item["reactions"]) == 4
    resp = await client.post("/api/feed/react", json={"target": item["target"], "kind": "lol"}, headers=client.auth)
    assert resp.status == 200
    assert [r["kind"] for r in (await resp.json())["reactions"] if r["mine"]] == ["lol"]
    assert (await client.post("/api/feed/react", json={"target": "ach:0", "kind": "lol"}, headers=client.auth)).status == 404
    assert (await client.post("/api/feed/react", data="[]", headers=client.auth)).status == 404


async def test_share_endpoint(client):
    ok = await client.post("/api/share", json={"kind": "day", "image": PNG_URL}, headers=client.auth)
    assert ok.status == 200 and (await ok.json()) == {"ok": True}
    assert client.bot.send_photo.await_args.args[0] == ADMIN
    again = await client.post("/api/share", json={"kind": "day", "image": PNG_URL}, headers=client.auth)
    assert again.status == 429
    bad = await client.post("/api/share", json={"kind": "day", "image": "data:image/png;base64,QUJD"}, headers=client.auth)
    assert bad.status == 400


# ── Страница ──────────────────────────────────────────────────────────────────

def test_page_has_day_screen_wide_layout_and_share():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "function renderDay(" in js and 'api("day"' in js
    assert "summary\\/(day|week|month|quarter|year)(?:\\/(\\d{8}))?$" in js
    assert "function twoCols(" in js and "@media (min-width: 760px)" in css and ".is-wide .cols" in css
    assert 'post("share"' in js and 'post("feed/react"' in js
    assert "Поделиться картинкой" in js and "Против каждого" in js
