"""
Тесты статистики личных встреч (H2H).
Запуск: pytest tests/test_h2h.py
"""
from datetime import datetime
from types import SimpleNamespace

from bot.utils import compute_h2h


def mk(sets, winner_id, challenger_id, challenged_id, rating_change=8.0, day=29):
    return SimpleNamespace(
        sets_data=sets,
        winner_id=winner_id,
        challenger_id=challenger_id,
        challenged_id=challenged_id,
        rating_change=rating_change,
        completed_at=datetime(2026, 5, day, 12, 0, 0),
    )


# ── Базовые случаи ────────────────────────────────────────────────────────────

def test_empty():
    s = compute_h2h([], viewer_id=1, opponent_id=2)
    assert s["total"] == 0
    assert s["wins"] == 0 and s["losses"] == 0 and s["draws"] == 0
    assert s["streak_desc"] is None
    assert s["first_date"] is None
    assert s["best_win"] is None


def test_counts_wins_losses_draws():
    matches = [
        mk([{"w": 11, "l": 7}], winner_id=1, challenger_id=1, challenged_id=2, day=29),
        mk([{"w": 11, "l": 8}], winner_id=2, challenger_id=1, challenged_id=2, day=28),
        mk([{"w": 11, "l": 9}, {"w": 9, "l": 11}], winner_id=None, challenger_id=1, challenged_id=2, day=27),
    ]
    s = compute_h2h(matches, viewer_id=1, opponent_id=2)
    assert s["total"] == 3
    assert s["wins"] == 1
    assert s["losses"] == 1
    assert s["draws"] == 1


# ── Подсчёт партий с перспективы viewer ───────────────────────────────────────

def test_sets_viewer_won():
    """Победа viewer (challenger): sets в его перспективе."""
    m = mk([{"w": 11, "l": 7}, {"w": 11, "l": 5}], winner_id=1, challenger_id=1, challenged_id=2)
    s = compute_h2h([m], viewer_id=1, opponent_id=2)
    assert s["my_sets"] == 2 and s["opp_sets"] == 0


def test_sets_viewer_lost_inverted():
    """Поражение viewer: sets в перспективе победителя-соперника → инверсия."""
    m = mk([{"w": 11, "l": 8}, {"w": 11, "l": 9}], winner_id=2, challenger_id=1, challenged_id=2)
    s = compute_h2h([m], viewer_id=1, opponent_id=2)
    assert s["my_sets"] == 0 and s["opp_sets"] == 2


def test_sets_draw_challenger_perspective():
    m = mk([{"w": 11, "l": 9}, {"w": 9, "l": 11}], winner_id=None, challenger_id=1, challenged_id=2)
    s = compute_h2h([m], viewer_id=1, opponent_id=2)
    assert s["my_sets"] == 1 and s["opp_sets"] == 1


def test_sets_viewer_is_challenged():
    """viewer — challenged и победил: sets в его (победителя) перспективе."""
    m = mk([{"w": 11, "l": 5}], winner_id=1, challenger_id=2, challenged_id=1)
    s = compute_h2h([m], viewer_id=1, opponent_id=2)
    assert s["my_sets"] == 1 and s["opp_sets"] == 0


# ── Рейтинг и лучшая победа ───────────────────────────────────────────────────

def test_rating_delta_and_best_win():
    matches = [
        mk([{"w": 11, "l": 7}], winner_id=1, challenger_id=1, challenged_id=2, rating_change=8.0, day=29),
        mk([{"w": 11, "l": 8}], winner_id=2, challenger_id=1, challenged_id=2, rating_change=6.0, day=28),
        mk([{"w": 11, "l": 9}, {"w": 9, "l": 11}], winner_id=None, challenger_id=1, challenged_id=2, rating_change=2.0, day=27),
    ]
    s = compute_h2h(matches, viewer_id=1, opponent_id=2)
    # +8 (win) -6 (loss) +2 (draw, challenger) = +4
    assert s["rating_delta"] == 4.0
    assert s["best_win"] == 8.0


# ── Серия ─────────────────────────────────────────────────────────────────────

def test_win_streak():
    matches = [
        mk([{"w": 11, "l": 7}], winner_id=1, challenger_id=1, challenged_id=2, day=29),
        mk([{"w": 11, "l": 7}], winner_id=1, challenger_id=1, challenged_id=2, day=28),
        mk([{"w": 11, "l": 7}], winner_id=1, challenger_id=1, challenged_id=2, day=27),
        mk([{"w": 11, "l": 7}], winner_id=2, challenger_id=1, challenged_id=2, day=26),
    ]
    s = compute_h2h(matches, viewer_id=1, opponent_id=2)
    assert s["streak_desc"] == "ты ведёшь — 3 побед подряд"


def test_loss_streak():
    matches = [
        mk([{"w": 11, "l": 7}], winner_id=2, challenger_id=1, challenged_id=2, day=29),
        mk([{"w": 11, "l": 7}], winner_id=2, challenger_id=1, challenged_id=2, day=28),
    ]
    s = compute_h2h(matches, viewer_id=1, opponent_id=2)
    assert s["streak_desc"] == "ты проигрываешь — 2 подряд"


def test_no_streak_when_single_win():
    m = mk([{"w": 11, "l": 7}], winner_id=1, challenger_id=1, challenged_id=2)
    s = compute_h2h([m], viewer_id=1, opponent_id=2)
    assert s["streak_desc"] is None


def test_no_streak_when_latest_is_draw():
    matches = [
        mk([{"w": 11, "l": 9}, {"w": 9, "l": 11}], winner_id=None, challenger_id=1, challenged_id=2, day=29),
        mk([{"w": 11, "l": 7}], winner_id=1, challenger_id=1, challenged_id=2, day=28),
    ]
    s = compute_h2h(matches, viewer_id=1, opponent_id=2)
    assert s["streak_desc"] is None


def test_first_date_is_earliest():
    matches = [
        mk([{"w": 11, "l": 7}], winner_id=1, challenger_id=1, challenged_id=2, day=29),
        mk([{"w": 11, "l": 7}], winner_id=1, challenger_id=1, challenged_id=2, day=20),
    ]
    s = compute_h2h(matches, viewer_id=1, opponent_id=2)
    assert s["first_date"] == datetime(2026, 5, 20, 12, 0, 0)
