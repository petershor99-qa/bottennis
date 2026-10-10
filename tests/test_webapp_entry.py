"""
Тесты входов в Mini App из бота (v2.163.0): строка «Открыть приложение» в
главном меню и кнопка меню Telegram «Клуб». Запуск: pytest tests/test_webapp_entry.py
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import MenuButtonCommands, MenuButtonWebApp

from bot.keyboards.inline import main_menu_kb
from bot.webapp.config import WEBAPP_BUTTON_TEXT, WEBAPP_MENU_BUTTON_TEXT
from bot.webapp.menu_button import sync_menu_button

ADMIN = 1001
PLAYER = 2002


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    monkeypatch.delenv("WEBAPP_AUDIENCE", raising=False)


def _rows(kb):
    return [[b.text for b in row] for row in kb.inline_keyboard]


def _app_buttons(kb):
    return [b for row in kb.inline_keyboard for b in row if b.web_app is not None]


# ── Строка в главном меню ─────────────────────────────────────────────────────

def test_menu_without_viewer_has_no_app_row(env):
    assert _app_buttons(main_menu_kb()) == []


def test_menu_for_admin_has_app_row_right_under_challenge(env):
    kb = main_menu_kb(viewer_telegram_id=ADMIN)
    rows = _rows(kb)
    assert rows[1] == [WEBAPP_BUTTON_TEXT]  # вторая строка — сразу под «Вызвать на матч»
    assert "Рейтинг клуба" in rows[2][0]
    [button] = _app_buttons(kb)
    assert button.web_app.url == "https://club.example/"
    assert WEBAPP_BUTTON_TEXT == "📱 Открыть приложение"


def test_menu_for_regular_player_hidden_while_admin_only(env):
    assert _app_buttons(main_menu_kb(viewer_telegram_id=PLAYER)) == []


def test_menu_for_everyone_when_audience_all(env, monkeypatch):
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")
    assert len(_app_buttons(main_menu_kb(viewer_telegram_id=PLAYER))) == 1


def test_menu_has_no_app_row_when_switched_off(env, monkeypatch):
    monkeypatch.delenv("WEBAPP_URL")
    assert _app_buttons(main_menu_kb(viewer_telegram_id=ADMIN)) == []


def test_share_and_rematch_rows_stay_above_the_challenge(env):
    kb = main_menu_kb(share_match_id=5, rematch_opponent_id=7, viewer_telegram_id=ADMIN)
    rows = _rows(kb)
    assert rows[0][0].endswith("Карточка победы")
    assert rows[1][0].endswith("Реванш")
    assert rows[3] == [WEBAPP_BUTTON_TEXT]


# ── Кнопка меню Telegram ──────────────────────────────────────────────────────

def _bot():
    return SimpleNamespace(set_chat_menu_button=AsyncMock())


def _calls(bot):
    return [(c.kwargs.get("chat_id"), c.kwargs["menu_button"]) for c in bot.set_chat_menu_button.call_args_list]


async def test_admin_only_sets_app_button_for_admin_chat_and_default_for_others(env):
    bot = _bot()
    await sync_menu_button(bot)
    default_call, admin_call = _calls(bot)
    assert default_call[0] is None and isinstance(default_call[1], MenuButtonCommands)
    assert admin_call[0] == ADMIN
    assert isinstance(admin_call[1], MenuButtonWebApp)
    assert admin_call[1].text == WEBAPP_MENU_BUTTON_TEXT == "Клуб"
    assert admin_call[1].web_app.url == "https://club.example/"


async def test_audience_all_sets_app_button_for_everyone(env, monkeypatch):
    monkeypatch.setenv("WEBAPP_AUDIENCE", "all")
    bot = _bot()
    await sync_menu_button(bot)
    first = _calls(bot)[0]
    assert first[0] is None and isinstance(first[1], MenuButtonWebApp)


async def test_switched_off_restores_command_menu_everywhere(env, monkeypatch):
    monkeypatch.delenv("WEBAPP_URL")
    bot = _bot()
    await sync_menu_button(bot)
    assert all(isinstance(btn, MenuButtonCommands) for _, btn in _calls(bot))
    assert [chat for chat, _ in _calls(bot)] == [None, ADMIN]


async def test_telegram_failure_does_not_raise(env, caplog):
    import logging

    bot = SimpleNamespace(set_chat_menu_button=AsyncMock(side_effect=RuntimeError("Telegram недоступен")))
    with caplog.at_level(logging.WARNING):
        await sync_menu_button(bot)
    assert any("кнопку меню" in r.message for r in caplog.records)


async def test_no_admin_id_touches_only_default(env, monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "")
    bot = _bot()
    await sync_menu_button(bot)
    assert [chat for chat, _ in _calls(bot)] == [None]


# ── Склонение серий в тексте бота (v2.164.1) ──────────────────────────────────

@pytest.mark.parametrize("n,expected", [
    (2, "2 победы подряд"), (3, "3 победы подряд"), (5, "5 побед подряд"),
    (11, "11 побед подряд"), (21, "21 победа подряд"), (22, "22 победы подряд"),
])
def test_profile_streak_lines_decline_wins(n, expected):
    """РЕГРЕССИЯ: было «3 побед подряд», «4 побед подряд» — без склонения."""
    from bot.handlers.profile import _stats_groups

    s = {"recent_7": [], "streak": n, "loss_streak": 0, "best_streak": n + 1,
         "activity_streak_days": 0, "best_opp": None, "nemesis": None, "top_opp": None,
         "unresolved_debts": [], "debtors": [], "trend_30d": None, "avg_delta": None, "best_win": None,
         "total_earned": 0, "total_lost": 0, "boss_fights_played": 0, "total_sets_played": 0,
         "deuce_total": 0, "first_set_conv": None, "fav_format": None, "best_day": None, "fav_hour": None,
         "lucky_day": None, "post_loss": None, "favorite_score": None, "style_insight": None}
    form = _stats_groups(SimpleNamespace(id=1, peak_rating=None, rating=1000.0), s)["form"]
    assert any(expected in line for line in form)


@pytest.mark.parametrize("n,expected", [(2, "2 поражения подряд"), (4, "4 поражения подряд"), (5, "5 поражений подряд")])
def test_profile_loss_streak_declines_losses(n, expected):
    from bot.handlers.profile import _stats_groups

    s = {"recent_7": [], "streak": 0, "loss_streak": n, "best_streak": 0, "activity_streak_days": 0,
         "best_opp": None, "nemesis": None, "top_opp": None, "unresolved_debts": [], "debtors": [],
         "trend_30d": None, "avg_delta": None, "best_win": None, "total_earned": 0, "total_lost": 0,
         "boss_fights_played": 0, "total_sets_played": 0, "deuce_total": 0, "first_set_conv": None,
         "fav_format": None, "best_day": None, "fav_hour": None, "lucky_day": None, "post_loss": None,
         "favorite_score": None, "style_insight": None}
    form = _stats_groups(SimpleNamespace(id=1, peak_rating=None, rating=1000.0), s)["form"]
    assert any(expected in line for line in form)
