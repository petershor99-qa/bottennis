"""
Тесты фичи «Босс-файт за 1-е место».
Запуск: pytest tests/test_boss_fight.py -v
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base, ChampionReign, Match, MatchStatus, Player
from bot.handlers.challenge import send_challenge, show_players_for_challenge
from bot.handlers.history import show_h2h
from bot.handlers.leaderboard import show_club_records, show_leaderboard
from bot.handlers.match_result import confirm_result, finish_sets
from bot.handlers.profile import show_my_stats, show_player_profile
from bot.keyboards.inline import players_list_kb, rematch_kb
from bot.services.achievements import get_achievements
from bot.services.rating import calculate_rating_change
from bot.states.states import MatchResultStates
from bot.utils import (
    NEWCOMER_THRESHOLD,
    bootstrap_champion,
    boss_fight_rematch_blocked,
    compute_ranks,
    get_challenger,
    get_champion,
    longest_champion_reign,
    most_boss_fight_defenses,
    try_transfer_champion,
)

# ── Фикстуры и хелперы ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _player(tid: int, name: str, rating: float = 1000.0) -> Player:
    return Player(
        telegram_id=tid, display_name=name, rating=rating,
        achievements="[]", backfill_version=0,
    )


def _state(user_id: int = 1, chat_id: int = 1):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    key = StorageKey(bot_id=1, chat_id=chat_id, user_id=user_id)
    return FSMContext(storage=MemoryStorage(), key=key)


def _callback(user_id: int, data: str) -> AsyncMock:
    cb = AsyncMock()
    cb.from_user = SimpleNamespace(id=user_id)
    cb.data = data
    cb.message = AsyncMock()
    cb.message.chat = SimpleNamespace(id=user_id)
    cb.message.message_id = 555
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _completed(challenger: Player, challenged: Player, winner_id: int, when: datetime) -> Match:
    return Match(
        challenger_id=challenger.id, challenged_id=challenged.id,
        status=MatchStatus.completed, winner_id=winner_id,
        sets_data=[{"w": 11, "l": 5}], rating_change=5.0, completed_at=when,
    )


async def _accepted_match(db, challenger: Player, challenged: Player, is_boss_fight: bool = False) -> Match:
    m = Match(
        challenger_id=challenger.id, challenged_id=challenged.id,
        status=MatchStatus.accepted, accepted_at=datetime(2026, 6, 1, 12, 0, 0),
        is_boss_fight=is_boss_fight,
    )
    db.add(m)
    await db.flush()
    return m


async def _seed_matches(db, player: Player, opponent: Player, n: int, base: datetime) -> None:
    """Дать player n завершённых побед над opponent — для порога NEWCOMER_THRESHOLD."""
    for i in range(n):
        db.add(_completed(player, opponent, player.id, base - timedelta(days=n - i)))


async def _confirming_state(match_id: int, reporter_id: int, sets: list[dict], is_draw: bool = False):
    st = _state(reporter_id)
    await st.set_state(MatchResultStates.confirming)
    await st.update_data(
        match_id=match_id, reporter_player_id=reporter_id,
        sets_data=sets, is_draw=is_draw,
    )
    return st


# ── A. Ранги / отображение ────────────────────────────────────────────────────

async def test_compute_ranks_champion_pinned_to_rank_1_despite_lower_rating(db):
    p1 = _player(1, "Champion", rating=900.0)
    p2 = _player(2, "HigherRated", rating=1200.0)
    db.add_all([p1, p2])
    await db.flush()
    counts = {p1.id: 1, p2.id: 1}

    ranks = compute_ranks([p1, p2], counts, champion_id=p1.id)
    assert ranks[p1.id] == 1
    assert ranks[p2.id] == 2


async def test_compute_ranks_none_champion_id_is_pure_rating_sort(db):
    p1 = _player(1, "Low", rating=900.0)
    p2 = _player(2, "High", rating=1200.0)
    db.add_all([p1, p2])
    await db.flush()
    counts = {p1.id: 1, p2.id: 1}

    ranks = compute_ranks([p1, p2], counts, champion_id=None)
    assert ranks[p2.id] == 1
    assert ranks[p1.id] == 2


async def test_compute_ranks_champion_with_zero_matches_falls_back_to_rating(db):
    """Чемпион без сыгранных матчей (не должно происходить в проде, но
    функция не должна падать) — просто не входит в выдачу, как и раньше."""
    p1 = _player(1, "ZeroMatchChampion", rating=900.0)
    p2 = _player(2, "Played", rating=1000.0)
    db.add_all([p1, p2])
    await db.flush()
    counts = {p2.id: 1}   # у p1 — 0 матчей

    ranks = compute_ranks([p1, p2], counts, champion_id=p1.id)
    assert p1.id not in ranks
    assert ranks[p2.id] == 1


def test_players_list_kb_champion_and_challenger_badges():
    p1 = _player(1, "Champion", rating=1000.0)
    p2 = _player(2, "Challenger", rating=1100.0)
    p1.id, p2.id = 1, 2
    kb = players_list_kb(
        [p1, p2], exclude_telegram_id=999,
        champion_id=1, challenger_id=2,
    )
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("👑" in t for t in texts)
    assert any("🗡" in t for t in texts)


def test_players_list_kb_boss_fight_shortcut_row():
    p1 = _player(1, "Someone", rating=1000.0)
    p1.id = 1
    kb = players_list_kb(
        [p1], exclude_telegram_id=999,
        boss_fight_target=(42, "Чемпион"),
    )
    first_row_texts = [btn.text for btn in kb.inline_keyboard[0]]
    assert any("БОСС-ФАЙТ" in t and "Чемпион" in t for t in first_row_texts)


# ── B. Претендент / bootstrap ─────────────────────────────────────────────────

async def test_get_challenger_none_when_no_champion(db):
    p1 = _player(1, "Alice", rating=1200.0)
    db.add(p1)
    await db.flush()
    assert await get_challenger(db, None) is None


async def test_get_challenger_none_when_nobody_above_champion(db):
    champion = _player(1, "Champion", rating=1200.0)
    champion.is_champion = True
    p2 = _player(2, "Lower", rating=1000.0)
    db.add_all([champion, p2])
    await db.flush()
    assert await get_challenger(db, champion) is None


async def test_get_challenger_none_when_above_rating_but_below_match_threshold(db):
    """Ключевой нюанс постановки: обошёл по очкам, но <15 матчей → претендента
    НЕТ ВООБЩЕ (не откатывается на следующего по рейтингу)."""
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    newcomer = _player(2, "Newcomer", rating=1340.0)
    db.add_all([champion, newcomer])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    await _seed_matches(db, newcomer, champion, 8, base)   # только 8 < 15
    await db.commit()

    assert await get_challenger(db, champion) is None


async def test_get_challenger_returns_eligible_top_rated_above_champion(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    eligible = _player(2, "Eligible", rating=1050.0)
    db.add_all([champion, eligible])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    await _seed_matches(db, eligible, champion, NEWCOMER_THRESHOLD, base)
    await db.commit()

    result = await get_challenger(db, champion)
    assert result is not None
    assert result.id == eligible.id


async def test_get_challenger_tie_break_by_smaller_id(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    a = _player(2, "A", rating=1050.0)
    b = _player(3, "B", rating=1050.0)
    db.add_all([champion, a, b])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    await _seed_matches(db, a, champion, NEWCOMER_THRESHOLD, base)
    await _seed_matches(db, b, champion, NEWCOMER_THRESHOLD, base)
    await db.commit()

    result = await get_challenger(db, champion)
    assert result.id == a.id   # меньший id при равном рейтинге


async def test_bootstrap_champion_noop_when_no_matches(db):
    p1 = _player(1, "Alice")
    db.add(p1)
    await db.flush()
    await bootstrap_champion(db)
    assert await get_champion(db) is None


async def test_bootstrap_champion_assigns_top_rated_among_played(db):
    p1 = _player(1, "Low", rating=900.0)
    p2 = _player(2, "High", rating=1200.0)
    p3 = _player(3, "NeverPlayed", rating=1500.0)
    db.add_all([p1, p2, p3])
    await db.flush()
    db.add(_completed(p1, p2, p2.id, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()

    await bootstrap_champion(db)

    champion = await get_champion(db)
    assert champion is not None
    assert champion.id == p2.id   # топ по рейтингу СРЕДИ ИГРАВШИХ, не p3


async def test_bootstrap_champion_idempotent(db):
    p1 = _player(1, "Alice", rating=1000.0)
    p2 = _player(2, "Bob", rating=1200.0)
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p2.id, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()
    p1.is_champion = True   # руками назначаем не топа чемпионом
    await db.commit()

    await bootstrap_champion(db)

    assert p1.is_champion is True    # не переопределилось
    assert p2.is_champion is False


async def test_bootstrap_champion_backfills_reign_for_pre_existing_champion(db):
    """Апгрейд с версии до ChampionReign: чемпион уже назначен старым кодом
    (bootstrap_champion ещё ни разу не заводил запись правления) — при первом
    же запуске новой версии должна открыться запись, иначе «Дольше всех
    лидировал» никогда не появится для уже действующих клубов."""
    p1, p2 = _player(1, "Alice", rating=1200.0), _player(2, "Bob", rating=1000.0)
    p1.is_champion = True   # как будто назначено старым кодом до апдейта
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()

    assert (await db.execute(select(ChampionReign))).scalars().first() is None

    await bootstrap_champion(db)

    assert p1.is_champion is True   # чемпион не поменялся
    reigns = (await db.execute(select(ChampionReign))).scalars().all()
    assert len(reigns) == 1
    assert reigns[0].player_id == p1.id
    assert reigns[0].ended_at is None

    result = await longest_champion_reign(db)
    assert result is not None and result[0] == p1.id


# ── C. Механика матча ─────────────────────────────────────────────────────────

async def test_send_challenge_marks_boss_fight_for_champion_challenger_pair(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=1050.0)
    filler = _player(3, "Filler")
    db.add_all([champion, challenger_p, filler])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    await _seed_matches(db, challenger_p, filler, NEWCOMER_THRESHOLD, base)
    await db.commit()

    cb, bot = _callback(2, f"challenge_{champion.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    r = await db.execute(select(Match).where(Match.status == MatchStatus.accepted))
    match = r.scalar_one()
    assert match.is_boss_fight is True


async def test_send_challenge_regular_match_is_not_boss_fight(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    cb, bot = _callback(1, f"challenge_{p2.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    r = await db.execute(select(Match).where(Match.status == MatchStatus.accepted))
    match = r.scalar_one()
    assert match.is_boss_fight is False


async def test_boss_fight_draw_blocked_in_finish_sets(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2, is_boss_fight=True)
    await db.commit()

    st = _state(1)
    await st.set_state(MatchResultStates.entering_set_score)
    await st.update_data(
        sets_data=[{"reporter": 11, "opponent": 9}, {"reporter": 8, "opponent": 11}],
        match_id=m.id,
    )
    cb = _callback(1, f"finish_sets_{m.id}")
    await finish_sets(cb, st, db)

    call = cb.answer.await_args
    assert call.kwargs.get("show_alert") is True
    assert "ничьей" in call.args[0]
    assert await st.get_state() == MatchResultStates.entering_set_score   # не продвинулись


async def test_boss_fight_draw_blocked_in_confirm_result_safety_net(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2, is_boss_fight=True)
    await db.commit()

    st = await _confirming_state(
        m.id, p1.id,
        [{"reporter": 11, "opponent": 9}, {"reporter": 8, "opponent": 11}],
        is_draw=True,
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    call = cb.answer.await_args
    assert call.kwargs.get("show_alert") is True

    r = await db.execute(select(Match).where(Match.id == m.id))
    fresh = r.scalar_one()
    assert fresh.status == MatchStatus.accepted   # CAS-guard не тронут


async def test_boss_fight_delta_doubled_and_ignores_repeat_multiplier(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=1000.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    # 3 победы подряд challenger_p над champion — вне боссфайта дал бы repeat ×0.85
    for i in range(3):
        db.add(_completed(challenger_p, champion, challenger_p.id, base - timedelta(days=3 - i)))
    await db.commit()

    m = await _accepted_match(db, challenger_p, champion, is_boss_fight=True)
    await db.commit()
    sets = [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}]
    st = await _confirming_state(m.id, challenger_p.id, sets)
    cb, bot = _callback(2, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    raw = calculate_rating_change(1000.0, 1000.0, [{"w": 11, "l": 0}, {"w": 11, "l": 0}])
    # newcomer_bonus ×1.2 (challenger_p имеет только 3 матча < 15), repeat ×1.0 (боссфайт), ×2.0 boss mult
    expected = round(raw * 1.2 * 1.0 * 2.0, 1)

    r = await db.execute(select(Match).where(Match.id == m.id))
    fresh = r.scalar_one()
    assert fresh.rating_change == expected


async def test_boss_fight_champion_transfer_on_challenger_win(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    m = await _accepted_match(db, challenger_p, champion, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(
        m.id, challenger_p.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(2, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert challenger_p.is_champion is True
    assert champion.is_champion is False
    texts = [c.args[1] for c in bot.send_message.await_args_list]
    assert any("Новый чемпион" in t for t in texts)


async def test_boss_fight_no_transfer_on_champion_defense(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    m = await _accepted_match(db, champion, challenger_p, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(
        m.id, champion.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert champion.is_champion is True
    assert challenger_p.is_champion is False
    texts = [c.args[1] for c in bot.send_message.await_args_list]
    assert any("Трон удержан" in t for t in texts)
    assert "throne_defended" in get_achievements(champion)


async def test_boss_fight_challenger_gets_throne_denied_on_defense(db):
    """Претендент, проигравший боссфайт, получает 'throne_denied' — чемпион
    получает 'throne_defended' одновременно (та же ветка confirm_result)."""
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    m = await _accepted_match(db, champion, challenger_p, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(
        m.id, champion.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert "throne_denied" in get_achievements(challenger_p)
    assert "throne_denied" not in get_achievements(champion)


async def test_boss_fight_winner_does_not_get_throne_denied_on_transfer(db):
    """При смене трона (претендент победил) 'throne_denied' не выдаётся никому —
    achievements не относится к сценарию 'трон устоял'."""
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    m = await _accepted_match(db, challenger_p, champion, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(
        m.id, challenger_p.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(2, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert "throne_denied" not in get_achievements(champion)
    assert "throne_denied" not in get_achievements(challenger_p)


async def test_regular_match_loss_does_not_grant_throne_denied(db):
    """Обычное (не боссфайт) поражение не выдаёт 'throne_denied'."""
    p1 = _player(1, "Alice", rating=1000.0)
    p2 = _player(2, "Bob", rating=1000.0)
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2, is_boss_fight=False)
    await db.commit()
    st = await _confirming_state(m.id, p1.id, [{"reporter": 5, "opponent": 11}])
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert "throne_denied" not in get_achievements(p1)
    assert "throne_denied" not in get_achievements(p2)


# ── D. Блокировка реванша ─────────────────────────────────────────────────────

async def test_boss_fight_rematch_blocked_right_after_completion(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    m = await _accepted_match(db, champion, challenger_p, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(
        m.id, champion.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert await boss_fight_rematch_blocked(db, champion.id, challenger_p.id) is True

    cb2, bot2 = _callback(1, f"challenge_{challenger_p.id}"), AsyncMock()
    await send_challenge(cb2, db, bot2)
    cb2.answer.assert_awaited_once_with("Сначала сыграй с кем-нибудь другим.", show_alert=True)


async def test_boss_fight_rematch_unblocked_after_non_champion_plays_third(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    third = _player(3, "Third")
    db.add_all([champion, challenger_p, third])
    await db.flush()
    m = await _accepted_match(db, champion, challenger_p, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(
        m.id, champion.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)   # champion защитился → non-champion = challenger_p

    m2 = await _accepted_match(db, challenger_p, third)
    await db.commit()
    st2 = await _confirming_state(
        m2.id, challenger_p.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb2, bot2 = _callback(2, f"confirm_{m2.id}"), AsyncMock()
    await confirm_result(cb2, db, st2, bot2)

    assert await boss_fight_rematch_blocked(db, champion.id, challenger_p.id) is False


async def test_boss_fight_rematch_not_unblocked_by_champion_playing_third(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    third = _player(3, "Third")
    db.add_all([champion, challenger_p, third])
    await db.flush()
    m = await _accepted_match(db, champion, challenger_p, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(
        m.id, champion.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)   # champion защитился

    m2 = await _accepted_match(db, champion, third)
    await db.commit()
    st2 = await _confirming_state(
        m2.id, champion.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb2, bot2 = _callback(1, f"confirm_{m2.id}"), AsyncMock()
    await confirm_result(cb2, db, st2, bot2)   # чемпион играет с третьим — не должно снимать блок

    assert await boss_fight_rematch_blocked(db, champion.id, challenger_p.id) is True


async def test_declined_boss_fight_does_not_block_rematch(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.declined, is_boss_fight=True,
        completed_at=datetime(2026, 6, 1, 12, 0, 0),
    ))
    await db.commit()

    assert await boss_fight_rematch_blocked(db, p1.id, p2.id) is False


def test_rematch_kb_hides_button_when_blocked():
    kb = rematch_kb(2, can_rematch=False)
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "⚔️ Реванш" not in texts


def test_rematch_kb_shows_button_by_default():
    kb = rematch_kb(2)
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "⚔️ Реванш" in texts


async def test_confirm_result_hides_rematch_button_after_boss_fight(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    m = await _accepted_match(db, champion, challenger_p, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(
        m.id, champion.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "⚔️ Реванш" not in texts


# ── E. Авто-освобождение трона ────────────────────────────────────────────────

async def _sched_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def test_champion_auto_release_transfers_after_7_days_inactive(monkeypatch):
    import bot.scheduler as sched

    engine, factory = await _sched_db()
    monkeypatch.setattr(sched, "async_session", factory)

    old_completed = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    async with factory() as s:
        champion = _player(1, "Champion", rating=1200.0)
        champion.is_champion = True
        heir = _player(2, "Heir", rating=1100.0)
        s.add_all([champion, heir])
        await s.flush()
        s.add(_completed(champion, heir, champion.id, old_completed))
        await _seed_matches(s, heir, champion, NEWCOMER_THRESHOLD, old_completed - timedelta(days=1))
        await s.commit()

    bot = AsyncMock()
    await sched.check_champion_auto_release(bot)

    async with factory() as s:
        r = await s.execute(select(Player).where(Player.id == 1))
        champion_after = r.scalar_one()
        r2 = await s.execute(select(Player).where(Player.id == 2))
        heir_after = r2.scalar_one()

    await engine.dispose()

    assert champion_after.is_champion is False
    assert heir_after.is_champion is True
    texts = [c.args[1] for c in bot.send_message.await_args_list]
    assert any("Трон освободился" in t for t in texts)


async def test_champion_auto_release_no_candidates_keeps_throne(monkeypatch):
    import bot.scheduler as sched

    engine, factory = await _sched_db()
    monkeypatch.setattr(sched, "async_session", factory)

    old_completed = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    async with factory() as s:
        champion = _player(1, "Champion", rating=1200.0)
        champion.is_champion = True
        underqualified = _player(2, "Underqualified", rating=1100.0)
        s.add_all([champion, underqualified])
        await s.flush()
        s.add(_completed(champion, underqualified, champion.id, old_completed))
        # только 5 матчей < NEWCOMER_THRESHOLD — не хватает на наследование
        await _seed_matches(s, underqualified, champion, 5, old_completed - timedelta(days=1))
        await s.commit()

    bot = AsyncMock()
    await sched.check_champion_auto_release(bot)

    async with factory() as s:
        r = await s.execute(select(Player).where(Player.id == 1))
        champion_after = r.scalar_one()
    await engine.dispose()

    assert champion_after.is_champion is True   # трон остался
    bot.send_message.assert_not_called()


async def test_champion_auto_release_not_triggered_within_7_days(monkeypatch):
    import bot.scheduler as sched

    engine, factory = await _sched_db()
    monkeypatch.setattr(sched, "async_session", factory)

    recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    async with factory() as s:
        champion = _player(1, "Champion", rating=1200.0)
        champion.is_champion = True
        heir = _player(2, "Heir", rating=1100.0)
        s.add_all([champion, heir])
        await s.flush()
        s.add(_completed(champion, heir, champion.id, recent))
        await _seed_matches(s, heir, champion, NEWCOMER_THRESHOLD, recent - timedelta(days=1))
        await s.commit()

    bot = AsyncMock()
    await sched.check_champion_auto_release(bot)

    async with factory() as s:
        r = await s.execute(select(Player).where(Player.id == 1))
        champion_after = r.scalar_one()
    await engine.dispose()

    assert champion_after.is_champion is True
    bot.send_message.assert_not_called()


# ── F. Уведомления (антиспам обгона) ──────────────────────────────────────────

async def test_overtake_notifications_sent_once_not_repeated(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    contender = _player(2, "Contender", rating=999.0)
    third = _player(3, "Third")
    db.add_all([champion, contender, third])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    # 14 прошлых матчей — этот победный станет 15-м и даст право на боссфайт
    for i in range(14):
        db.add(_completed(contender, third, contender.id, base - timedelta(days=14 - i)))
    await db.commit()

    m1 = await _accepted_match(db, contender, champion)   # обычный вызов, ещё не боссфайт
    await db.commit()
    st1 = await _confirming_state(
        m1.id, contender.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb1, bot1 = _callback(2, f"confirm_{m1.id}"), AsyncMock()
    await confirm_result(cb1, db, st1, bot1)

    assert contender.rating > champion.rating
    texts1 = [c.args[1] for c in bot1.send_message.await_args_list]
    assert any("обошёл чемпиона" in t for t in texts1)
    assert any("Тебя догнал по очкам" in t for t in texts1)

    # Второй матч — тот же претендент играет с третьим, личность не меняется
    m2 = await _accepted_match(db, contender, third)
    await db.commit()
    st2 = await _confirming_state(
        m2.id, contender.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb2, bot2 = _callback(2, f"confirm_{m2.id}"), AsyncMock()
    await confirm_result(cb2, db, st2, bot2)

    texts2 = [c.args[1] for c in bot2.send_message.await_args_list]
    assert not any("обошёл чемпиона" in t for t in texts2)


# ── G. «Просран шанс» (chance_blown) ────────────────────────────────────────────

async def test_chance_blown_when_champion_retakes_lead(db):
    """Претендент теряет статус, если чемпион обычной победой (НЕ боссфайтом)
    обгоняет его обратно по очкам — 'ПОТРАЧЕНО' + ачивка chance_blown."""
    champion = _player(1, "Champion", rating=999.0)
    champion.is_champion = True
    contender = _player(2, "Contender", rating=1000.0)
    third = _player(3, "Third", rating=1000.0)
    db.add_all([champion, contender, third])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    for i in range(15):
        db.add(_completed(contender, third, contender.id, base - timedelta(days=15 - i)))
    await db.commit()

    pre_champion = await get_champion(db)
    pre_challenger = await get_challenger(db, pre_champion)
    assert pre_challenger is not None and pre_challenger.id == contender.id

    m = await _accepted_match(db, champion, third, is_boss_fight=False)
    await db.commit()
    st = await _confirming_state(
        m.id, champion.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert champion.rating > contender.rating  # обгон реально случился
    texts = [c.args[1] for c in bot.send_message.await_args_list]
    assert any("ПОТРАЧЕНО" in t for t in texts)
    assert "chance_blown" in get_achievements(contender)


async def test_chance_blown_when_challenger_loses_to_third_party(db):
    """Претендент теряет статус, проиграв кому угодно (не обязательно чемпиону) —
    рейтинг упал ниже чемпионского."""
    champion = _player(1, "Champion", rating=990.0)
    champion.is_champion = True
    contender = _player(2, "Contender", rating=1000.0)
    third = _player(3, "Third", rating=1000.0)
    db.add_all([champion, contender, third])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    for i in range(15):
        db.add(_completed(contender, third, contender.id, base - timedelta(days=15 - i)))
    await db.commit()

    pre_champion = await get_champion(db)
    pre_challenger = await get_challenger(db, pre_champion)
    assert pre_challenger is not None and pre_challenger.id == contender.id

    m = await _accepted_match(db, contender, third, is_boss_fight=False)
    await db.commit()
    st = await _confirming_state(m.id, contender.id, [{"reporter": 0, "opponent": 11}, {"reporter": 0, "opponent": 11}])
    cb, bot = _callback(2, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert contender.rating < champion.rating  # выпал из претендентов
    texts = [c.args[1] for c in bot.send_message.await_args_list]
    assert any("ПОТРАЧЕНО" in t for t in texts)
    assert "chance_blown" in get_achievements(contender)


async def test_chance_blown_not_given_on_actual_boss_fight_loss(db):
    """Проигрыш НЕПОСРЕДСТВЕННО в боссфайте даёт throne_denied, но не chance_blown —
    два разных уведомления на одно и то же событие были бы дублированием."""
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    contender = _player(2, "Contender", rating=999.0)
    db.add_all([champion, contender])
    await db.flush()
    m = await _accepted_match(db, champion, contender, is_boss_fight=True)
    await db.commit()
    st = await _confirming_state(
        m.id, champion.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert "throne_denied" in get_achievements(contender)
    assert "chance_blown" not in get_achievements(contender)
    texts = [c.args[1] for c in bot.send_message.await_args_list]
    assert not any("ПОТРАЧЕНО" in t for t in texts)


async def test_chance_blown_not_given_when_challenger_status_unchanged(db):
    """Обычный матч между игроками, рейтинг которых заведомо далёк от
    чемпиона/претендента — не может их сдвинуть, уведомления нет."""
    champion = _player(1, "Champion", rating=990.0)
    champion.is_champion = True
    contender = _player(2, "Contender", rating=1000.0)
    # 800 pts — специально далеко ниже contender, чтобы даже разгромная победа
    # p3 над p4 не подобралась к 1000 и не сместила contender (см. тест ниже
    # про честный обгон третьей стороной — там разница специально маленькая).
    p3, p4 = _player(3, "P3", rating=800.0), _player(4, "P4", rating=800.0)
    db.add_all([champion, contender, p3, p4])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    for i in range(15):
        db.add(_completed(contender, p3, contender.id, base - timedelta(days=15 - i)))
    await db.commit()

    m = await _accepted_match(db, p3, p4, is_boss_fight=False)
    await db.commit()
    st = await _confirming_state(m.id, p3.id, [{"reporter": 11, "opponent": 5}])
    cb, bot = _callback(3, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert "chance_blown" not in get_achievements(contender)
    texts = [c.args[1] for c in bot.send_message.await_args_list]
    assert not any("ПОТРАЧЕНО" in t for t in texts)


async def test_chance_blown_also_fires_on_third_party_overtake(db):
    """Осознанный бонус сверх узкого скоупа: реализация переиспользует ту же
    инфраструктуру 'претендент до/после', что уже существующее уведомление
    'появился новый претендент' — а оно уже считалось после КАЖДОГО матча в
    клубе, не только матчей чемпиона/претендента. Раз дорогая часть (пересчёт
    challenger до/после) уже оплачена существующей фичей, ловить и обгон
    третьей стороной получилось бесплатно — сознательно не стали искусственно
    urезать это обратно до 'только матчи чемпиона/претендента'."""
    champion = _player(1, "Champion", rating=990.0)
    champion.is_champion = True
    contender = _player(2, "Contender", rating=1000.0)
    third, fourth = _player(3, "Third", rating=1000.0), _player(4, "Fourth", rating=1000.0)
    db.add_all([champion, contender, third, fourth])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    for i in range(15):
        db.add(_completed(contender, third, contender.id, base - timedelta(days=15 - i)))
    await db.commit()

    pre_champion = await get_champion(db)
    pre_challenger = await get_challenger(db, pre_champion)
    assert pre_challenger is not None and pre_challenger.id == contender.id  # тай-брейк по id

    # third обыгрывает fourth (матч вообще без участия champion/contender)
    # и разгромной победой перескакивает contender по рейтингу
    m = await _accepted_match(db, third, fourth, is_boss_fight=False)
    await db.commit()
    st = await _confirming_state(
        m.id, third.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(3, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert third.rating > contender.rating  # честный обгон третьей стороной
    assert "chance_blown" in get_achievements(contender)


async def test_boss_fight_start_broadcast_to_all_players(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=1050.0)
    bystander = _player(3, "Bystander")
    filler = _player(4, "Filler")
    db.add_all([champion, challenger_p, bystander, filler])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    await _seed_matches(db, challenger_p, filler, NEWCOMER_THRESHOLD, base)
    await db.commit()

    cb, bot = _callback(2, f"challenge_{champion.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    recipients = {c.args[0] for c in bot.send_message.await_args_list}
    assert bystander.telegram_id in recipients   # зритель тоже получил рассылку
    texts = [c.args[1] for c in bot.send_message.await_args_list]
    assert any("Грядёт БОСС-ФАЙТ" in t for t in texts)


# ── G. Находки код-ревью PR #54 — регрессии ───────────────────────────────────

# -- try_transfer_champion (CAS) --

async def test_try_transfer_champion_success_opens_reign(db):
    a, b = _player(1, "A"), _player(2, "B")
    a.is_champion = True
    db.add_all([a, b])
    await db.flush()

    at = datetime(2026, 6, 1, 12, 0, 0)
    ok = await try_transfer_champion(db, a.id, b.id, at=at)
    await db.commit()

    assert ok is True
    ra = await db.execute(select(Player).where(Player.id == a.id))
    assert ra.scalar_one().is_champion is False
    rb = await db.execute(select(Player).where(Player.id == b.id))
    assert rb.scalar_one().is_champion is True

    reigns = (await db.execute(select(ChampionReign))).scalars().all()
    assert len(reigns) == 1
    assert reigns[0].player_id == b.id
    assert reigns[0].started_at == at
    assert reigns[0].ended_at is None


async def test_try_transfer_champion_closes_previous_reign(db):
    a, b = _player(1, "A"), _player(2, "B")
    a.is_champion = True
    db.add_all([a, b])
    await db.flush()
    db.add(ChampionReign(player_id=a.id, started_at=datetime(2026, 5, 1), ended_at=None))
    await db.commit()

    at = datetime(2026, 6, 1, 12, 0, 0)
    await try_transfer_champion(db, a.id, b.id, at=at)
    await db.commit()

    reigns = (await db.execute(select(ChampionReign).order_by(ChampionReign.id))).scalars().all()
    assert len(reigns) == 2
    assert reigns[0].player_id == a.id and reigns[0].ended_at == at
    assert reigns[1].player_id == b.id and reigns[1].ended_at is None


async def test_try_transfer_champion_fails_when_from_not_champion(db):
    """CAS: from_id уже не чемпион (трон сменился гонкой) — перенос не проходит."""
    a, b, c = _player(1, "A"), _player(2, "B"), _player(3, "C")
    c.is_champion = True  # реальный текущий чемпион — не a
    db.add_all([a, b, c])
    await db.flush()
    await db.commit()

    ok = await try_transfer_champion(db, a.id, b.id, at=datetime(2026, 6, 1))
    assert ok is False
    rb = await db.execute(select(Player).where(Player.id == b.id))
    assert rb.scalar_one().is_champion is False


# -- Гонка авто-освобождения vs идущий боссфайт --

async def test_auto_release_skipped_when_champion_has_active_match(monkeypatch):
    import bot.scheduler as sched

    engine, factory = await _sched_db()
    monkeypatch.setattr(sched, "async_session", factory)

    old_completed = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    async with factory() as s:
        champion = _player(1, "Champion", rating=1200.0)
        champion.is_champion = True
        heir = _player(2, "Heir", rating=1100.0)
        s.add_all([champion, heir])
        await s.flush()
        s.add(_completed(champion, heir, champion.id, old_completed))
        await _seed_matches(s, heir, champion, NEWCOMER_THRESHOLD, old_completed - timedelta(days=1))
        # У чемпиона ПРЯМО СЕЙЧАС идёт активный матч — джоба не должна его трогать
        s.add(Match(
            challenger_id=champion.id, challenged_id=heir.id,
            status=MatchStatus.accepted,
            accepted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            is_boss_fight=True,
        ))
        await s.commit()

    bot = AsyncMock()
    await sched.check_champion_auto_release(bot)

    async with factory() as s:
        r = await s.execute(select(Player).where(Player.id == 1))
        champion_after = r.scalar_one()
    await engine.dispose()

    assert champion_after.is_champion is True
    bot.send_message.assert_not_called()


async def test_auto_release_quiet_when_cas_fails(monkeypatch):
    import bot.scheduler as sched

    engine, factory = await _sched_db()
    monkeypatch.setattr(sched, "async_session", factory)
    monkeypatch.setattr(sched, "try_transfer_champion", AsyncMock(return_value=False))

    old_completed = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    async with factory() as s:
        champion = _player(1, "Champion", rating=1200.0)
        champion.is_champion = True
        heir = _player(2, "Heir", rating=1100.0)
        s.add_all([champion, heir])
        await s.flush()
        s.add(_completed(champion, heir, champion.id, old_completed))
        await _seed_matches(s, heir, champion, NEWCOMER_THRESHOLD, old_completed - timedelta(days=1))
        await s.commit()

    bot = AsyncMock()
    await sched.check_champion_auto_release(bot)  # не должно упасть

    bot.send_message.assert_not_called()
    await engine.dispose()


async def test_confirm_result_skips_transfer_when_cas_fails(db, monkeypatch):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    m = await _accepted_match(db, challenger_p, champion, is_boss_fight=True)
    await db.commit()

    import bot.handlers.match_result as mr
    monkeypatch.setattr(mr, "try_transfer_champion", AsyncMock(return_value=False))

    st = await _confirming_state(
        m.id, challenger_p.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(2, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    r = await db.execute(select(Player).where(Player.id == challenger_p.id))
    assert r.scalar_one().is_champion is False   # трон не додан силой
    texts = [c.args[1] for c in bot.send_message.await_args_list]
    assert not any("Новый чемпион" in t for t in texts)


# -- Видимость кнопок при активном блоке реванша --

async def _bf_pair_with_block(db) -> tuple[Player, Player]:
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=999.0)
    db.add_all([champion, challenger_p])
    await db.flush()
    db.add(Match(
        challenger_id=champion.id, challenged_id=challenger_p.id,
        status=MatchStatus.completed, winner_id=champion.id, is_boss_fight=True,
        sets_data=[{"w": 11, "l": 0}], rating_change=20.0,
        completed_at=datetime(2026, 6, 1, 12, 0, 0),
    ))
    await db.commit()
    return champion, challenger_p


async def test_challenge_list_hides_champion_when_rematch_blocked(db):
    champion, challenger_p = await _bf_pair_with_block(db)

    cb = _callback(challenger_p.telegram_id, "menu_play")
    await show_players_for_challenge(cb, db)

    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert not any("БОСС-ФАЙТ" in t for t in texts)
    assert not any(champion.display_name in t for t in texts)


async def test_profile_can_challenge_false_when_rematch_blocked(db):
    champion, challenger_p = await _bf_pair_with_block(db)

    cb = _callback(challenger_p.telegram_id, f"player_profile_{champion.id}")
    await show_player_profile(cb, db)

    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert not any("Вызвать" in t for t in texts)


async def test_h2h_can_challenge_false_when_rematch_blocked(db):
    champion, challenger_p = await _bf_pair_with_block(db)

    cb = _callback(challenger_p.telegram_id, f"h2h_{champion.id}_0")
    await show_h2h(cb, db)

    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert not any("Вызвать" in t for t in texts)


# -- Порядок рассылки старта боссфайта --

async def test_boss_fight_broadcast_not_sent_when_opponent_notify_fails(db):
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    challenger_p = _player(2, "Challenger", rating=1050.0)
    bystander = _player(3, "Bystander")
    filler = _player(4, "Filler")
    db.add_all([champion, challenger_p, bystander, filler])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    await _seed_matches(db, challenger_p, filler, NEWCOMER_THRESHOLD, base)
    await db.commit()

    bot = AsyncMock()

    async def failing_send(chat_id, *args, **kwargs):
        if chat_id == champion.telegram_id:
            raise Exception("недоступен")

    bot.send_message.side_effect = failing_send

    cb = _callback(2, f"challenge_{champion.id}")
    await send_challenge(cb, db, bot)

    texts = [c.args[1] for c in bot.send_message.await_args_list if len(c.args) > 1]
    assert not any("Грядёт БОСС-ФАЙТ" in t for t in texts)
    r = await db.execute(select(Match).where(Match.status == MatchStatus.accepted))
    assert r.scalar_one_or_none() is None   # матч откатился


# -- Рубильник фичи переживает деплой --

async def test_bootstrap_champion_does_not_recrown_after_manual_disable(db):
    p1, p2 = _player(1, "Alice", rating=1000.0), _player(2, "Bob", rating=1200.0)
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p2.id, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()

    await bootstrap_champion(db)
    champion = await get_champion(db)
    assert champion is not None and champion.id == p2.id

    # Админ вручную отключает фичу
    p2.is_champion = False
    await db.commit()

    await bootstrap_champion(db)  # как при рестарте/деплое

    assert await get_champion(db) is None


# -- ▲▼ на лидерборде не врут при пиннинге чемпиона --

async def test_leaderboard_no_false_arrow_for_pinned_champion(db):
    champion = _player(1, "PinnedChamp", rating=950.0)
    champion.is_champion = True
    higher = _player(2, "HigherRated", rating=1100.0)
    db.add_all([champion, higher])
    await db.flush()
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    db.add(_completed(champion, higher, champion.id, old))  # только старый матч, за неделю тишина
    await db.commit()

    cb = _callback(1, "menu_leaderboard")
    await show_leaderboard(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    champion_line = next(line for line in text.split("\n") if "PinnedChamp" in line)
    higher_line = next(line for line in text.split("\n") if "HigherRated" in line)
    assert "▲" not in champion_line and "▼" not in champion_line
    assert "▲" not in higher_line and "▼" not in higher_line


# -- «Крупнейший апсет» не искажается боссфайтом --

async def test_biggest_upset_excludes_boss_fight_matches(db):
    p1, p2 = _player(1, "A", rating=1000.0), _player(2, "B", rating=1000.0)
    db.add_all([p1, p2])
    await db.flush()
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id, is_boss_fight=True,
        sets_data=[{"w": 11, "l": 9}], rating_change=30.0,
        completed_at=datetime(2026, 6, 1, 12, 0, 0),
    ))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p2.id, is_boss_fight=False,
        sets_data=[{"w": 11, "l": 2}], rating_change=18.0,
        completed_at=datetime(2026, 6, 2, 12, 0, 0),
    ))
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    assert "Крупнейший апсет" in text
    assert "+18.0 pts" in text
    assert "+30.0 pts" not in text


# -- «Дольше всех лидировал» --

async def test_longest_champion_reign_picks_longest(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    db.add(ChampionReign(player_id=p1.id, started_at=datetime(2026, 1, 1), ended_at=datetime(2026, 1, 5)))
    db.add(ChampionReign(player_id=p2.id, started_at=datetime(2026, 2, 1), ended_at=datetime(2026, 2, 20)))
    await db.commit()

    result = await longest_champion_reign(db)
    assert result == (p2.id, 19)


async def test_longest_champion_reign_none_when_no_reigns(db):
    p1 = _player(1, "A")
    db.add(p1)
    await db.flush()
    assert await longest_champion_reign(db) is None


async def test_club_records_shows_longest_reign(db):
    p1, p2 = _player(1, "LongReign"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, datetime(2026, 6, 1, 12, 0, 0)))
    db.add(ChampionReign(player_id=p1.id, started_at=datetime(2026, 1, 1), ended_at=datetime(2026, 1, 11)))
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    assert "Дольше всех лидировал" in text
    assert "LongReign" in text
    assert "10 дней" in text


# -- «Больше всего защит трона подряд» --

async def test_most_boss_fight_defenses_counts_within_one_reign(db):
    """Все боссфайты чемпиона внутри его правления — по конструкции победы
    (поражение немедленно закрыло бы правление), просто считаем их число."""
    p1, p2, p3 = _player(1, "Champ"), _player(2, "B"), _player(3, "C")
    db.add_all([p1, p2, p3])
    await db.flush()
    db.add(ChampionReign(player_id=p1.id, started_at=datetime(2026, 1, 1), ended_at=None))
    for i, opp in enumerate([p2, p3, p2]):
        db.add(Match(
            challenger_id=p1.id, challenged_id=opp.id,
            status=MatchStatus.completed, winner_id=p1.id, is_boss_fight=True,
            sets_data=[{"w": 11, "l": 5}],
            completed_at=datetime(2026, 1, 2 + i, 12, 0, 0),
        ))
    await db.commit()

    result = await most_boss_fight_defenses(db)
    assert result == (p1.id, 3)


async def test_most_boss_fight_defenses_picks_reign_with_more(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    db.add(ChampionReign(player_id=p1.id, started_at=datetime(2026, 1, 1), ended_at=datetime(2026, 1, 10)))
    db.add(ChampionReign(player_id=p2.id, started_at=datetime(2026, 1, 10), ended_at=None))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, is_boss_fight=True, sets_data=[{"w": 11, "l": 5}],
        completed_at=datetime(2026, 1, 5, 12, 0, 0),
    ))
    for i in range(2):
        db.add(Match(
            challenger_id=p2.id, challenged_id=p1.id, status=MatchStatus.completed,
            winner_id=p2.id, is_boss_fight=True, sets_data=[{"w": 11, "l": 5}],
            completed_at=datetime(2026, 1, 12 + i, 12, 0, 0),
        ))
    await db.commit()

    result = await most_boss_fight_defenses(db)
    assert result == (p2.id, 2)


async def test_most_boss_fight_defenses_none_when_no_reigns(db):
    p1 = _player(1, "A")
    db.add(p1)
    await db.flush()
    assert await most_boss_fight_defenses(db) is None


async def test_club_records_shows_most_defenses(db):
    p1, p2 = _player(1, "Defender"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, datetime(2026, 6, 1, 12, 0, 0)))
    db.add(ChampionReign(player_id=p1.id, started_at=datetime(2026, 1, 1), ended_at=None))
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, is_boss_fight=True, sets_data=[{"w": 11, "l": 5}],
        completed_at=datetime(2026, 1, 2, 12, 0, 0),
    ))
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    assert "Больше всего защит трона подряд" in text
    assert "Defender" in text


# -- «Самый долгий боссфайт» --

async def test_club_records_shows_longest_boss_fight(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    # Обычный длинный матч (не боссфайт) — не должен попасть в этот рекорд
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, is_boss_fight=False,
        sets_data=[{"w": 11, "l": 9}] * 5,
        completed_at=datetime(2026, 6, 1, 12, 0, 0),
    ))
    # Короткий боссфайт
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, is_boss_fight=True,
        sets_data=[{"w": 11, "l": 5}, {"w": 11, "l": 5}],
        completed_at=datetime(2026, 6, 2, 12, 0, 0),
    ))
    # Длинный боссфайт — победитель этого рекорда
    db.add(Match(
        challenger_id=p2.id, challenged_id=p1.id, status=MatchStatus.completed,
        winner_id=p1.id, is_boss_fight=True,
        sets_data=[{"w": 11, "l": 9}] * 4,
        completed_at=datetime(2026, 6, 3, 12, 0, 0),
    ))
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    assert "Самый долгий боссфайт" in text
    assert "4 партий" in text


# -- Личная статистика: боссфайты сыграно/выиграно --

async def test_stats_screen_shows_boss_fight_record(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, is_boss_fight=True, sets_data=[{"w": 11, "l": 5}],
        completed_at=datetime(2026, 6, 1, 12, 0, 0),
    ))
    db.add(Match(
        challenger_id=p2.id, challenged_id=p1.id, status=MatchStatus.completed,
        winner_id=p2.id, is_boss_fight=True, sets_data=[{"w": 11, "l": 5}],
        completed_at=datetime(2026, 6, 2, 12, 0, 0),
    ))
    await db.commit()

    cb = _callback(1, "menu_stats")
    await show_my_stats(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    assert "Боссфайты" in text
    assert "1/2" in text


async def test_stats_screen_omits_boss_fight_line_when_none_played(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id, status=MatchStatus.completed,
        winner_id=p1.id, is_boss_fight=False, sets_data=[{"w": 11, "l": 5}],
        completed_at=datetime(2026, 6, 1, 12, 0, 0),
    ))
    await db.commit()

    cb = _callback(1, "menu_stats")
    await show_my_stats(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    assert "Боссфайты" not in text
