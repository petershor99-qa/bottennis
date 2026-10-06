"""«До звания» на экране «Мой профиль» (v2.157.0)."""
from datetime import datetime

from hypothesis import given
from hypothesis import strategies as st

from bot.handlers.profile import _build_stats_screen, _rank_title_progress_line
from bot.utils import RANK_TITLE_BANDS, RANK_TITLE_TOP, rank_progress, rank_title
from tests.conftest import _completed, _player


def test_junior_progress_points_to_middle():
    assert rank_progress(1000.0) == ("Миддл", 50.0)


def test_each_band_points_to_the_next_title():
    assert rank_progress(1100.0) == ("Сеньор", 100.0)
    assert rank_progress(1250.0) == ("Тим лид", 50.0)
    assert rank_progress(1350.0) == (RANK_TITLE_TOP, 50.0)


def test_top_rank_has_no_progress():
    assert rank_progress(1400.0) is None
    assert rank_progress(1800.0) is None
    assert _rank_title_progress_line(1500.0) is None


def test_float_noise_does_not_round_the_remainder_up():
    # 1050 - 1026.6 в float = 23.400000000000091 — должно остаться ровно 23.4
    assert rank_progress(1026.6) == ("Миддл", 23.4)


def test_remainder_is_never_zero_just_below_a_boundary():
    title, left = rank_progress(1049.96)
    assert title == "Миддл" and left == 0.1


def test_line_text():
    assert _rank_title_progress_line(1176.6) == "🎖 До звания «Сеньор»: +23.4 рейтинга"


@given(st.floats(min_value=0.0, max_value=3000.0, allow_nan=False))
def test_progress_agrees_with_rank_title(rating):
    progress = rank_progress(rating)
    titles = [t for _, t in RANK_TITLE_BANDS] + [RANK_TITLE_TOP]
    if rank_title(rating) == RANK_TITLE_TOP:
        assert progress is None
    else:
        title, left = progress
        assert title == titles[titles.index(rank_title(rating)) + 1]
        assert 0.1 <= left <= 1050.0 + 0.1


async def test_personal_profile_shows_title_progress(db):
    p1, p2 = _player(1, "Alice", 1100.0), _player(2, "Bob", 1000.0)
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()
    text, _kb = await _build_stats_screen(db, p1)
    assert "🎖 До звания «Сеньор»: +100.0 рейтинга" in text


async def test_top_rank_player_has_no_title_progress_line(db):
    p1, p2 = _player(1, "Alice", 1450.0), _player(2, "Bob", 1000.0)
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()
    text, _kb = await _build_stats_screen(db, p1)
    assert "До звания" not in text
