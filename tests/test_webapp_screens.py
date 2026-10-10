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
    assert data["metrics"][0]["label"] == "Победы"
    assert data["chart"]["values"][-1] == 1080.0
    titles = [s["title"] for s in data["sections"]]
    assert titles[:4] == ["Достижения", "Радар стиля", "Активность", "История матчей"]
    assert "Личные встречи" not in titles  # с самим собой встреч нет


async def test_profile_payload_other_player_has_h2h_and_no_title_goal(db):
    a, b, _ = await _seed(db)
    data = await api.player_payload(db, b, a)
    assert data["head"]["personal"] is False
    assert any(s["title"] == "Личные встречи" for s in data["sections"])
    labels = [it.get("label", "") for g in data["groups"] for it in g]
    assert not any(label.startswith("До звания") for label in labels)  # цель звания — только себе


async def test_profile_without_matches(db):
    lonely = _player(9, "Новичок")
    db.add(lonely)
    await db.commit()
    data = await api.player_payload(db, lonely, lonely)
    assert data["empty"]
    assert data["sections"] == []


async def test_achievements_payload_counts(db):
    a, _, _ = await _seed(db)
    data = api.achievements_payload(a)
    assert data["count"].startswith("2 из ")
    first = api.achievement_category_payload(a, 0)
    assert first["items"][0]["earned"] is True
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
    assert data["metrics"][0]["label"] == "Счёт встреч"
    assert len(data["matches"]) == 4


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


async def test_leaderboard_links(db):
    a, _, _ = await _seed(db)
    data = await api.leaderboard_payload(db, a.id)
    routes = [link["route"] for link in data["links"]]
    assert routes == [f"player/{a.id}", "records", "matches", "activity/club", "throne"]
    assert data["rows"][0]["player_id"]


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
    "/api/player/1", "/api/player/2", "/api/player/1/stats/opp", "/api/player/1/achievements",
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
