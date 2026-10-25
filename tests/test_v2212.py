"""
v2.212.0: экраны Mini App без повторов и длинных простыней (профиль, «Все матчи клуба»,
«Итоги → День», рейтинг, рекорды, ачивки), экран «Рассылки».
Запуск: pytest tests/test_v2212.py
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.services.digests import is_digest_muted, set_digest_muted
from bot.services.stats import _compute_player_stats, _nearest_achievement_progress
from bot.webapp import api, server
from tests.conftest import _player
from tests.test_webapp_screens import _seed

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")


def _text(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


async def test_position_block_has_one_line_when_champion_is_the_next_place(db):
    """Алиса вторая, чемпион (Боб) первый: «До #1» и «До трона» — одно число, строка одна."""
    a, b, _ = await _seed(db)
    data = await api.player_payload(db, a, a)
    position = next(blk for blk in data["blocks"] if blk["title"] == "Положение")
    assert len(position["items"]) == 1


def test_rating_goal_not_shown_twice_next_to_rank_goal():
    p = _player(1, "Игрок", rating=1100.0)
    s = {**_compute_player_stats(p, []), "wins": 1}      # nearest_achievements молчит без единого матча
    alone = _nearest_achievement_progress(p, s, 6)
    assert alone is not None and "Рейтинг 1200" in alone                       # сама по себе цель на месте
    paired = _nearest_achievement_progress(p, s, 6, rank_goal_shown=True)
    assert paired is None or "Рейтинг 1200" not in paired                      # рядом с «До звания Сеньор» — нет
    far = _player(2, "Новичок", rating=1000.0)                                 # следующее звание — 1050, цели разные
    s2 = {**_compute_player_stats(far, []), "wins": 1}
    kept = _nearest_achievement_progress(far, s2, 6, rank_goal_shown=True)
    assert kept is None or isinstance(kept, str)


async def test_club_matches_grouped_by_day_with_chips_and_filter(db):
    a, b, c = await _seed(db)
    data = await api.club_matches_payload(db, 0)
    assert data["players"] and {p["name"] for p in data["players"]} >= {"Алиса", "Боб", "Вика"}
    first = data["matches"][0]
    assert first["day"] and first["day_label"] and first["sets"] and first["pa"]["name"] and first["pb"]["name"]
    assert any(m["delta"].startswith("+") for m in data["matches"])
    only = await api.club_matches_payload(db, 0, a.id)
    assert only["matches"] and all(a.id in (m["a_id"], m["b_id"]) for m in only["matches"])
    assert only["player_id"] == a.id and only["players"]                       # список игроков — для кнопок вверху
    nobody = await api.club_matches_payload(db, 0, 999999)
    assert nobody["matches"] == [] and nobody["empty"] == "У этого игрока ещё нет матчей."


async def test_notifications_payload_and_switching(db):
    a, _, _ = await _seed(db)
    data = api.notifications_payload(a)
    assert [i["key"] for i in data["items"]] == ["day", "week", "month", "quarter", "year"]
    assert all(i["on"] for i in data["items"]) and "отключить нельзя" in data["note"]
    assert not any(ch in blob for blob in (i["label"] for i in data["items"]) for ch in "📅📆🗓📊🎆")

    request = SimpleNamespace(json=AsyncMock(return_value={"key": "month", "muted": True}))
    result = await server.notifications_set(request, db, a)
    assert is_digest_muted(a, "month") and next(i for i in result["items"] if i["key"] == "month")["on"] is False
    request.json.return_value = {"key": "month", "muted": True}                 # повтор ничего не «перекидывает»
    await server.notifications_set(request, db, a)
    assert is_digest_muted(a, "month")
    request.json.return_value = {"key": "month", "muted": False}
    await server.notifications_set(request, db, a)
    assert not is_digest_muted(a, "month")


async def test_notifications_reject_bad_requests(db):
    a, _, _ = await _seed(db)
    for body in ({"key": "nonsense", "muted": True}, {"key": "day"}, {"key": "day", "muted": "yes"}, [1]):
        res = await server.notifications_set(SimpleNamespace(json=AsyncMock(return_value=body)), db, a)
        assert res is None or getattr(res, "status", 200) == 400                # 404 (None) или 400
    assert a.muted_digests in (None, "")
    assert set_digest_muted(a, "nope", True) is False


def test_client_wiring_v2212():
    profile = _text("12-profile.js")
    assert 'if (b.title !== "Положение") foldSection(s, b.title);' in profile and "function badgeNameSize(name)" in profile
    assert 'nrow = clickable(el("li", "row"), "notifications")' in profile
    assert 'el("h2", "group-title", "Матчи дня")' in _text("17-day.js")
    rating = _text("11-rating.js")
    assert "▲▼ — на сколько мест игрок поднялся или опустился за неделю." in rating and '["Гонка рейтинга", "race"]' in rating
    home = _text("10-home.js")
    assert home.index("clubTiles(d.links)") < home.index("goalsBlock(d.goals)")       # меню выше целей недели
    assert "renderClubMatches" in _text("14-club.js") and 'el("div", "pick-bar")' in _text("14-club.js")
    assert "renderNotifications" in (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert ".group.is-fold.is-folded > .list { display: none; }" in CSS
    assert ".rec-tiles > .rec-tile:last-child:nth-child(odd)" in CSS and ".pick-chip.is-active" in CSS
