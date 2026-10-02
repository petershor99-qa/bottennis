"""Пулы пасхалок после матча (v2.153.0)."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

import bot.phrases as phrases
from bot.handlers.match_result import (
    _pick_egg,
    _send_loser_eggs,
    _send_time_based_eggs,
    _send_winner_eggs,
    confirm_result,
)
from tests.conftest import _callback, _player
from tests.test_boss_fight import _accepted_match, _confirming_state

EGG_POOLS = {n: getattr(phrases, n) for n in dir(phrases) if n.startswith("EGG_")}

# Прежние (одиночные) фразы остаются ПЕРВЫМИ в пулах — не теряем старые пасхалки
ORIGINALS = {
    "EGG_CLEAN_SWEEP": "💥 FINISH HIM!",
    "EGG_FLAWLESS": "🩸 Flawless Victory",
    "EGG_SHUTOUT": "Читы включил? 🎮",
    "EGG_DEUCE_DECIDER": "⚡ Драматично!",
    "EGG_REVENGE": "⚡ Мы в расчёте",
    "EGG_COMEBACK": "💪 Упал — отжался — победил",
    "EGG_QUICK_REMATCH": "Не наигрался? 😤",
    "EGG_WEEKEND": "Вышел на работу ради тенниса? Уважаемо! 🫡",
    "EGG_FRIDAY_EVENING": "Закрываем неделю красиво 🍻",
    "EGG_NIGHT": "🌙 Тебе точно не спится?",
    "EGG_LOSS_STREAK_3": "💪 Надо собраться",
    "EGG_FIRST_LOSS": "🕶 Добро пожаловать в реальный мир",
}


def test_all_twelve_egg_pools_exist():
    assert set(EGG_POOLS) == set(ORIGINALS)


@pytest.mark.parametrize("name", sorted(ORIGINALS))
def test_pool_keeps_original_first_and_is_grown(name):
    pool = EGG_POOLS[name]
    assert pool[0] == ORIGINALS[name]
    assert len(pool) >= 3
    assert len(pool) == len(set(pool))


@pytest.mark.parametrize("name", sorted(ORIGINALS))
def test_pool_is_html_safe(name):
    for phrase in EGG_POOLS[name]:
        assert "<" not in phrase and ">" not in phrase and "&" not in phrase, phrase
        assert phrase == phrase.strip() and phrase


@pytest.mark.parametrize("name", sorted(ORIGINALS))
def test_pick_is_stable_per_match_and_varies_across_matches(name):
    pool = EGG_POOLS[name]
    assert _pick_egg(pool, 5, "salt") == _pick_egg(pool, 5, "salt")
    seen = {_pick_egg(pool, mid, "salt") for mid in range(80)}
    assert len(seen) >= min(len(pool), 3)
    assert seen <= set(pool)


def _ctx(**kw):
    ctx = {
        "flawless": False, "clean_sweep": False, "shutout": False, "deuce_decider": False,
        "comeback": False, "marathon": False,
        "old_winner_rating": 1000.0, "old_loser_rating": 1000.0, "match_id": 0,
        "previous_wins": 1, "streak": 1, "loss_streak_before": 0,
        "first_blood": False, "revenge": False, "first_time_top1": False, "winner_total": 5,
        "loss_streak": 0, "prev_losses": 1, "loser_total": 5,
    }
    ctx.update(kw)
    return ctx


def _sent(bot):
    return [c.args[1] for c in bot.send_message.await_args_list]


@pytest.mark.parametrize(
    ("flag", "pool"),
    [
        ("flawless", phrases.EGG_FLAWLESS), ("clean_sweep", phrases.EGG_CLEAN_SWEEP),
        ("shutout", phrases.EGG_SHUTOUT), ("deuce_decider", phrases.EGG_DEUCE_DECIDER),
        ("revenge", phrases.EGG_REVENGE), ("comeback", phrases.EGG_COMEBACK),
    ],
)
async def test_winner_egg_text_comes_from_its_pool_and_varies(flag, pool):
    seen = set()
    for mid in range(60):
        winner, loser = _player(1, "A"), _player(2, "B")
        winner.rating = 1013.7
        bot = AsyncMock()
        await _send_winner_eggs(bot, winner, loser, _ctx(**{flag: True, "match_id": mid}))
        texts = _sent(bot)
        assert len(texts) == 1 and texts[0] in pool
        seen.add(texts[0])
    assert len(seen) >= 3


async def test_loser_eggs_use_pools():
    first = AsyncMock()
    await _send_loser_eggs(first, _player(2, "B"), _player(1, "A"), _ctx(prev_losses=0, match_id=7), 1000.0)
    assert _sent(first)[0] in phrases.EGG_FIRST_LOSS
    streak = AsyncMock()
    await _send_loser_eggs(streak, _player(2, "B"), _player(1, "A"), _ctx(loss_streak=3, match_id=7), 1000.0)
    assert _sent(streak)[0] in phrases.EGG_LOSS_STREAK_3


async def test_time_based_eggs_vary_with_match_id_and_both_players_get_same_text():
    seen = set()
    for mid in range(40):
        bot = AsyncMock()
        # суббота, 15:00 МСК
        await _send_time_based_eggs(bot, [_player(1, "A"), _player(2, "B")],
                                    datetime(2026, 6, 6, 12, 0, 0), mid)
        texts = _sent(bot)
        assert len(texts) == 2 and texts[0] == texts[1] and texts[0] in phrases.EGG_WEEKEND
        seen.add(texts[0])
    assert len(seen) >= 3


async def test_clean_sweep_end_to_end_uses_real_match_id(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2)
    await db.commit()
    st = await _confirming_state(
        m.id, p1.id, [{"reporter": 11, "opponent": 5}, {"reporter": 11, "opponent": 6}],
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)
    winner_texts = [c.args[1] for c in bot.send_message.await_args_list if c.args[0] == 1]
    assert any(t in phrases.EGG_CLEAN_SWEEP for t in winner_texts)
    assert _pick_egg(phrases.EGG_CLEAN_SWEEP, m.id, "egg_clean_sweep") in winner_texts
