"""Сравнение периодов в дайджестах и пометка рекорда клуба (v2.141.0)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot.scheduler as sched
from bot.utils import msk_day_start
from tests.conftest import _completed, _player


def _totals(matches: int, sets: int, points: int) -> dict:
    return {"matches": matches, "sets": sets, "points": points}


# ── Чистые хелперы ──────────────────────────────────────────────────────────────

def test_pct_change_basic_and_zero_base():
    assert sched._pct_change(12, 10) == 20
    assert sched._pct_change(8, 10) == -20
    assert sched._pct_change(10, 10) == 0
    assert sched._pct_change(5, 0) is None   # база пуста — молча пропускаем


def test_comparison_line_up_down_and_flat():
    line = sched._comparison_line(
        _totals(12, 30, 600), _totals(10, 30, 800), "прошлому месяцу"
    )
    assert line is not None
    assert "К прошлому месяцу" in line
    assert "матчи ▲ +20%" in line
    assert "партии ▬ 0%" in line
    assert "очки ▼ -25%" in line


def test_comparison_line_skips_metrics_with_zero_base():
    line = sched._comparison_line(
        _totals(5, 10, 100), _totals(0, 10, 0), "прошлому кварталу"
    )
    assert line is not None
    assert "матчи" not in line and "очки" not in line
    assert "партии ▬ 0%" in line


def test_comparison_line_none_when_whole_baseline_empty():
    assert sched._comparison_line(_totals(5, 10, 100), _totals(0, 0, 0), "x") is None


def test_period_totals_counts_matches_sets_and_points():
    m1 = SimpleNamespace(sets_data=[{"w": 11, "l": 5}, {"w": 11, "l": 9}])
    m2 = SimpleNamespace(sets_data=[{"w": 11, "l": 0}])
    m3 = SimpleNamespace(sets_data=None)
    assert sched._period_totals([m1, m2, m3]) == {"matches": 3, "sets": 3, "points": 47}


def _m(challenger: int, challenged: int, winner: int | None, rc: float = 10.0):
    return SimpleNamespace(
        challenger_id=challenger, challenged_id=challenged,
        winner_id=winner, rating_change=rc,
    )


def test_personal_line_compares_matches_winrate_and_rating():
    prev = [_m(1, 2, 1), _m(1, 2, 2)]                       # 2 матча, 50%, +10 -10 = 0.0
    cur = [_m(1, 2, 1), _m(1, 2, 1), _m(1, 2, 1), _m(1, 2, 2)]  # 4 матча, 75%, +20
    line = sched._personal_vs_previous_line(1, cur, prev, "прошлого месяца")
    assert line is not None
    assert "прошлого месяца" in line
    assert "матчей 2 → 4" in line
    assert "винрейт 50% → 75%" in line
    assert "прирост рейтинга +0.0 → +20.0" in line


def test_personal_line_needs_player_in_both_periods():
    assert sched._personal_vs_previous_line(1, [_m(1, 2, 1)], [_m(3, 4, 3)], "x") is None
    assert sched._personal_vs_previous_line(1, [], [_m(1, 2, 1)], "x") is None


def test_club_record_line_thresholds():
    assert sched._club_record_line(6, 4) is not None
    assert "прежний рекорд — 4" in sched._club_record_line(6, 4)
    assert sched._club_record_line(4, 4) is None     # равенство — не рекорд
    assert sched._club_record_line(2, 1) is None     # рекорда ещё не существовало (< 3)
    assert sched._club_record_line(5, 0) is None     # нет истории


# ── Недельный «Клубный пульс»: партии и очки ───────────────────────────────────

async def test_weekly_pulse_compares_sets_and_points_too(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with db_factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        for i in range(2):                    # эта неделя: 2 матча по 1 партии (16 очков)
            s.add(_completed(p1, p2, p1.id, 10.0, now - timedelta(hours=5 - i)))
        for week in range(1, 5):              # прошлые 4 недели: по 1 такому матчу
            s.add(_completed(p1, p2, p1.id, 10.0, now - timedelta(days=7 * week + 1)))
        await s.commit()

    bot = AsyncMock()
    await sched.send_weekly_digest(bot)

    text = bot.send_message.call_args_list[0][0][1]
    assert "К среднему за 4 недели" in text
    assert "матчи ▲ +100%" in text
    assert "партии ▲ +100%" in text
    assert "очки ▲ +100%" in text


async def test_weekly_pulse_silent_without_baseline(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with db_factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(_completed(p1, p2, p1.id, 10.0, now - timedelta(hours=2)))
        await s.commit()

    bot = AsyncMock()
    await sched.send_weekly_digest(bot)

    text = bot.send_message.call_args_list[0][0][1]
    assert "Сыграно за неделю" in text
    assert "К среднему" not in text


# ── Месяц ──────────────────────────────────────────────────────────────────────

def _month_windows():
    msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + sched.MSK_OFFSET
    month_end = msk_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start = (month_end - timedelta(days=1)).replace(day=1)
    prev_start = (month_start - timedelta(days=1)).replace(day=1)
    return prev_start, month_start


async def test_monthly_summary_compares_with_previous_month(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    prev_start, month_start = _month_windows()
    cur = (month_start + timedelta(days=5)) - sched.MSK_OFFSET
    prev = (prev_start + timedelta(days=5)) - sched.MSK_OFFSET

    async with db_factory() as s:
        p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
        s.add_all([p1, p2, p3])
        await s.flush()
        for i in range(4):                       # этот месяц: 4 матча, Alice выигрывает все
            s.add(_completed(p1, p2, p1.id, 10.0, cur + timedelta(hours=i)))
        for i in range(2):                       # прошлый месяц: 2 матча, Alice 1:1
            s.add(_completed(p1, p2, (p1.id, p2.id)[i], 10.0, prev + timedelta(hours=i)))
        s.add(_completed(p3, p2, p3.id, 10.0, prev))   # Cara играла только в прошлом
        await s.commit()

    bot = AsyncMock()
    await sched.send_monthly_summary(bot)

    texts = {c.args[0]: c.args[1] for c in bot.send_message.call_args_list}
    alice = texts[1]
    assert "К прошлому месяцу" in alice
    assert "матчи ▲ +33%" in alice            # 4 против 3
    assert "Ты против прошлого месяца" in alice
    assert "матчей 2 → 4" in alice
    assert "винрейт 50% → 100%" in alice
    assert "Ты против" not in texts[3]        # Cara в этом месяце не играла


async def test_monthly_summary_no_comparison_without_previous_month(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    _, month_start = _month_windows()
    cur = (month_start + timedelta(days=5)) - sched.MSK_OFFSET

    async with db_factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(_completed(p1, p2, p1.id, 10.0, cur))
        await s.commit()

    bot = AsyncMock()
    await sched.send_monthly_summary(bot)

    text = bot.send_message.call_args_list[0][0][1]
    assert "Сыграно за месяц" in text
    assert "К прошлому месяцу" not in text
    assert "Ты против" not in text


# ── Квартал ────────────────────────────────────────────────────────────────────

async def test_quarterly_summary_compares_with_previous_quarter(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + sched.MSK_OFFSET
    q_start, _ = sched._quarter_bounds_msk(msk_now)
    prev_q_start, _ = sched._quarter_bounds_msk(q_start)
    cur = (q_start + timedelta(days=5)) - sched.MSK_OFFSET
    prev = (prev_q_start + timedelta(days=5)) - sched.MSK_OFFSET

    async with db_factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        for i in range(3):
            s.add(_completed(p1, p2, p1.id, 10.0, cur + timedelta(hours=i)))
        s.add(_completed(p1, p2, p1.id, 10.0, prev))
        await s.commit()

    bot = AsyncMock()
    await sched.send_quarterly_summary(bot)

    text = bot.send_message.call_args_list[0][0][1]
    assert "К прошлому кварталу" in text
    assert "матчи ▲ +200%" in text            # 3 против 1


async def test_quarterly_summary_no_comparison_without_previous_quarter(
    monkeypatch, db_factory,
):
    monkeypatch.setattr(sched, "async_session", db_factory)
    msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + sched.MSK_OFFSET
    q_start, _ = sched._quarter_bounds_msk(msk_now)
    cur = (q_start + timedelta(days=5)) - sched.MSK_OFFSET

    async with db_factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(_completed(p1, p2, p1.id, 10.0, cur))
        await s.commit()

    bot = AsyncMock()
    await sched.send_quarterly_summary(bot)

    assert "К прошлому кварталу" not in bot.send_message.call_args_list[0][0][1]


# ── День: без сравнения, но с пометкой рекорда ─────────────────────────────────

async def test_daily_summary_has_no_comparison_line(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    yesterday = msk_day_start() - timedelta(hours=5)
    async with db_factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(_completed(p1, p2, p1.id, 10.0, now))
        s.add(_completed(p1, p2, p1.id, 10.0, yesterday))
        await s.commit()

    bot = AsyncMock()
    await sched.send_daily_summary(bot)

    text = bot.send_message.call_args_list[0][0][1]
    assert "Сыграно:" in text
    assert "К прошлому" not in text and "К среднему" not in text


async def test_daily_summary_marks_club_record(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    yesterday = msk_day_start() - timedelta(hours=5)
    async with db_factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        for i in range(3):                       # прежний рекорд — 3 матча за день
            s.add(_completed(p1, p2, p1.id, 10.0, yesterday - timedelta(minutes=i)))
        for _ in range(4):                       # сегодня — 4
            s.add(_completed(p1, p2, p2.id, 10.0, now))
        await s.commit()

    bot = AsyncMock()
    await sched.send_daily_summary(bot)

    text = bot.send_message.call_args_list[0][0][1]
    assert "Рекорд клуба" in text
    assert "прежний рекорд — 3" in text


async def test_daily_summary_no_record_mark_when_not_beaten(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    yesterday = msk_day_start() - timedelta(hours=5)
    async with db_factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        for i in range(5):
            s.add(_completed(p1, p2, p1.id, 10.0, yesterday - timedelta(minutes=i)))
        for _ in range(3):
            s.add(_completed(p1, p2, p2.id, 10.0, now))
        await s.commit()

    bot = AsyncMock()
    await sched.send_daily_summary(bot)

    assert "Рекорд клуба" not in bot.send_message.call_args_list[0][0][1]
