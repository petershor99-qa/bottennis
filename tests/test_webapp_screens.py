"""
Тесты экранов Mini App (v2.161.0): преобразование строк бота, данные
профиля/достижений/графиков/истории/личных встреч/рекордов/зала славы и
HTTP-маршруты. Запуск: pytest tests/test_webapp_screens.py
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select

from bot.db.models import ChampionReign, Match, MatchStatus, Player, UsageEvent
from bot.webapp import api
from bot.webapp.server import create_app
from bot.webapp.text import groups, item, plain
from tests.conftest import _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

# ── Строки бота -> элементы приложения ────────────────────────────────────────


def test_plain_strips_tags_entities_and_emoji():
    assert plain("🔥 Серия: <b>5 побед подряд</b>") == "Серия: 5 побед подряд"
    assert plain("<b>Боб &amp; Ко</b>") == "Боб & Ко"


def test_plain_turns_double_space_into_separator():
    assert plain("+36.4 pts  <i>11:5</i>") == "+36.4 pts · 11:5"


def test_plain_keeps_double_space_before_parenthesis_as_space():
    assert plain("<b>Боб</b>  (3 из 4)") == "Боб (3 из 4)"


def test_inline_handshake_becomes_word():
    assert item("⚔️ Чаще всего: <b>Боб</b> (12 матчей, 5–6 🤝1)")["value"] == "Боб (12 матчей, 5–6 ничьи 1)"


def test_item_splits_label_and_value():
    assert item("📈 Пик рейтинга: <b>1150.2</b> pts") == {"label": "Пик рейтинга", "value": "1150.2 pts"}


def test_item_without_colon_is_text():
    assert item("🌟 Ты MVP месяца!") == {"text": "Ты MVP месяца!"}


def test_form_line_becomes_dots():
    assert item("🗓 Форма (7 дней): 🟢🔴🟡  <i>(12 матчей)</i>") == {
        "label": "Форма (7 дней)", "form": ["w", "l", "d"], "note": "12 матчей",
    }


def test_groups_split_on_blank_lines_and_keep_multiline_notes():
    out = groups(["a: 1", "b: 2", "", "🌟 <b>Матч</b>\n<b>A</b> vs <b>B</b>\n<i>камбэк</i>"])
    assert len(out) == 2
    assert out[1][0] == {"text": "Матч", "note": "A vs B\nкамбэк"}


def test_progress_bar_becomes_fraction():
    out = groups(["⏳ Цель: Стукнул полтинник: 35/50 матчей\n███████░░░"])
    assert out[0][0]["progress"] == 0.7
    assert "note" not in out[0][0]


def test_no_emoji_left_in_any_profile_string():
    lines = ["🎖 Рекорд серии: <b>4 побед подряд</b>", "〽️ В среднем за матч: <b>+1.2 pts</b>",
             "❄️ x", "⚖️ Равный бой", "✅ готово"]
    for line in lines:
        text = plain(line)
        assert all(ord(ch) < 0x2190 or 0x2010 <= ord(ch) <= 0x2122 for ch in text), text


# ── Данные экранов ────────────────────────────────────────────────────────────

def _sets(won: bool) -> list[dict]:
    return [{"w": 11, "l": 6}, {"w": 9, "l": 11}, {"w": 11, "l": 8}] if won else [{"w": 11, "l": 4}]


async def _seed(session) -> tuple[Player, Player, Player]:
    a = _player(ADMIN, "Алиса", 1080.0)
    b = _player(2002, "Боб", 1120.0)
    c = _player(3003, "Вика", 1040.0)
    a.achievements = json.dumps(["press_start", "first_blood"])
    session.add_all([a, b, c])
    await session.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(8):
        winner = a if i % 3 else b
        session.add(Match(
            challenger_id=a.id, challenged_id=b.id if i % 2 else c.id,
            status=MatchStatus.completed,
            winner_id=winner.id if (i % 2 or winner is a) else c.id,
            sets_data=_sets(i % 2 == 0), rating_change=10.0 + i,
            completed_at=now - timedelta(days=20 - i * 2),
        ))
    session.add(Match(challenger_id=b.id, challenged_id=c.id, status=MatchStatus.completed,
                      winner_id=None, sets_data=[{"w": 11, "l": 9}, {"w": 9, "l": 11}],
                      rating_change=1.5, completed_at=now - timedelta(days=1)))
    b.is_champion = True
    session.add(ChampionReign(player_id=c.id, started_at=now - timedelta(days=40), ended_at=now - timedelta(days=30)))
    session.add(ChampionReign(player_id=b.id, started_at=now - timedelta(days=30)))
    await session.commit()
    return a, b, c


async def test_profile_payload_personal(db):
    a, _, _ = await _seed(db)
    data = await api.player_payload(db, a, a)
    assert data["head"]["personal"] is True
    assert data["head"]["rating"] == "1080.0"
    assert [t["label"] for t in data["tiles"]][0] == "Побед"
    assert len(data["tiles"]) == 4
    assert data["chart"]["values"][-1] == 1080.0
    assert data["versus"] is None  # с самим собой встреч нет
    assert 1 <= len(data["last10"]) <= 10
    assert set(data["last10"]) <= {"w", "l", "d"}
    assert any(g["label"].startswith("До звания") and 0 <= g["progress"] <= 1 for g in data["goals"])
    assert data["counts"]["matches"] == 8


async def test_profile_last10_is_oldest_to_newest(db):
    a, _, _ = await _seed(db)
    data = await api.player_payload(db, a, a)
    # последний матч сида (i=7): Алиса против Боба, 7 % 3 != 0 -> победа Алисы
    assert data["last10"][-1] == "w"


async def test_profile_payload_other_player_has_versus_and_no_goals(db):
    a, b, _ = await _seed(db)
    data = await api.player_payload(db, b, a)
    assert data["head"]["personal"] is False
    assert data["versus"] == "Ты против Боб"
    assert data["goals"] == []  # цели звания и ачивки — только себе


async def test_profile_without_matches(db):
    lonely = _player(9, "Новичок")
    db.add(lonely)
    await db.commit()
    data = await api.player_payload(db, lonely, lonely)
    assert data["empty"]
    assert data["tiles"] == [] and data["blocks"] == []


@pytest.mark.parametrize("rating,expected", [(900.0, 0.0), (975.0, 0.5), (1050.0, 0.0), (1125.0, 0.5), (1500.0, 1.0)])
def test_title_progress_inside_band(rating, expected):
    assert api._title_progress(rating) == expected


@pytest.mark.parametrize("ach_id,tier", [
    ("hat_trick", "bronze"), ("im_on_fire", "silver"), ("god_mode", "gold"),
    ("fk_tyumen", "bronze"), ("valley_of_tears", "silver"),
    ("fifty", "bronze"), ("veteran", "bronze"), ("legend", "silver"), ("monument", "gold"), ("superstar", "gold"),
    ("press_start", ""), ("highlander", ""),
])
def test_achievement_tiers(ach_id, tier):
    assert api.achievement_tier(ach_id) == tier


def test_ladders_reference_real_achievements():
    known = {a.id for a in api.ACHIEVEMENTS_LIST}
    for ladder in api.ACHIEVEMENT_LADDERS:
        assert set(ladder) <= known


async def test_achievements_payload_counts(db):
    a, _, _ = await _seed(db)
    data = api.achievements_payload(a)
    assert data["count"].startswith("2 из ")
    first = data["categories"][0]
    assert first["items"][0]["earned"] is True
    assert sum(len(c["items"]) for c in data["categories"]) == len(api.ACHIEVEMENTS_LIST)
    assert api.achievement_category_payload(a, 99) is None


async def test_hidden_achievement_is_masked(db):
    a, _, _ = await _seed(db)
    for index in range(6):
        for it in api.achievement_category_payload(a, index)["items"]:
            if it.get("hidden"):
                assert it["name"] == "?"


async def test_radar_requires_five_matches(db):
    _, _, c = await _seed(db)
    lonely = _player(10, "Мало")
    db.add(lonely)
    await db.commit()
    assert (await api.radar_payload(db, lonely))["empty"]
    data = await api.radar_payload(db, (await db.execute(select(Player).where(Player.telegram_id == ADMIN))).scalar_one())
    assert len(data["axes"]) >= 3
    assert all(0 <= ax["value"] <= 100 for ax in data["axes"])


async def test_activity_covers_whole_weeks(db):
    a, _, _ = await _seed(db)
    data = await api.activity_payload(db, a)
    assert len(data["days"]) % 7 == 0 or data["days"][-1]["count"] is not None
    assert sum(d["count"] or 0 for d in data["days"]) == 8
    club = await api.activity_payload(db, None)
    assert sum(d["count"] or 0 for d in club["days"]) == 9


async def test_history_rows_are_in_player_perspective(db):
    a, _, _ = await _seed(db)
    data = await api.history_payload(db, a)
    assert len(data["matches"]) == 8
    for row in data["matches"]:
        assert row["result"] in ("w", "l", "d")
        assert row["delta"].startswith(("+", "−"))
        if row["result"] == "w":
            assert row["delta"].startswith("+")


async def test_h2h_payload(db):
    a, b, _ = await _seed(db)
    data = await api.h2h_payload(db, a, b)
    assert data["title"] == "Ты против Боб"
    assert data["metrics"][0]["label"] == "Счёт встреч"
    assert len(data["matches"]) == 4
    assert data["score"]["me"] + data["score"]["them"] + data["score"]["draws"] == 4
    assert 0 <= data["forecast"]["chance"] <= 100
    assert data["forecast"]["win"].startswith("+") and data["forecast"]["lose"].startswith("−")


async def test_h2h_axes_when_both_have_enough_matches(db):
    a, b, c = await _seed(db)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(3):
        db.add(Match(challenger_id=b.id, challenged_id=c.id, status=MatchStatus.completed, winner_id=b.id,
                     sets_data=[{"w": 11, "l": 5}], rating_change=5.0, completed_at=now - timedelta(hours=i + 1)))
    await db.commit()
    data = await api.h2h_payload(db, a, b)
    assert data["axes"]
    assert all(0 <= ax["me"] <= 100 and 0 <= ax["them"] <= 100 for ax in data["axes"])


async def test_h2h_without_meetings_still_has_forecast(db):
    a, _, _ = await _seed(db)
    stranger = _player(77, "Новенький")
    db.add(stranger)
    await db.commit()
    data = await api.h2h_payload(db, a, stranger)
    assert data["empty"] and data["score"] is None
    assert data["forecast"]["chance"] > 50  # у Алисы рейтинг выше
    assert data["axes"] == []  # у соперника меньше 5 матчей — сравнения стилей нет


async def test_club_matches_paging(db, monkeypatch):
    await _seed(db)
    monkeypatch.setattr(api, "CLUB_MATCHES_PAGE", 5)
    first = await api.club_matches_payload(db, 0)
    assert len(first["matches"]) == 5 and first["next_offset"] == 5
    rest = await api.club_matches_payload(db, 5)
    assert len(rest["matches"]) == 4 and rest["next_offset"] is None
    draw = next(m for m in first["matches"] + rest["matches"] if not m["winner"])
    assert draw["a"] == "Боб"


async def test_records_have_no_html_or_emoji(db):
    await _seed(db)
    data = await api.records_payload(db)
    assert data["categories"]
    for cat in data["categories"]:
        for it in cat["items"]:
            blob = json.dumps(it, ensure_ascii=False)
            assert "<b>" not in blob and "<i>" not in blob
            assert not any(0x1F300 <= ord(ch) <= 0x1FAFF for ch in blob)


async def test_hall_of_fame(db):
    await _seed(db)
    data = await api.hall_of_fame_payload(db)
    assert data["current"]["name"] == "Боб"
    assert len(data["reigns"]) == 1 and data["reigns"][0]["name"] == "Вика"


async def test_home_payload(db):
    a, _, _ = await _seed(db)
    data = await api.home_payload(db, a)
    assert [p["rank"] for p in data["podium"]] == [1, 2, 3]
    assert data["podium"][0]["champion"] is True and data["podium"][0]["name"] == "Боб"
    assert data["me"]["place"].startswith("#")
    assert data["featured"] is not None  # сегодня матчей нет — показан последний
    assert data["featured"]["title"] == "Последний матч"
    assert [link["route"] for link in data["links"]] == ["records", "matches", "activity/club", "throne"]


async def test_home_shows_recent_record_pings(db):
    from bot.db.models import ClubRecordPing

    a, b, _ = await _seed(db)
    db.add(ClubRecordPing(kind="streak", player_id=b.id, value=7))
    db.add(ClubRecordPing(kind="peak", player_id=b.id, value=1500.0,
                          pinged_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)))
    await db.commit()
    data = await api.home_payload(db, a)
    assert len(data["records"]) == 1  # пинг месячной давности не показывается
    assert data["records"][0]["value"] == "7 побед подряд"


async def test_home_match_of_day_from_today(db):
    a, b, _ = await _seed(db)
    db.add(Match(challenger_id=a.id, challenged_id=b.id, status=MatchStatus.completed, winner_id=a.id,
                 sets_data=[{"w": 11, "l": 13}, {"w": 14, "l": 12}, {"w": 12, "l": 10}, {"w": 13, "l": 11}],
                 rating_change=20.0, completed_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    await db.commit()
    data = await api.home_payload(db, a)
    assert data["today"]["count"] == 1
    assert data["featured"]["title"] == "Матч дня"
    assert data["featured"]["winner"] == "a"
    assert data["featured"]["reason"]


async def test_leaderboard_rows_have_player_ids(db):
    a, _, _ = await _seed(db)
    data = await api.leaderboard_payload(db, a.id)
    assert all(r["player_id"] for r in data["rows"])


# ── HTTP ──────────────────────────────────────────────────────────────────────

@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    async with db_factory() as s:
        await _seed(s)
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        yield c


ROUTES_200 = [
    "/api/home", "/api/me", "/api/player/1", "/api/player/2", "/api/player/1/stats/opp", "/api/player/1/achievements",
    "/api/player/1/achievements/0", "/api/player/1/radar", "/api/player/1/activity",
    "/api/player/1/history", "/api/h2h/2", "/api/activity/club", "/api/records",
    "/api/matches", "/api/matches?offset=5", "/api/throne",
]


@pytest.mark.parametrize("path", ROUTES_200)
async def test_screen_routes_answer(client, path):
    resp = await client.get(path, headers=client.auth)
    assert resp.status == 200, await resp.text()


@pytest.mark.parametrize("path", [
    "/api/player/999", "/api/player/1/stats/nope", "/api/player/1/achievements/77",
    "/api/h2h/1", "/api/player/x",
])
async def test_unknown_targets_are_404(client, path):
    resp = await client.get(path, headers=client.auth)
    assert resp.status in (404, 405), path


@pytest.mark.parametrize("path", ROUTES_200)
async def test_screen_routes_require_init_data(client, path):
    assert (await client.get(path)).status == 401


async def test_bad_offset_falls_back_to_start(client):
    resp = await client.get("/api/matches?offset=abc", headers=client.auth)
    assert resp.status == 200


async def test_screens_do_not_write_to_db(client, db_factory):
    async def snapshot():
        async with db_factory() as s:
            return tuple([
                (await s.execute(select(func.count()).select_from(model))).scalar()
                for model in (Player, Match, ChampionReign, UsageEvent)
            ])
    before = await snapshot()
    for path in ROUTES_200:
        await client.get(path, headers=client.auth)
    assert await snapshot() == before


async def test_me_returns_viewer(client):
    resp = await client.get("/api/me", headers=client.auth)
    assert (await resp.json())["name"] == "Алиса"


async def test_photo_without_bot_is_404(client):
    resp = await client.get("/api/photo/1", headers=client.auth)
    assert resp.status == 404


class _FakeBot:
    def __init__(self, has_photo=True, fail=False):
        self.has_photo, self.fail, self.calls = has_photo, fail, 0

    async def get_user_profile_photos(self, user_id, limit):
        from types import SimpleNamespace

        self.calls += 1
        if self.fail:
            raise RuntimeError("telegram недоступен")
        sizes = [SimpleNamespace(width=80, file_id="small"), SimpleNamespace(width=320, file_id="big")]
        return SimpleNamespace(photos=[sizes] if self.has_photo else [])

    async def download(self, file_id, destination):
        destination.write(b"JPEG-" + file_id.encode())
        return destination


async def test_photo_is_served_and_cached(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    async with db_factory() as s:
        await _seed(s)
    bot = _FakeBot()
    async with TestClient(TestServer(create_app(TOKEN, db_factory, bot))) as c:
        auth = {"Authorization": "tma " + _init_data(ADMIN)}
        first = await c.get("/api/photo/2", headers=auth)
        assert first.status == 200
        assert await first.read() == b"JPEG-big"  # минимальный размер не меньше 160 px
        assert first.headers["Content-Type"] == "image/jpeg"
        await c.get("/api/photo/2", headers=auth)
        assert bot.calls == 1  # второй раз из кэша


async def test_photo_absent_or_failing():
    from bot.webapp.photos import PhotoCache

    cache = PhotoCache()
    assert await cache.get(_FakeBot(has_photo=False), 1) is None
    failing = _FakeBot(fail=True)
    assert await cache.get(failing, 2) is None
    assert await cache.get(failing, 2) is None
    assert failing.calls == 2  # сбой Telegram не кэшируется
