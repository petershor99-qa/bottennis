"""
v2.192.0: меню клуба по использованию («частые» и «Ещё»), серия игровых дней
в профиле, подсказка про жесты, «С возвращением» с картинкой заставки и
карточка «Пока тебя не было», которая больше не пропадает при старте из снимка.
Запуск: pytest tests/test_v2192.py
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from bot.db.models import Match, MatchStatus, UsageEvent
from bot.webapp import api, menu
from tests.conftest import _player

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")

LINKS = [{"route": r, "title": r} for r in
         ("summary/day", "month", "records", "matches", "activity/club", "throne", "chronicle", "scores", "pairs")]


def test_rank_links_by_usage():
    counts = {"app_pairs": 30, "app_throne": 12, "app_summary_day": 12, "app_activity_club": 5}
    ranked = menu.rank_links(LINKS, counts)
    routes = [x["route"] for x in ranked]
    assert routes[:4] == ["pairs", "summary/day", "throne", "activity/club"]   # при равенстве — прежний порядок
    assert [bool(x.get("more")) for x in ranked] == [False] * menu.OFTEN + [True] * (len(LINKS) - menu.OFTEN)
    assert "more" not in LINKS[0]                                              # исходный список не меняется


def test_rank_links_keeps_order_with_little_data():
    ranked = menu.rank_links(LINKS, {"app_pairs": menu.MIN_EVENTS - 1})
    assert [x["route"] for x in ranked] == [x["route"] for x in LINKS]
    assert ranked[menu.OFTEN].get("more") is True


def test_screen_action_matches_page_names():
    assert menu.screen_action("summary/day") == "app_summary_day"
    assert menu.screen_action("activity/club") == "app_activity_club"
    assert menu.screen_action("throne") == "app_throne"


async def test_usage_counts_last_30_days_only_app(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add_all([UsageEvent(user_id=1, action="app_pairs", created_at=now),
                UsageEvent(user_id=2, action="app_pairs", created_at=now - timedelta(days=3)),
                UsageEvent(user_id=1, action="app_pairs", created_at=now - timedelta(days=40)),
                UsageEvent(user_id=1, action="menu_play", created_at=now)])
    await db.commit()
    assert await menu.usage_counts(db) == {"app_pairs": 2}


def test_play_day_streak_skips_weekends():
    fri, mon, tue = date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13)
    thu = date(2026, 10, 8)
    assert api.play_day_streak({thu, fri, mon, tue}, tue) == 4                 # выходные не рвут
    assert api.play_day_streak({thu, fri, mon}, tue) == 3                      # сегодня ещё не играл — жива
    assert api.play_day_streak({thu, mon, tue}, tue) == 2                      # пропущена пятница
    assert api.play_day_streak({fri, date(2026, 10, 10), mon}, mon) == 3       # матч в субботу продлевает
    assert api.play_day_streak(set(), tue) == 0


async def test_profile_head_days_streak(db):
    a, b = _player(1, "Алиса"), _player(2, "Боб")
    db.add_all([a, b])
    await db.flush()
    today = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
    days, d = [], today
    while len(days) < 4:                                                       # четыре рабочих дня подряд
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    for day in days:
        at = datetime(day.year, day.month, day.day, 9, 0)                      # 12:00 МСК
        db.add(Match(challenger_id=a.id, challenged_id=b.id, status=MatchStatus.completed, winner_id=a.id,
                     sets_data=[{"w": 11, "l": 5}], rating_change=5.0, completed_at=at))
    await db.commit()
    head = (await api.player_payload(db, a, a))["head"]
    assert head["days_streak"] == "4 игровых дня подряд"


def test_client_wiring():
    home = (SRC / "10-home.js").read_text(encoding="utf-8")
    assert 'var CLUB_MORE_KEY = "club_more_v1";' in home and '"Ещё " + extra' in home
    assert 'h.days_streak' in (SRC / "12-profile.js").read_text(encoding="utf-8")
    rating = (SRC / "11-rating.js").read_text(encoding="utf-8")
    assert 'var GESTURE_HINT_KEY = "gesture_hint_v1";' in rating and '"ontouchstart" in window' in rating
    assert 'storageSet("gesture_hint_v1", "1");' in (SRC / "22-gestures.js").read_text(encoding="utf-8")
    ret = (SRC / "23-return.js").read_text(encoding="utf-8")
    assert '"С возвращением!"' in ret and 'link[rel="preload"][as="image"]' in ret
    # карточка переживает перерисовку главной после старта из снимка
    assert "sinceState = { d: d, closed: false };" in ret
    assert 'if (sinceState && !sinceState.closed && !c.querySelector(".since")) sinceCard(c, sinceState.d);' in ret
    for sel in (".club-group:not(.is-open) .club-tile-more", ".hero-streak", ".since-pic", ".since-hello"):
        assert sel in CSS
    assert 'id="ti-hand-finger"' in (ROOT / "webapp" / "static" / "icons.svg").read_text(encoding="utf-8")
