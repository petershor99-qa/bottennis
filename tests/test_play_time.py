"""«Золотой час клуба» и «Любимое время» игрока (v2.142.0)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from bot.handlers.leaderboard import _collect_club_records
from bot.handlers.profile import _stats_groups
from bot.services.stats import _compute_player_stats
from bot.utils import busiest_msk_hour, get_career_matches, hour_range_label
from tests.conftest import _completed, _player

# 11:00 UTC == 14:00 МСК
MSK_14 = datetime(2026, 3, 2, 11, 10)


def test_hour_range_label_wraps_at_midnight():
    assert hour_range_label(14) == "14:00–15:00"
    assert hour_range_label(9) == "09:00–10:00"
    assert hour_range_label(23) == "23:00–00:00"


def _at(hour_utc: int):
    return SimpleNamespace(completed_at=datetime(2026, 3, 2, hour_utc, 5))


def test_busiest_hour_converts_utc_to_msk():
    assert busiest_msk_hour([_at(11), _at(11), _at(8)]) == (14, 2)


def test_busiest_hour_tie_prefers_earlier_hour():
    assert busiest_msk_hour([_at(11), _at(8)]) == (11, 1)


def test_busiest_hour_ignores_undated_and_handles_empty():
    assert busiest_msk_hour([]) is None
    assert busiest_msk_hour([SimpleNamespace(completed_at=None)]) is None


async def _two_players(db, hours_utc):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    for i, h in enumerate(hours_utc):
        when = datetime(2026, 3, 2 + i, h, 10)
        db.add(_completed(p1, p2, p1.id, 10.0, when))
    await db.commit()
    return p1, p2


async def test_personal_favorite_time_shown_from_three_matches(db):
    p1, _ = await _two_players(db, [11, 11, 11, 8])
    s = _compute_player_stats(p1, await get_career_matches(db, p1.id, with_opponents=True))
    assert s["fav_hour"] == (14, 3)
    misc = _stats_groups(p1, s)["misc"]
    assert any("Любимое время" in line and "14:00–15:00" in line and "3 матча" in line for line in misc)


async def test_personal_favorite_time_hidden_on_small_sample(db):
    p1, _ = await _two_players(db, [11, 11, 8])
    s = _compute_player_stats(p1, await get_career_matches(db, p1.id, with_opponents=True))
    assert s["fav_hour"] is None
    assert not any("Любимое время" in line for line in _stats_groups(p1, s)["misc"])


async def test_club_golden_hour_record_with_share(db):
    await _two_players(db, [11, 11, 11, 8])
    records = await _collect_club_records(db)
    line = next(r for r in records["volume"] if "Золотой час клуба" in r)
    assert "14:00–15:00" in line
    assert "3 матча" in line
    assert "75% всех" in line


async def test_club_golden_hour_hidden_when_fewer_than_three(db):
    await _two_players(db, [11, 11, 8, 9])
    records = await _collect_club_records(db)
    assert not any("Золотой час клуба" in r for r in records["volume"])


async def test_club_golden_hour_is_clubwide_not_per_player(db):
    """Матчи разных пар на один час складываются — час клубный, не личный."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, MSK_14))
    db.add(_completed(p2, p3, p2.id, 10.0, MSK_14 + timedelta(days=1)))
    db.add(_completed(p1, p3, p3.id, 10.0, MSK_14 + timedelta(days=2)))
    await db.commit()
    records = await _collect_club_records(db)
    assert any("Золотой час клуба" in r and "3 матча" in r for r in records["volume"])
