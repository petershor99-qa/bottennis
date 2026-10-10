"""
Тесты Mini App (v2.160.0): вход по initData, флаги из .env, данные таблицы
и HTTP-ответы веб-сервера. Запуск: pytest tests/test_webapp.py
"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.db.models import Player
from bot.keyboards.inline import leaderboard_kb
from bot.services.leaderboard import LeaderboardRow
from bot.webapp.api import _gap_line, _subtitle, _week_label, leaderboard_payload
from bot.webapp.auth import (
    INIT_DATA_MAX_AGE_SECONDS,
    WebAppAuthError,
    init_data_from_header,
    telegram_id_from_init_data,
)
from bot.webapp.config import is_webapp_allowed, webapp_base_url, webapp_url_for
from bot.webapp.server import create_app
from tests.conftest import _completed, _player

TOKEN = "123456:TEST-token"
ADMIN = 1001


def _init_data(user_id: int | None = ADMIN, auth_date: int | None = None, token: str = TOKEN) -> str:
    """Строка initData, подписанная как это делает Telegram."""
    fields = {"auth_date": str(auth_date if auth_date is not None else int(time.time())), "query_id": "AAE"}
    if user_id is not None:
        fields["user"] = json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False)
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
def webapp_env(monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.delenv("WEBAPP_AUDIENCE", raising=False)


# ── Вход по initData ──────────────────────────────────────────────────────────

def test_valid_init_data_gives_telegram_id():
    assert telegram_id_from_init_data(TOKEN, _init_data(42)) == 42


def test_init_data_signed_by_another_bot_is_rejected():
    with pytest.raises(WebAppAuthError):
        telegram_id_from_init_data(TOKEN, _init_data(42, token="999:OTHER"))


def test_tampered_user_is_rejected():
    forged = _init_data(42).replace("%22id%22%3A+42", "%22id%22%3A+1001")
    assert forged != _init_data(42)
    with pytest.raises(WebAppAuthError):
        telegram_id_from_init_data(TOKEN, forged)


def test_stale_init_data_is_rejected():
    old = int(time.time()) - INIT_DATA_MAX_AGE_SECONDS - 60
    with pytest.raises(WebAppAuthError):
        telegram_id_from_init_data(TOKEN, _init_data(42, auth_date=old))


def test_init_data_without_user_is_rejected():
    with pytest.raises(WebAppAuthError):
        telegram_id_from_init_data(TOKEN, _init_data(None))


def test_empty_init_data_is_rejected():
    with pytest.raises(WebAppAuthError):
        telegram_id_from_init_data(TOKEN, "")


def test_fresh_init_data_inside_window_is_accepted():
    now = datetime.now(timezone.utc)
    issued = int((now - timedelta(hours=23)).timestamp())
    assert telegram_id_from_init_data(TOKEN, _init_data(7, auth_date=issued), now=now) == 7


@pytest.mark.parametrize("header,expected", [
    ("tma abc=1&hash=2", "abc=1&hash=2"),
    ("TMA  x=1", "x=1"),
    ("Bearer x=1", ""),
    (None, ""),
    ("", ""),
])
def test_init_data_from_header(header, expected):
    assert init_data_from_header(header) == expected


# ── Флаги из .env ─────────────────────────────────────────────────────────────

def test_webapp_off_without_url(monkeypatch):
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    assert webapp_base_url() is None
    assert webapp_url_for(ADMIN) is None


def test_plain_http_url_is_ignored(monkeypatch):
    """Telegram открывает Mini App только по HTTPS — http-адрес = выключено."""
    monkeypatch.setenv("WEBAPP_URL", "http://club.example")
    assert webapp_base_url() is None


def test_admin_only_by_default(webapp_env):
    assert webapp_url_for(ADMIN) == "https://club.example/"
    assert webapp_url_for(2002) is None


def test_audience_all_opens_for_everyone(webapp_env, monkeypatch):
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")
    assert is_webapp_allowed(2002) is True


def test_admin_only_without_admin_id_is_closed(webapp_env, monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "")
    assert is_webapp_allowed(ADMIN) is False


# ── Подписи строк таблицы ─────────────────────────────────────────────────────

def _row(rank=1, pid=1, name="Боб", rating=1120.4, streak=0, week=0, champion=False,
         challenger=False, mvp=False, inactive=False) -> LeaderboardRow:
    return LeaderboardRow(
        rank=rank, player_id=pid, name=name, rating=rating, matches=10, wins=5, win_rate=50,
        streak=streak, week_change=week, is_champion=champion, is_challenger=challenger,
        is_mvp=mvp, inactive=inactive,
    )


def test_subtitle_champion_with_title():
    assert _subtitle(_row(rating=1420.0, champion=True)) == "Чемпион · Ген дир"


def test_subtitle_streak():
    assert _subtitle(_row(rating=1107.2, streak=5)) == "Миддл · серия 5 побед"


def test_subtitle_inactive():
    assert _subtitle(_row(rating=1000.0, inactive=True)) == "Джун · не играл 7+ дней"


@pytest.mark.parametrize("change,label", [(0, ""), (1, "+1 место"), (-2, "−2 места"), (5, "+5 мест")])
def test_week_label(change, label):
    assert _week_label(change) == label


def test_gap_to_player_above():
    rows = [_row(1, 1, "Боб", 1120.4), _row(2, 2, "Алиса", 1107.2)]
    assert _gap_line(rows, 1) == "До #1 Боб: 13.2 очка."


def test_gap_for_leader_is_lead_over_second():
    rows = [_row(1, 1, "Боб", 1120.4), _row(2, 2, "Алиса", 1107.2)]
    assert _gap_line(rows, 0) == "Отрыв от #2 Алиса: 13.2 очка."


def test_no_gap_under_pinned_champion_with_lower_rating():
    rows = [_row(1, 1, "Боб", 1090.0, champion=True), _row(2, 2, "Алиса", 1107.2)]
    assert _gap_line(rows, 1) == ""


def test_no_gap_for_viewer_outside_table():
    assert _gap_line([_row()], None) == ""


# ── Данные таблицы из БД ──────────────────────────────────────────────────────

async def _seed(db) -> tuple[Player, Player, Player]:
    a, b, c = _player(ADMIN, "Алиса", 1107.2), _player(2002, "Боб", 1120.4), _player(3003, "Новичок")
    db.add_all([a, b, c])
    await db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(_completed(b, a, b.id, 10.0, now - timedelta(days=10)))
    await db.commit()
    return a, b, c


async def test_payload_matches_bot_table(db):
    a, b, _ = await _seed(db)
    data = await leaderboard_payload(db, a.id)
    assert [r["name"] for r in data["rows"]] == ["Боб", "Алиса"]  # без матчей — не в таблице
    assert [r["rating"] for r in data["rows"]] == ["1120.4", "1107.2"]
    assert data["rows"][1]["is_viewer"] is True
    assert data["rows"][0]["is_viewer"] is False
    assert data["gap"] == "До #1 Боб: 13.2 очка."
    assert data["players_label"] == "2 игрока"
    assert data["empty"] == ""


async def test_payload_without_matches_is_empty(db):
    db.add(_player(ADMIN, "Алиса"))
    await db.commit()
    data = await leaderboard_payload(db, 1)
    assert data["rows"] == []
    assert data["empty"]


# ── HTTP ──────────────────────────────────────────────────────────────────────

@pytest.fixture
async def client(db_factory, webapp_env):
    async with db_factory() as s:
        await _seed(s)
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        yield c


async def test_api_requires_init_data(client):
    resp = await client.get("/api/leaderboard")
    assert resp.status == 401


async def test_api_rejects_forged_init_data(client):
    resp = await client.get("/api/leaderboard", headers={"Authorization": "tma " + _init_data(token="1:X")})
    assert resp.status == 401


async def test_api_closed_for_non_admin_by_default(client):
    resp = await client.get("/api/leaderboard", headers={"Authorization": "tma " + _init_data(2002)})
    assert resp.status == 403


async def test_api_rejects_unregistered_user(client, monkeypatch):
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")
    resp = await client.get("/api/leaderboard", headers={"Authorization": "tma " + _init_data(5555)})
    assert resp.status == 403


async def test_api_returns_table_for_admin(client):
    resp = await client.get("/api/leaderboard", headers={"Authorization": "tma " + _init_data(ADMIN)})
    assert resp.status == 200
    data = await resp.json()
    assert data["rows"][0]["name"] == "Боб"
    assert resp.headers["Cache-Control"] == "no-store"


async def test_index_and_static_are_served(client):
    page = await client.get("/")
    assert page.status == 200
    assert "Рейтинг клуба" in await page.text()
    for path in ("/static/app.css", "/static/app.js"):
        assert (await client.get(path)).status == 200


async def test_api_does_not_write_to_db(client, db_factory):
    """Веб-часть только читает: после запроса в БД те же строки."""
    from sqlalchemy import func, select

    async with db_factory() as s:
        before = (await s.execute(select(func.count()).select_from(Player))).scalar()
    await client.get("/api/leaderboard", headers={"Authorization": "tma " + _init_data(ADMIN)})
    async with db_factory() as s:
        after = (await s.execute(select(func.count()).select_from(Player))).scalar()
    assert before == after


# ── Кнопка в боте ─────────────────────────────────────────────────────────────

def _web_app_buttons(kb):
    return [b for row in kb.inline_keyboard for b in row if b.web_app is not None]


def test_keyboard_without_url_has_no_web_app_button():
    assert _web_app_buttons(leaderboard_kb([])) == []


def test_keyboard_with_url_has_one_web_app_button():
    buttons = _web_app_buttons(leaderboard_kb([], web_app_url="https://club.example/"))
    assert len(buttons) == 1
    assert buttons[0].web_app.url == "https://club.example/"


async def test_leaderboard_screen_shows_button_only_to_admin(db, webapp_env):
    from bot.handlers.leaderboard import _build_leaderboard_screen

    await _seed(db)
    _, admin_kb = await _build_leaderboard_screen(db, ADMIN)
    _, player_kb = await _build_leaderboard_screen(db, 2002)
    assert len(_web_app_buttons(admin_kb)) == 1
    assert _web_app_buttons(player_kb) == []
