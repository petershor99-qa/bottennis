"""
Тесты чистых хелперов наполнения дайджестов (без БД и бота).
Запуск: pytest tests/test_digest_helpers.py
"""
from datetime import datetime
from types import SimpleNamespace

from bot.scheduler import _biggest_swing, _longest_no_loss_streak, _total_points
from bot.utils import pluralize_points


def mk(challenger_id, challenged_id, winner_id, rating_change, day, sets_data=None):
    return SimpleNamespace(
        challenger_id=challenger_id,
        challenged_id=challenged_id,
        winner_id=winner_id,
        rating_change=rating_change,
        completed_at=datetime(2026, 6, day, 12, 0, 0),
        sets_data=sets_data,
    )


NAMES = {1: "Alice", 2: "Bob", 3: "Cara", 4: "Dave"}


# ── pluralize_points ─────────────────────────────────────────────────────────

def test_pluralize_points_forms():
    assert pluralize_points(1) == "1 очко"
    assert pluralize_points(2) == "2 очка"
    assert pluralize_points(5) == "5 очков"
    assert pluralize_points(11) == "11 очков"
    assert pluralize_points(21) == "21 очко"


# ── _total_points ─────────────────────────────────────────────────────────────

def test_total_points_sums_all_sets():
    matches = [
        mk(1, 2, 1, 10.0, 1, sets_data=[{"w": 11, "l": 7}, {"w": 11, "l": 9}]),
        mk(2, 1, 2, 5.0, 2, sets_data=[{"w": 11, "l": 3}]),
    ]
    assert _total_points(matches) == (11 + 7) + (11 + 9) + (11 + 3)


def test_total_points_skips_matches_without_sets_data():
    matches = [mk(1, 2, 1, 10.0, 1, sets_data=None)]
    assert _total_points(matches) == 0


# ── _longest_no_loss_streak ────────────────────────────────────────────────────

def test_no_loss_streak_survives_draws_broken_by_loss():
    """Alice: победа-ничья-победа-поражение -> лучшая серия без поражений = 3."""
    matches = [
        mk(1, 3, 1, 10.0, 1),   # Alice победила
        mk(1, 3, None, 3.0, 2),  # ничья
        mk(1, 3, 1, 8.0, 3),    # Alice победила
        mk(1, 3, 3, 12.0, 4),   # Alice проиграла — серия оборвана
        mk(2, 4, 2, 5.0, 5),    # Bob выиграл разово — серия 1, не конкурент
    ]
    result = _longest_no_loss_streak(matches, NAMES)
    assert result is not None
    assert "Alice" in result
    assert "3 матча" in result


def test_no_loss_streak_label_has_no_period_suffix():
    """v2.97.0: заголовок сообщения уже задаёт период (день/неделя) — строка
    метрики больше не дублирует его словом 'дня'/'недели'."""
    matches = [
        mk(1, 3, 1, 10.0, 1), mk(1, 3, None, 3.0, 2), mk(1, 3, 1, 8.0, 3),
    ]
    result = _longest_no_loss_streak(matches, NAMES)
    assert result is not None
    assert "дня" not in result
    assert "недели" not in result
    assert result.startswith("🧱 Без поражений —")


def test_no_loss_streak_none_below_threshold():
    matches = [mk(1, 2, 1, 10.0, 1)]  # одна победа — серии 1, порог не пройден
    assert _longest_no_loss_streak(matches, NAMES) is None


def test_no_loss_streak_empty_matches_returns_none():
    assert _longest_no_loss_streak([], NAMES) is None


# ── _biggest_swing ─────────────────────────────────────────────────────────────

def test_biggest_swing_picks_player_with_most_back_and_forth():
    """Alice выиграла +40 у Bob, проиграла -35 Cara: net +5, но качало на 70.
    У Bob/Cara свои качели меньше (10) — Alice однозначный лидер, без ничьей."""
    matches = [
        mk(1, 2, 1, 40.0, 1),  # Alice bt Bob: Alice +40, Bob -40
        mk(1, 3, 3, 35.0, 2),  # Cara bt Alice: Alice -35, Cara +35
        mk(2, 3, 2, 5.0, 3),   # Bob bt Cara: Bob +5, Cara -5
    ]
    result = _biggest_swing(matches, NAMES)
    assert result is not None
    assert "Alice" in result


def test_biggest_swing_label_has_no_period_suffix():
    """v2.97.0: та же причина, что у _longest_no_loss_streak."""
    matches = [
        mk(1, 2, 1, 40.0, 1),
        mk(1, 3, 3, 35.0, 2),
        mk(2, 3, 2, 5.0, 3),
    ]
    result = _biggest_swing(matches, NAMES)
    assert result is not None
    assert "дня" not in result
    assert "недели" not in result
    assert result.startswith("🎢 Американские горки —")


def test_biggest_swing_none_when_movement_is_all_one_direction():
    """Одна победа — весь путь = чистый рост, качелей (разницы abs_total-|net|) нет."""
    matches = [mk(1, 2, 1, 15.0, 1)]
    assert _biggest_swing(matches, NAMES) is None


def test_biggest_swing_none_when_below_threshold():
    """Небольшие туда-обратно колебания (<20 pts) не считаются «горками»."""
    matches = [
        mk(1, 2, 1, 5.0, 1),
        mk(1, 2, 2, 4.0, 2),
    ]
    assert _biggest_swing(matches, NAMES) is None
