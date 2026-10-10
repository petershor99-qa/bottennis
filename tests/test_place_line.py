"""Блок «Рейтинг клуба» под итогом матча (v2.159.0; в v2.158.0 здесь была одна
строка «Место в клубе»).

По данным `/usage` после внесения результата 24 из 29 игроков сразу шли в рейтинг —
узнать своё место. Теперь все места видны прямо в итоге (у репортёра) и в
уведомлении второму участнику: свой ряд помечен, чемпион с короной, под списком —
разрыв до игрока выше.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from sqlalchemy import select

from bot.db.models import Match, MatchStatus
from bot.handlers.match_result import confirm_result
from bot.states.states import MatchResultStates
from bot.utils import StandingRow, ranking_block, snapshot_ranks, snapshot_standings
from tests.conftest import _callback, _completed, _player, _state

SETS_BIG_WIN = [{"reporter": 11, "opponent": 3}, {"reporter": 11, "opponent": 3}]


def _rows(*items):
    """items: (rank, id, name, rating[, is_champion])"""
    return [StandingRow(i[0], i[1], i[2], i[3], bool(i[4]) if len(i) > 4 else False) for i in items]


# ── Чистая функция ranking_block ──────────────────────────────────────────────

def test_block_marks_viewer_and_lists_everyone_in_order():
    rows = _rows((1, 1, "Боб", 1120.4), (2, 2, "Алиса", 1107.2), (3, 3, "Вика", 1095.0))
    block = ranking_block({2: 3}, rows, viewer_id=2)
    assert block.split("\n") == [
        "🏆 <b>Рейтинг клуба:</b>",
        "#1 Боб · 1120.4",
        "▶ #2 <b>Ты</b> · 1107.2 (было #3)",
        "#3 Вика · 1095.0",
        "",
        "До #1 Боб: −13.2",
    ]


def test_no_was_marker_when_place_is_unchanged_or_unknown():
    rows = _rows((1, 1, "Боб", 1120.4), (2, 2, "Алиса", 1107.2))
    assert "(было" not in ranking_block({2: 2}, rows, viewer_id=2)   # место то же
    assert "(было" not in ranking_block({}, rows, viewer_id=2)       # дебютант: места «до» нет


def test_leader_sees_lead_over_second_place():
    rows = _rows((1, 1, "Алиса", 1130.0), (2, 2, "Боб", 1117.6))
    block = ranking_block({1: 1}, rows, viewer_id=1)
    assert block.split("\n")[-1] == "Отрыв от #2 Боб: +12.4"
    assert "До #" not in block


def test_champion_gets_a_crown_and_viewer_champion_gets_both_marks():
    rows = _rows((1, 1, "Боб", 1000.0, True), (2, 2, "Алиса", 1100.0))
    other = ranking_block({}, rows, viewer_id=2)
    assert "#1 👑 Боб · 1000.0" in other
    mine = ranking_block({}, rows, viewer_id=1)
    assert "▶ #1 👑 <b>Ты</b> · 1000.0" in mine


def test_no_gap_line_when_pinned_champion_has_lower_rating():
    """Чемпион стоит на #1, хотя у игрока на #2 рейтинг выше: «до #1: −X» выдумывать нельзя."""
    rows = _rows((1, 1, "Боб", 1000.0, True), (2, 2, "Алиса", 1100.0))
    block = ranking_block({}, rows, viewer_id=2)
    assert "До #" not in block and "Отрыв" not in block


def test_names_are_escaped():
    rows = _rows((1, 1, "Боб <b>", 1100.0), (2, 2, "Алиса", 1000.0))
    block = ranking_block({}, rows, viewer_id=2)
    assert "Боб &lt;b&gt;" in block and "<b>Боб" not in block.replace("<b>Ты</b>", "")


def test_two_players_and_missing_viewer():
    rows = _rows((1, 1, "Боб", 1100.0), (2, 2, "Алиса", 1000.0))
    assert ranking_block({}, rows, viewer_id=2).split("\n") == [
        "🏆 <b>Рейтинг клуба:</b>",
        "#1 Боб · 1100.0",
        "▶ #2 <b>Ты</b> · 1000.0",
        "",
        "До #1 Боб: −100.0",
    ]
    assert ranking_block({}, rows, viewer_id=99) == ""
    assert ranking_block({}, [], viewer_id=1) == ""


# ── Снимки ────────────────────────────────────────────────────────────────────

async def _club(db, ratings):
    """Игроки с заданными рейтингами; каждый уже сыграл (общий матч с «Статистом»)."""
    stat = _player(100, "Stat", 500.0)
    players = [_player(i + 1, f"P{i + 1}", r) for i, r in enumerate(ratings)]
    db.add_all([stat, *players])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    db.add_all([
        _completed(p, stat, p.id, 5.0, base + timedelta(hours=i)) for i, p in enumerate(players)
    ])
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


async def test_standings_carry_names_ratings_and_champion_flag(db):
    (a, b), stat = await _club(db, [1100.0, 1200.0])
    a.is_champion = True
    await db.commit()
    rows = await snapshot_standings(db)
    assert [r.name for r in rows] == ["P1", "P2", "Stat"]          # чемпион закреплён на #1
    assert [r.rank for r in rows] == [1, 2, 3]
    assert rows[0].is_champion and not rows[1].is_champion
    assert rows[1].rating == 1200.0


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
    screen = _screen(cb)
    assert "▶ #1 <b>Ты</b>" in screen and "(было #2)" in screen and "#2 P1" in screen
    assert "Отрыв от #2 P1: +" in screen
    notice = next(t for t in _sent_to(bot, a.telegram_id) if "Рейтинг клуба" in t)
    assert "▶ #2 <b>Ты</b>" in notice and "(было #1)" in notice and "#1 P2" in notice
    assert "До #1 P2: −" in notice


async def test_overtake_when_the_loser_reports_the_score(db):
    (a, b), _ = await _club(db, [1100.0, 1090.0])
    cb, bot = await _confirm(db, b, a, reporter=a, sets=[{"reporter": 3, "opponent": 11}] * 2)
    screen = _screen(cb)                                                  # экран у проигравшего A
    assert "▶ #2 <b>Ты</b>" in screen and "(было #1)" in screen
    notice = next(t for t in _sent_to(bot, b.telegram_id) if "Рейтинг клуба" in t)
    assert "▶ #1 <b>Ты</b>" in notice and "(было #2)" in notice


async def test_block_is_shown_even_when_places_do_not_change(db):
    (a, b), _ = await _club(db, [1300.0, 1000.0])
    cb, bot = await _confirm(db, b, a, reporter=b, sets=SETS_BIG_WIN)
    screen = _screen(cb)
    assert "🏆 <b>Рейтинг клуба:</b>" in screen and "▶ #2 <b>Ты</b>" in screen
    assert "(было" not in screen and "До #1 P1: −" in screen
    notice = next(t for t in _sent_to(bot, a.telegram_id) if "Рейтинг клуба" in t)
    assert "(было" not in notice and "▶ #1 <b>Ты</b>" in notice


async def test_debutant_sees_the_table_without_was_marker(db):
    (a,), stat = await _club(db, [1100.0])
    newbie = _player(70, "Newbie", 1000.0)
    db.add(newbie)
    await db.commit()
    cb, bot = await _confirm(db, newbie, a, reporter=newbie, sets=SETS_BIG_WIN)
    screen = _screen(cb)
    assert "Рейтинг клуба" in screen and "<b>Ты</b>" in screen and "(было" not in screen


async def test_boss_fight_throne_transfer_is_reflected(db):
    (champ, challenger), _ = await _club(db, [1000.0, 1100.0])
    champ.is_champion = True
    await db.commit()
    before = await snapshot_ranks(db)
    assert before[champ.id] == 1 and before[challenger.id] == 2           # чемпион закреплён на #1

    cb, bot = await _confirm(db, challenger, champ, reporter=challenger, sets=SETS_BIG_WIN,
                             is_boss_fight=True)
    fresh = (await db.execute(select(type(champ)).where(type(champ).id == challenger.id))).scalar_one()
    assert fresh.is_champion is True
    screen = _screen(cb)
    assert "▶ #1 👑 <b>Ты</b>" in screen and "(было #2)" in screen
    notice = next(t for t in _sent_to(bot, champ.telegram_id) if "Рейтинг клуба" in t)
    assert "#1 👑 P2" in notice and "▶ #2 <b>Ты</b>" in notice and "(было #1)" in notice


async def test_failing_rank_snapshot_does_not_break_the_result(db, monkeypatch):
    (a, b), _ = await _club(db, [1100.0, 1090.0])

    async def boom(*_a, **_k):
        raise RuntimeError("места упали")

    monkeypatch.setattr("bot.handlers.match_result.snapshot_ranks", boom)
    cb, bot = await _confirm(db, b, a, reporter=b, sets=SETS_BIG_WIN)
    assert "Матч завершён" in _screen(cb)            # блок без «(было #N)» всё равно показан
    m = (await db.execute(select(Match).order_by(Match.id.desc()))).scalars().first()
    assert m.status == MatchStatus.completed and m.winner_id == b.id


async def test_failing_standings_snapshot_does_not_break_the_result(db, monkeypatch):
    (a, b), _ = await _club(db, [1100.0, 1090.0])

    async def boom(*_a, **_k):
        raise RuntimeError("таблица упала")

    monkeypatch.setattr("bot.handlers.match_result.snapshot_standings", boom)
    cb, bot = await _confirm(db, b, a, reporter=b, sets=SETS_BIG_WIN)
    assert "Матч завершён" in _screen(cb) and "Рейтинг клуба" not in _screen(cb)


async def test_block_is_shown_even_when_the_before_snapshot_failed(db, monkeypatch):
    (a, b), _ = await _club(db, [1100.0, 1090.0])

    async def boom(*_a, **_k):
        raise RuntimeError("места до матча упали")

    monkeypatch.setattr("bot.handlers.match_result.snapshot_ranks", boom)
    cb, bot = await _confirm(db, b, a, reporter=b, sets=SETS_BIG_WIN)
    screen = _screen(cb)
    assert "🏆 <b>Рейтинг клуба:</b>" in screen and "▶ #1 <b>Ты</b>" in screen
    assert "(было" not in screen
