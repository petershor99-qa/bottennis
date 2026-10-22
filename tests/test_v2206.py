"""
v2.206.0: уведомления про босс-файт тише. Чемпиону «Тебя догнал по очкам» — не чаще
раза в сутки и без звука; «Трон трещит» убран; общие рассылки про босс-файт
(«Грядёт БОСС-ФАЙТ», «Новый чемпион», «Трон удержан», «Трон освободился») — без звука.
Запуск: pytest tests/test_v2206.py
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

import bot.scheduler as sched
from bot.db.models import Player
from bot.handlers.challenge import send_challenge
from bot.handlers.match_result import _notify_challenger_status_change, confirm_result
from bot.utils import NEWCOMER_THRESHOLD
from tests.conftest import _callback, _player
from tests.test_boss_fight import _accepted_match, _completed, _confirming_state, _seed_matches

WON_2_0 = [{"reporter": 11, "opponent": 5}, {"reporter": 11, "opponent": 5}]
OVERTAKE = "Тебя догнал по очкам"


def _calls_to(bot, telegram_id: int, needle: str):
    return [c for c in bot.send_message.await_args_list if c.args[0] == telegram_id and needle in c.args[1]]


async def _champion_and_runner(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    runner = _player(2, "Runner", rating=1050.0)
    filler = _player(3, "Filler")
    db.add_all([champion, runner, filler])
    await db.flush()
    await _seed_matches(db, runner, filler, NEWCOMER_THRESHOLD, datetime(2026, 6, 1, 12, 0, 0))
    await db.commit()
    return champion, runner


async def test_overtake_notice_is_silent_and_once_a_day(db):
    champion, runner = await _champion_and_runner(db)
    match = SimpleNamespace(is_boss_fight=True)
    bot = AsyncMock()
    await _notify_challenger_status_change(db, bot, match, None, None)
    sent = _calls_to(bot, 1, OVERTAKE)
    assert len(sent) == 1 and sent[0].kwargs.get("disable_notification") is True
    assert _calls_to(bot, 2, "обошёл чемпиона")           # претенденту — как раньше

    bot = AsyncMock()
    await _notify_challenger_status_change(db, bot, match, None, None)   # снова в тот же день
    assert not _calls_to(bot, 1, OVERTAKE)

    champion.throne_warn_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)
    await db.commit()
    bot = AsyncMock()
    await _notify_challenger_status_change(db, bot, match, None, None)   # прошли сутки
    assert len(_calls_to(bot, 1, OVERTAKE)) == 1


async def test_overtake_through_real_match_is_silent(db):
    champion, runner = await _champion_and_runner(db)
    runner.rating = 995.0
    third = _player(4, "Third", rating=900.0)
    db.add(third)
    await db.flush()
    m = await _accepted_match(db, runner, third)
    await db.commit()
    st = await _confirming_state(m.id, runner.id, WON_2_0)
    bot = AsyncMock()
    await confirm_result(_callback(2, f"confirm_{m.id}"), db, st, bot)
    sent = _calls_to(bot, 1, OVERTAKE)
    assert len(sent) == 1 and sent[0].kwargs.get("disable_notification") is True


async def test_boss_fight_outcome_broadcast_is_silent(db):
    champion, runner = await _champion_and_runner(db)
    m = await _accepted_match(db, runner, champion, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(m.id, runner.id, WON_2_0)
    bot = AsyncMock()
    await confirm_result(_callback(2, f"confirm_{m.id}"), db, st, bot)
    sent = [c for c in bot.send_message.await_args_list if "Новый чемпион —" in c.args[1]]
    assert sent and all(c.kwargs.get("disable_notification") is True for c in sent)


async def test_boss_fight_start_broadcast_is_silent(db):
    champion, runner = await _champion_and_runner(db)
    bot = AsyncMock()
    await send_challenge(_callback(2, f"challenge_{champion.id}"), db, bot)
    sent = [c for c in bot.send_message.await_args_list if "Грядёт БОСС-ФАЙТ" in c.args[1]]
    assert sent and all(c.kwargs.get("disable_notification") is True for c in sent)


async def test_auto_release_broadcast_is_silent(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=15)
    async with db_factory() as s:
        champion = _player(1, "Champion", rating=1200.0)
        champion.is_champion = True
        heir = _player(2, "Heir", rating=1100.0)
        s.add_all([champion, heir])
        await s.flush()
        s.add(_completed(champion, heir, champion.id, rc=5.0, when=old))
        await _seed_matches(s, heir, champion, NEWCOMER_THRESHOLD, old - timedelta(days=1))
        await s.commit()
    bot = AsyncMock()
    await sched.check_champion_auto_release(bot)
    sent = [c for c in bot.send_message.await_args_list if "Трон освободился" in c.args[1]]
    assert sent and all(c.kwargs.get("disable_notification") is True for c in sent)
    async with db_factory() as s:
        assert (await s.execute(select(Player).where(Player.id == 2))).scalar_one().is_champion is True


def test_throne_cracking_removed():
    assert not hasattr(sched, "check_throne_cracking")
