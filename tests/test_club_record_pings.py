"""Пинги «Рекорд клуба» (v2.157.0): пороги, тексты, антиспам, интеграция с confirm_result."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from bot.db.models import ClubRecordPing, Match, MatchStatus
from bot.handlers.match_result import confirm_result
from bot.services.club_records import (
    DEFENSE_MIN,
    DURATION_MAX_MINUTES,
    DURATION_MIN_MINUTES,
    KIND_DEFENSE,
    KIND_DURATION,
    KIND_PEAK,
    KIND_STREAK,
    PEAK_STEP,
    STREAK_MIN,
    RecordBreak,
    RecordValue,
    best_win_streak,
    detect_record_breaks,
    longest_match,
    match_minutes,
    ping_text,
    send_record_pings,
    snapshot_club_records,
)
from bot.states.states import MatchResultStates
from tests.conftest import _callback, _completed, _player, _state


def _snap(peak=None, streak=None, defense=None, duration=None):
    return {KIND_PEAK: peak, KIND_STREAK: streak, KIND_DEFENSE: defense, KIND_DURATION: duration}


def _rv(holder, value, extra=None):
    return RecordValue(holder, float(value), extra)


# ── Пороги ────────────────────────────────────────────────────────────────────

def test_peak_pings_only_when_a_round_mark_is_crossed():
    before = _snap(peak=_rv(1, 1463.0))
    assert detect_record_breaks(before, _snap(peak=_rv(1, 1471.0))) == []
    breaks = detect_record_breaks(before, _snap(peak=_rv(1, 1500.4)))
    assert [b.kind for b in breaks] == [KIND_PEAK]
    assert breaks[0].prev_value == 1463.0 and breaks[0].value == 1500.4


def test_peak_crossing_by_a_new_holder_counts():
    before = _snap(peak=_rv(1, 1498.0))
    after = _snap(peak=_rv(2, 1500.0))
    assert detect_record_breaks(before, after)[0].holder_id == 2


def test_streak_needs_to_beat_the_record_and_reach_the_floor():
    assert detect_record_breaks(_snap(streak=_rv(1, 3)), _snap(streak=_rv(2, 4))) == []
    assert detect_record_breaks(_snap(streak=_rv(1, 11)), _snap(streak=_rv(1, 11))) == []
    assert detect_record_breaks(_snap(streak=_rv(1, STREAK_MIN - 1)), _snap(streak=_rv(2, STREAK_MIN)))
    assert detect_record_breaks(_snap(streak=_rv(1, 11)), _snap(streak=_rv(1, 12)))


def test_defense_floor():
    assert detect_record_breaks(_snap(defense=_rv(1, 1)), _snap(defense=_rv(2, 2))) == []
    assert detect_record_breaks(_snap(defense=_rv(1, 2)), _snap(defense=_rv(2, DEFENSE_MIN)))


def test_duration_floor_and_margin():
    old = _snap(duration=_rv(1, 20, 2))
    assert detect_record_breaks(old, _snap(duration=_rv(3, 25, 4))) == []              # ниже порога
    old = _snap(duration=_rv(1, 40, 2))
    assert detect_record_breaks(old, _snap(duration=_rv(3, 40.5, 4))) == []            # меньше минуты разницы
    assert detect_record_breaks(old, _snap(duration=_rv(3, 45, 4)))[0].kind == KIND_DURATION


def test_first_measurement_is_silent():
    for kind_snap in (
        _snap(peak=None), _snap(streak=None), _snap(defense=None), _snap(duration=None),
    ):
        assert detect_record_breaks(kind_snap, _snap(
            peak=_rv(1, 1600), streak=_rv(1, 9), defense=_rv(1, 5), duration=_rv(1, 60, 2))) == []


def test_untouched_records_produce_nothing():
    snap = _snap(peak=_rv(1, 1463), streak=_rv(2, 11), defense=_rv(3, 1), duration=_rv(4, 75, 5))
    assert detect_record_breaks(snap, snap) == []


# ── Подсчёт рекордов ──────────────────────────────────────────────────────────

def test_match_minutes_ignores_forgotten_results():
    m = Match(challenger_id=1, challenged_id=2, status=MatchStatus.completed)
    m.accepted_at = datetime(2026, 6, 1, 12, 0)
    m.completed_at = m.accepted_at + timedelta(minutes=20)
    assert match_minutes(m) == 20
    m.completed_at = m.accepted_at + timedelta(minutes=DURATION_MAX_MINUTES + 1)
    assert match_minutes(m) is None
    m.accepted_at = None
    assert match_minutes(m) is None


def test_longest_match_and_best_streak_helpers():
    base = datetime(2026, 6, 1, 12, 0)
    a, b = _player(1, "A"), _player(2, "B")
    a.id, b.id = 1, 2
    ms = []
    for i in range(3):
        m = _completed(a, b, a.id, 5.0, base + timedelta(days=i))
        m.accepted_at = m.completed_at - timedelta(minutes=10 + i * 5)
        ms.append(m)
    ms.append(_completed(a, b, b.id, 5.0, base + timedelta(days=5)))
    longest = longest_match(ms)
    assert longest is not None and longest.value == 20
    best = best_win_streak(ms)
    assert best is not None and best.holder_id == 1 and best.value == 3
    assert best_win_streak([]) is None and longest_match([]) is None


# ── Тексты ────────────────────────────────────────────────────────────────────

NAMES = {1: "Алиса", 2: "Боб <b>", 3: "Вика"}


def test_peak_text():
    text = ping_text(RecordBreak(KIND_PEAK, 1, 1500.4, None, 2, 1463.0), NAMES)
    assert text == (
        "🏔 Рекорд клуба: <b>Алиса</b> дошёл до 1500.4. Выше в истории клуба никто не поднимался. "
        "Прошлый рекорд: Боб &lt;b&gt;, 1463.0."
    )


def test_streak_text():
    text = ping_text(RecordBreak(KIND_STREAK, 1, 12.0, None, 2, 11.0), NAMES)
    assert text == (
        "🔥 Рекорд клуба: <b>Алиса</b> выиграл 12 матчей подряд. Длиннее серии в истории клуба не было. "
        "Прошлый рекорд: Боб &lt;b&gt;, 11."
    )


def test_defense_text():
    text = ping_text(RecordBreak(KIND_DEFENSE, 1, 3.0, None, 2, 2.0), NAMES)
    assert text == (
        "🛡 Рекорд клуба: <b>Алиса</b> защитил трон 3 раза подряд. Дольше никто не держал. "
        "Прошлый рекорд: Боб &lt;b&gt;, 2."
    )


def test_duration_text_uses_correct_plural():
    text = ping_text(RecordBreak(KIND_DURATION, 1, 75.4, 3, 2, 74.0), NAMES)
    assert text == (
        "⏱ Рекорд клуба: <b>Алиса</b> vs <b>Вика</b>, 75 минут. Дольше матча в клубе не было. "
        "Прошлый рекорд: 74 минуты."
    )


# ── Снимок, рассылка, антиспам ────────────────────────────────────────────────

async def _club_with_streak_record(db, bob_streak=4, alice_streak=4):
    alice, bob, carol = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Carol")
    db.add_all([alice, bob, carol])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    for i in range(bob_streak):
        db.add(_completed(bob, carol, bob.id, 5.0, base + timedelta(hours=i)))
    for i in range(alice_streak):
        db.add(_completed(alice, carol, alice.id, 5.0, base + timedelta(days=1, hours=i)))
    await db.commit()
    return alice, bob, carol


async def test_snapshot_reflects_streak_and_ignores_excluded_match(db):
    alice, bob, carol = await _club_with_streak_record(db)
    snap = await snapshot_club_records(db)
    assert snap[KIND_STREAK].value == 4
    last = (await db.execute(select(Match).order_by(Match.id.desc()))).scalars().first()
    snap2 = await snapshot_club_records(db, exclude_match_id=last.id)
    assert snap2[KIND_STREAK].value == 4          # у Боба всё ещё 4
    assert snap[KIND_DEFENSE] is None and snap[KIND_DURATION] is None


async def test_send_record_pings_sends_to_everyone_once_then_cools_down(db):
    alice, bob, carol = await _club_with_streak_record(db)
    before = await snapshot_club_records(db)
    db.add(_completed(alice, carol, alice.id, 5.0, datetime(2026, 6, 3, 12, 0, 0)))   # 5-я победа Алисы
    await db.commit()

    bot = AsyncMock()
    assert await send_record_pings(db, bot, before) == 1
    texts = [c.args[1] if len(c.args) > 1 else c.kwargs.get("text") for c in bot.send_message.await_args_list]
    assert len(texts) == 3 and all("Рекорд клуба" in str(t) for t in texts)
    assert "5 матчей подряд" in str(texts[0])

    logged = (await db.execute(select(ClubRecordPing))).scalars().all()
    assert [(r.kind, r.player_id, r.value) for r in logged] == [(KIND_STREAK, alice.id, 5.0)]

    bot2 = AsyncMock()
    assert await send_record_pings(db, bot2, before) == 0     # тот же игрок, тот же рекорд — молчим
    bot2.send_message.assert_not_awaited()


async def test_cooldown_expires_after_a_week(db):
    alice, bob, carol = await _club_with_streak_record(db)
    before = await snapshot_club_records(db)
    db.add(_completed(alice, carol, alice.id, 5.0, datetime(2026, 6, 3, 12, 0, 0)))
    db.add(ClubRecordPing(kind=KIND_STREAK, player_id=alice.id, value=5.0,
                          pinged_at=datetime(2020, 1, 1)))                      # давно
    await db.commit()
    assert await send_record_pings(db, AsyncMock(), before) == 1


async def test_nothing_is_sent_without_a_broken_record(db):
    await _club_with_streak_record(db)
    before = await snapshot_club_records(db)
    bot = AsyncMock()
    assert await send_record_pings(db, bot, before) == 0
    bot.send_message.assert_not_awaited()


async def test_confirm_result_pings_when_the_streak_record_falls(db):
    alice, bob, carol = await _club_with_streak_record(db)
    m = Match(challenger_id=alice.id, challenged_id=carol.id, status=MatchStatus.accepted,
              accepted_at=datetime(2026, 6, 5, 12, 0, 0))
    db.add(m)
    await db.commit()
    st = _state(alice.id)
    await st.set_state(MatchResultStates.confirming)
    await st.update_data(
        match_id=m.id, reporter_player_id=alice.id, is_draw=False,
        sets_data=[{"reporter": 11, "opponent": 3}, {"reporter": 11, "opponent": 4}],
    )
    bot = AsyncMock()
    await confirm_result(_callback(1, f"confirm_{m.id}"), db, st, bot)

    texts = [str(c.args[1]) for c in bot.send_message.await_args_list if len(c.args) > 1]
    assert sum("🔥 Рекорд клуба" in t for t in texts) == 3       # всем трём игрокам


async def test_confirm_result_survives_a_failing_snapshot(db, monkeypatch):
    alice, bob, carol = await _club_with_streak_record(db)
    m = Match(challenger_id=alice.id, challenged_id=carol.id, status=MatchStatus.accepted,
              accepted_at=datetime(2026, 6, 5, 12, 0, 0))
    db.add(m)
    await db.commit()

    async def boom(*_a, **_k):
        raise RuntimeError("снимок упал")

    monkeypatch.setattr("bot.handlers.match_result.snapshot_club_records", boom)
    st = _state(alice.id)
    await st.set_state(MatchResultStates.confirming)
    await st.update_data(
        match_id=m.id, reporter_player_id=alice.id, is_draw=False,
        sets_data=[{"reporter": 11, "opponent": 3}, {"reporter": 11, "opponent": 4}],
    )
    await confirm_result(_callback(1, f"confirm_{m.id}"), db, st, AsyncMock())
    fresh = (await db.execute(select(Match).where(Match.id == m.id))).scalar_one()
    assert fresh.status == MatchStatus.completed and fresh.winner_id == alice.id


def test_constants_are_the_agreed_ones():
    assert PEAK_STEP == 100 and STREAK_MIN == 5 and DEFENSE_MIN == 3
    assert DURATION_MIN_MINUTES == 30 and DURATION_MAX_MINUTES == 120


@pytest.mark.parametrize("n,word", [(31, "минута"), (32, "минуты"), (45, "минут"), (111, "минут")])
def test_minutes_plural(n, word):
    text = ping_text(RecordBreak(KIND_DURATION, 1, float(n), 3, 2, 30.0), NAMES)
    assert f"{n} {word}." in text
