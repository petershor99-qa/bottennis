"""
v2.220.0: устойчивость веб-эндпоинтов — гигантские числа из URL/JSON дают
400/404 вместо 500 (OverflowError на привязке к SQLite), занятая база в
notifications_set отвечает 503 (паттерн reactions.py), «С возвращением» не
рисуется на чужом экране (см. e2e).
Запуск: pytest tests/test_v2220.py
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.exc import OperationalError

import bot.webapp.reactions as reactions
import bot.webapp.server as server
from bot.webapp.server import _int_param
from tests.test_v2203 import ADMIN, app_on  # noqa: F401 — фикстура окружения приложения


def test_int_param_ranges():
    assert _int_param("42") == 42
    assert _int_param("0", lo=0) == 0
    assert _int_param("9" * 30) is None            # гигантское — вне диапазона
    assert _int_param("-5") is None                # ниже lo=1
    assert _int_param("abc") is None
    assert _int_param("", lo=0) is None


async def test_target_giant_id_returns_none():
    request = SimpleNamespace(match_info={"player_id": "9" * 30})
    assert await server._target(request, None) is None


async def test_since_giant_timestamp_returns_none():
    request = SimpleNamespace(query={"t": "9" * 25})
    assert await server.since(request, None, None) is None


async def test_react_giant_match_id_returns_none():
    request = SimpleNamespace(match_info={"match_id": "9" * 30},
                              json=AsyncMock(return_value={"kind": "fire"}))
    assert await server.react(request, None, None) is None
    request.json.assert_not_awaited()              # отсекается до чтения тела


async def test_challenge_open_giant_opponent_id_400():
    request = SimpleNamespace(app={server.BOT_KEY: SimpleNamespace()},
                              json=AsyncMock(return_value={"opponent_id": 10**20}))
    resp = await server.challenge_open(request, None, SimpleNamespace(telegram_id=42))
    assert resp.status == 400


async def test_feed_target_giant_id_is_ignored():
    session = SimpleNamespace()
    assert await reactions.toggle_feed_reaction(session, "ach:" + "9" * 30, None, "fire") is None


async def test_notifications_set_busy_db_503(monkeypatch):
    """База занята ботом — переключатель отвечает 503 с rollback, а не 500."""
    monkeypatch.setattr("bot.services.digests.set_digest_muted",
                        lambda viewer, key, muted: True)
    session = SimpleNamespace(
        commit=AsyncMock(side_effect=OperationalError("stmt", {}, Exception("busy"))),
        rollback=AsyncMock())
    request = SimpleNamespace(json=AsyncMock(return_value={"key": "day", "muted": True}))
    viewer = SimpleNamespace(telegram_id=ADMIN, muted_digests="")
    resp = await server.notifications_set(request, session, viewer)
    assert resp.status == 503
    session.rollback.assert_awaited_once()
