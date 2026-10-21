"""
v2.205.0: сообщения бота про трон не обгоняют события — «Трон твой. Пока.» только
тому, кто реально взял трон (победа в босс-файте), а не тому, кто обошёл чемпиона
по очкам; «местную легенду» — про чемпиона, а не про лидера по рейтингу; ничья не
считается поражением в «Вылез из жопы».
Запуск: pytest tests/test_v2205.py
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from bot.handlers.challenge import send_challenge
from bot.handlers.match_result import confirm_result
from bot.utils import NEWCOMER_THRESHOLD
from tests.conftest import _callback, _completed, _player
from tests.test_boss_fight import _accepted_match, _confirming_state, _seed_matches

THRONE = "Трон твой"
LEGEND = "местную легенду"
WON_2_0 = [{"reporter": 11, "opponent": 5}, {"reporter": 11, "opponent": 5}]


def _texts_to(bot, telegram_id: int) -> list[str]:
    return [c.args[1] for c in bot.send_message.await_args_list if c.args[0] == telegram_id]


async def test_throne_egg_only_when_throne_actually_taken(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    m = await _accepted_match(db, challenger_p, champion, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(m.id, challenger_p.id, WON_2_0)
    bot = AsyncMock()
    await confirm_result(_callback(2, f"confirm_{m.id}"), db, st, bot)

    assert challenger_p.is_champion is True
    assert any(THRONE in t for t in _texts_to(bot, 2))
    assert not any(THRONE in t for t in _texts_to(bot, 1))


async def test_no_throne_egg_when_champion_overtaken_by_points_only(db):
    """Скриншот из жалобы: обошёл чемпиона по очкам обычным матчем — трон остаётся у чемпиона."""
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    runner = _player(2, "Runner", rating=995.0)
    third = _player(3, "Third", rating=900.0)
    db.add_all([champion, runner, third])
    await db.flush()
    await _seed_matches(db, runner, third, NEWCOMER_THRESHOLD, datetime(2026, 6, 1, 12, 0, 0))
    m = await _accepted_match(db, runner, third)
    await db.commit()
    st = await _confirming_state(m.id, runner.id, WON_2_0)
    bot = AsyncMock()
    await confirm_result(_callback(2, f"confirm_{m.id}"), db, st, bot)

    assert runner.rating > champion.rating and runner.is_champion is False
    to_runner = _texts_to(bot, 2)
    assert any("обошёл чемпиона" in t for t in to_runner)
    assert not any(THRONE in t for t in to_runner)


async def test_no_throne_egg_when_champion_defends_in_boss_fight(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    m = await _accepted_match(db, champion, challenger_p, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(m.id, champion.id, WON_2_0)
    bot = AsyncMock()
    await confirm_result(_callback(1, f"confirm_{m.id}"), db, st, bot)

    assert not any(THRONE in t for t in _texts_to(bot, 1))


async def test_no_throne_egg_when_champion_wins_regular_match(db):
    """Чемпион, выигравший обычный матч, и так на троне — «Трон твой» не нужен."""
    champion = _player(1, "Champion", rating=1100.0)
    champion.is_champion = True
    other = _player(2, "Other", rating=1000.0)
    db.add_all([champion, other])
    await db.flush()
    m = await _accepted_match(db, champion, other)
    await db.commit()
    st = await _confirming_state(m.id, champion.id, WON_2_0)
    bot = AsyncMock()
    await confirm_result(_callback(1, f"confirm_{m.id}"), db, st, bot)

    assert not any(THRONE in t for t in _texts_to(bot, 1))


async def test_legend_egg_is_about_champion_not_rating_leader(db):
    """Претендент выше чемпиона по очкам: «легенда» — чемпион, а не претендент."""
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    leader = _player(2, "RatingLeader", rating=1050.0)
    caller = _player(3, "Caller", rating=950.0)
    filler = _player(4, "Filler")
    db.add_all([champion, leader, caller, filler])
    await db.flush()
    await _seed_matches(db, leader, filler, NEWCOMER_THRESHOLD, datetime(2026, 6, 1, 12, 0, 0))
    await db.commit()

    bot = AsyncMock()
    await send_challenge(_callback(3, f"challenge_{leader.id}"), db, bot)
    assert not any(LEGEND in t for t in _texts_to(bot, 3))

    # у caller уже есть активный матч с leader — отменяем, чтобы вызвать чемпиона
    from sqlalchemy import delete

    from bot.db.models import Match
    await db.execute(delete(Match).where(Match.status == "accepted"))
    await db.commit()
    bot = AsyncMock()
    await send_challenge(_callback(3, f"challenge_{champion.id}"), db, bot)
    assert any(LEGEND in t for t in _texts_to(bot, 3))


async def test_legend_egg_falls_back_to_rating_without_champion(db):
    top = _player(1, "Top", rating=1200.0)
    caller = _player(2, "Caller", rating=1000.0)
    db.add_all([top, caller])
    await db.flush()
    bot = AsyncMock()
    await send_challenge(_callback(2, f"challenge_{top.id}"), db, bot)
    assert any(LEGEND in t for t in _texts_to(bot, 2))


async def test_comeback_after_draw_is_not_a_loss_streak(db):
    """«Вылез из жопы» — после 3+ поражений подряд; ничья серию поражений обрывает."""
    me = _player(1, "Me", rating=1000.0)
    foe = _player(2, "Foe", rating=1000.0)
    db.add_all([me, foe])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    for i in range(3):                                        # три поражения подряд...
        db.add(_completed(me, foe, foe.id, rc=5.0, when=base + timedelta(days=i)))
    db.add(_completed(me, foe, None, rc=0.0, when=base + timedelta(days=3)))   # ...потом ничья
    await db.flush()
    m = await _accepted_match(db, me, foe)
    await db.commit()
    st = await _confirming_state(m.id, me.id, WON_2_0)
    bot = AsyncMock()
    await confirm_result(_callback(1, f"confirm_{m.id}"), db, st, bot)

    assert not any("Вылез из жопы" in t for t in _texts_to(bot, 1))
