"""
Тесты валидации счёта партии настольного тенниса.
Запуск: pytest tests/test_validation.py
"""
from bot.services.validation import validate_set_score


# ── Корректные счёта ──────────────────────────────────────────────────────────

def test_normal_win():
    assert validate_set_score(11, 7) is None
    assert validate_set_score(11, 0) is None
    assert validate_set_score(11, 9) is None


def test_opponent_wins_also_valid():
    """Счёт где противник выиграл тоже корректен."""
    assert validate_set_score(7, 11) is None
    assert validate_set_score(0, 11) is None


def test_deuce_win():
    assert validate_set_score(12, 10) is None
    assert validate_set_score(13, 11) is None
    assert validate_set_score(15, 13) is None
    assert validate_set_score(20, 18) is None


def test_deuce_opponent_wins():
    assert validate_set_score(10, 12) is None
    assert validate_set_score(11, 13) is None


# ── Некорректные счёта ────────────────────────────────────────────────────────

def test_eleven_ten_is_invalid():
    """11:10 — победитель набрал 11, но отрыв только 1. Не дьюс (нужно 12:10)."""
    assert validate_set_score(11, 10) == "invalid"
    assert validate_set_score(10, 11) == "invalid"


def test_winner_less_than_11():
    """Победитель набрал меньше 11 — некорректно."""
    assert validate_set_score(10, 8) == "invalid"
    assert validate_set_score(9, 7) == "invalid"


def test_large_gap_at_deuce_is_invalid():
    """15:5 некорректно — при 11:5 уже нужно было остановиться."""
    assert validate_set_score(15, 5) == "invalid"


def test_deuce_gap_not_two():
    """13:10 некорректно — при дьюсе нужен ровно отрыв в 2 очка."""
    assert validate_set_score(13, 10) == "invalid"


# ── Ошибки ввода ──────────────────────────────────────────────────────────────

def test_draw_is_rejected():
    assert validate_set_score(11, 11) == "draw"
    assert validate_set_score(0, 0) == "draw"
    assert validate_set_score(7, 7) == "draw"


def test_negative_score():
    assert validate_set_score(-1, 11) == "negative"
    assert validate_set_score(11, -1) == "negative"
    assert validate_set_score(-5, -3) == "negative"
