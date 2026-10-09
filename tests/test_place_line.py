"""«Место в клубе: #2 (было #3)» в итоге матча (v2.158.0).

По данным `/usage` после внесения результата 24 из 29 игроков сразу шли в рейтинг —
узнать своё место. Теперь место видно прямо в итоге (у репортёра) и в уведомлении
второму участнику, но только если оно изменилось.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from sqlalchemy import select

from bot.db.models import Match, MatchStatus
from bot.handlers.match_result import confirm_result
from bot.states.states import MatchResultStates
from bot.utils import place_change_line, snapshot_ranks
from tests.conftest import _callback, _completed, _player, _state

SETS_BIG_WIN = [{"reporter": 11, "opponent": 3}, {"reporter": 11, "opponent": 3}]


# ── Чистая функция ────────────────────────────────────────────────────────────

def test_line_when_place_improved():
    assert place_change_line({1: 3}, {1: 2}, 1) == "🏆 Место в клубе: <b>#2</b> (было #3)"


def test_line_when_place_dropped():
    assert place_change_line({1: 1}, {1: 2}, 1) == "🏆 Место в клубе: <b>#2</b> (было #1)"


def test_no_line_when_place_is_unchanged():
    assert place_change_line({1: 2}, {1: 2}, 1) == ""


def test_no_line_for_a_debutant_or_unknown_player():
    assert place_change_line({}, {1: 4}, 1) == ""
    assert place_change_line({1: 4}, {}, 1) == ""
    assert place_change_line({2: 1}, {2: 1}, 99) == ""


# ── Снимок мест ───────────────────────────────────────────────────────────────

async def _club(db, ratings):
    """Игроки с заданными рейтингами; каждый уже сыграл (общий матч с «Статистом»)."""
    stat = _player(100, "Stat", 500.0)
    players = [_player(i + 1, f"P{i + 1}", r) for i, r in enumerate(ratings)]
    db.add_all([stat, *players])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    matches = [
        _completed(p, stat, p.id, 5.0, base + timedelta(hours=i)) for i, p in enumerate(players)
    ]
    db.add_all(matches)
    await db.commit()
    return players, stat


async def test_snapshot_orders_by_rating_and_skips_players_without_matches(db):
    players, _ = await _club(db, [1100.0, 1200.0])
    idle = _player(50, "Idle", 1500.0)
    db.add(idle)
    await db.commit()
    ranks = await snapshot_ranks(db)
    assert ranks[players[1].id] == 1 and ranks[players[0].id] == 2
    assert idle.id not in ranks


async def test_snapshot_can_exclude_the_current_match(db):
    players, stat = await _club(db, [1100.0])
    newbie = _player(60, "Newbie", 1000.0)
    db.add(newbie)
    await db.flush()
    m = _completed(newbie, stat, newbie.id, 5.0, datetime(2026, 6, 2, 12, 0, 0))
    db.add(m)
    await db.commit()
    assert newbie.id in await snapshot_ranks(db)
    assert newbie.id not in await snapshot_ranks(db, exclude_match_id=m.id)


# ── Интеграция с confirm_result ───────────────────────────────────────────────

async def _confirm(db, challenger, challenged, reporter, sets, is_boss_fight=False):
    m = Match(
        challenger_id=challenger.id, challenged_id=challenged.id, status=MatchStatus.accepted,
        accepted_at=datetime(2026, 6, 5, 12, 0, 0), is_boss_fight=is_boss_fight,
    )
    db.add(m)
    await db.commit()
    st = _state(reporter.telegram_id)
    await st.set_state(MatchResultStates.confirming)
    await st.update_data(match_id=m.id, reporter_player_id=reporter.id, is_draw=False, sets_data=sets)
    cb, bot = _callback(reporter.telegram_id, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)
    return cb, bot


def _screen(cb):
    return cb.message.edit_text.await_args.args[0]


def _sent_to(bot, telegram_id):
    return [str(c.args[1]) for c in bot.send_message.await_args_list
            if c.args and c.args[0] == telegram_id and len(c.args) > 1]


async def test_overtake_is_shown_to_both_players(db):
    (a, b), _ = await _club(db, [1100.0, 1090.0])
    cb, bot = await _confirm(db, b, a, reporter=b, sets=SETS_BIG_WIN)   # B обгоняет A
    assert "🏆 Место в клубе: <b>#1</b> (было #2)" in _screen(cb)
    assert any("🏆 Место в клубе: <b>#2</b> (было #1)" in t for t in _sent_to(bot, a.telegram_id))


async def test_overtake_when_the_loser_reports_the_score(db):
    (a, b), _ = await _club(db, [1100.0, 1090.0])
    cb, bot = await _confirm(db, b, a, reporter=a, sets=[{"reporter": 3, "opponent": 11}] * 2)
    assert "🏆 Место в клубе: <b>#2</b> (было #1)" in _screen(cb)          # экран у проигравшего A
    assert any("🏆 Место в клубе: <b>#1</b> (было #2)" in t for t in _sent_to(bot, b.telegram_id))


async def test_no_line_when_places_do_not_change(db):
    (a, b), _ = await _club(db, [1300.0, 1000.0])
    cb, bot = await _confirm(db, b, a, reporter=b, sets=SETS_BIG_WIN)
    assert "Место в клубе" not in _screen(cb)
    assert all("Место в клубе" not in t for t in _sent_to(bot, a.telegram_id))


async def test_debutant_gets_no_line(db):
    (a,), stat = await _club(db, [1100.0])
    newbie = _player(70, "Newbie", 1000.0)
    db.add(newbie)
    await db.commit()
    cb, bot = await _confirm(db, newbie, a, reporter=newbie, sets=SETS_BIG_WIN)
    assert "Место в клубе" not in _screen(cb)


async def test_boss_fight_throne_transfer_changes_places(db):
    (champ, challenger), _ = await _club(db, [1000.0, 1100.0])
    champ.is_champion = True
    await db.commit()
    before = await snapshot_ranks(db)
    assert before[champ.id] == 1 and before[challenger.id] == 2           # чемпион закреплён на #1

    cb, bot = await _confirm(db, challenger, champ, reporter=challenger, sets=SETS_BIG_WIN,
                             is_boss_fight=True)
    fresh = (await db.execute(select(type(champ)).where(type(champ).id == challenger.id))).scalar_one()
    assert fresh.is_champion is True
    assert "🏆 Место в клубе: <b>#1</b> (было #2)" in _screen(cb)
    assert any("🏆 Место в клубе: <b>#2</b> (было #1)" in t for t in _sent_to(bot, champ.telegram_id))


async def test_failing_rank_snapshot_does_not_break_the_result(db, monkeypatch):
    (a, b), _ = await _club(db, [1100.0, 1090.0])

    async def boom(*_a, **_k):
        raise RuntimeError("места упали")

    monkeypatch.setattr("bot.handlers.match_result.snapshot_ranks", boom)
    cb, bot = await _confirm(db, b, a, reporter=b, sets=SETS_BIG_WIN)
    assert "Матч завершён" in _screen(cb) and "Место в клубе" not in _screen(cb)
    m = (await db.execute(select(Match).order_by(Match.id.desc()))).scalars().first()
    assert m.status == MatchStatus.completed and m.winner_id == b.id
