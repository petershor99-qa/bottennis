"""
Тесты рейтинговой формулы ELO.
Запуск: pytest tests/test_rating.py
"""
from bot.services.rating import (
    SHORT_MATCH_MULT,
    calculate_draw_rating_change,
    calculate_rating_change,
    win_probability,
)

# ── win_probability ───────────────────────────────────────────────────────────

def test_win_probability_equal_is_half():
    assert win_probability(1000, 1000) == 0.5


def test_win_probability_symmetric():
    """p(a,b) + p(b,a) == 1."""
    assert abs(win_probability(1200, 900) + win_probability(900, 1200) - 1.0) < 1e-9


def test_win_probability_favourite_above_half():
    assert win_probability(1200, 1000) > 0.5
    assert win_probability(1000, 1200) < 0.5


# ── calculate_rating_change ───────────────────────────────────────────────────

def test_winner_gains_positive_delta():
    """Победитель всегда получает положительную дельту."""
    sets = [{"w": 11, "l": 7}, {"w": 11, "l": 5}, {"w": 11, "l": 3}]
    assert calculate_rating_change(1000, 1000, sets) > 0


def test_underdog_wins_more_than_favourite():
    """Аутсайдер получает больше очков за победу, чем фаворит."""
    sets = [{"w": 11, "l": 7}, {"w": 11, "l": 7}, {"w": 11, "l": 7}]
    delta_underdog = calculate_rating_change(900, 1100, sets)
    delta_favourite = calculate_rating_change(1100, 900, sets)
    assert delta_underdog > delta_favourite


def test_sweep_gives_more_than_close_match():
    """Разгром 3:0 даёт больше очков чем победа 3:2."""
    sets_sweep = [{"w": 11, "l": 7}, {"w": 11, "l": 7}, {"w": 11, "l": 7}]
    sets_close = [
        {"w": 11, "l": 9}, {"w": 9, "l": 11},
        {"w": 11, "l": 9}, {"w": 9, "l": 11},
        {"w": 11, "l": 9},
    ]
    delta_sweep = calculate_rating_change(1000, 1000, sets_sweep)
    delta_close = calculate_rating_change(1000, 1000, sets_close)
    assert delta_sweep > delta_close


def test_crushing_score_gives_more_than_narrow():
    """Победа 11:0 даёт больше очков чем 11:9."""
    sets_crush = [{"w": 11, "l": 0}, {"w": 11, "l": 0}, {"w": 11, "l": 0}]
    sets_narrow = [{"w": 11, "l": 9}, {"w": 11, "l": 9}, {"w": 11, "l": 9}]
    assert calculate_rating_change(1000, 1000, sets_crush) > \
           calculate_rating_change(1000, 1000, sets_narrow)


def test_delta_is_positive_float():
    """Дельта — положительное число с одним знаком после запятой."""
    sets = [{"w": 11, "l": 7}]
    delta = calculate_rating_change(1000, 1000, sets)
    assert isinstance(delta, float)
    assert delta > 0
    # Один знак после запятой — проверяем round(x, 1) не изменяет значение
    assert round(delta, 1) == delta


# ── short_match_penalty ──────────────────────────────────────────────────────

def test_single_set_less_than_two_sets():
    """1-партийный матч даёт меньше очков, чем 2-партийный с тем же счётом."""
    sets_1 = [{"w": 11, "l": 7}]
    sets_2 = [{"w": 11, "l": 7}, {"w": 11, "l": 7}]
    assert calculate_rating_change(1000, 1000, sets_1) < \
           calculate_rating_change(1000, 1000, sets_2)


def test_single_set_penalty_is_075():
    """Коэффициент SHORT_MATCH_MULT = 0.75 применяется к 1-партийному матчу."""
    sets_1 = [{"w": 11, "l": 7}]
    # 2-партийный с тем же счётом: sets_ratio=1.0, pts_ratio одинаковы → delta ÷ 0.75
    sets_2 = [{"w": 11, "l": 7}, {"w": 11, "l": 7}]
    delta_1 = calculate_rating_change(1000, 1000, sets_1)
    delta_2 = calculate_rating_change(1000, 1000, sets_2)
    assert abs(delta_1 / delta_2 - SHORT_MATCH_MULT) < 0.05


def test_two_sets_no_penalty():
    """2-партийный матч не получает штраф."""
    sets_2 = [{"w": 11, "l": 7}, {"w": 11, "l": 7}]
    sets_3 = [{"w": 11, "l": 7}, {"w": 11, "l": 7}, {"w": 11, "l": 7}]
    # 2-0 должен давать столько же, сколько 3-0 с теми же pts_ratio
    # (sets_ratio одинаковый = 1.0, pts_ratio одинаковый) — без штрафа оба равны
    assert calculate_rating_change(1000, 1000, sets_2) == \
           calculate_rating_change(1000, 1000, sets_3)


# ── calculate_draw_rating_change ─────────────────────────────────────────────

def test_draw_equal_ratings_zero():
    """Равные рейтинги — ничья не меняет очки."""
    assert calculate_draw_rating_change(1000, 1000) == 0.0


def test_draw_underdog_challenger_gains():
    """Challenger-аутсайдер получает положительную дельту при ничье."""
    assert calculate_draw_rating_change(900, 1100) > 0


def test_draw_favourite_challenger_loses():
    """Challenger-фаворит теряет очки при ничье."""
    assert calculate_draw_rating_change(1100, 900) < 0


def test_draw_is_symmetric():
    """Дельты challenger'а и challenged'а симметричны."""
    delta = calculate_draw_rating_change(900, 1100)
    assert calculate_draw_rating_change(1100, 900) == -delta
