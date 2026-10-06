"""Property-based тесты (hypothesis) для чистых функций проекта.

Обычный тест проверяет несколько заранее придуманных входов. Здесь вместо этого
описаны СВОЙСТВА, которые должны выполняться для любых входов, а hypothesis сам
подбирает сотни случаев, включая граничные (нули, большие числа, границы пулов).
Покрыто то, что не требует БД и Telegram: формула рейтинга, валидация счёта,
склонение, звания, ось графика, выбор фразы, окно голосования, имя экрана для
счётчика использования.
"""
import math
from datetime import datetime

from hypothesis import given
from hypothesis import strategies as st

from bot.services.rating import (
    SHORT_MATCH_MULT,
    calculate_draw_rating_change,
    calculate_rating_change,
    what_if_range,
    win_probability,
)
from bot.services.usage import normalize_action
from bot.services.validation import validate_set_score
from bot.services.year_vote import is_voting_open
from bot.utils import (
    RANK_TITLE_BANDS,
    RANK_TITLE_TOP,
    _rating_axis_bounds,
    _ru_plural,
    _stable_pool_index,
    hour_range_label,
    rank_title,
)

ratings = st.floats(min_value=0.0, max_value=3000.0, allow_nan=False, allow_infinity=False)


@st.composite
def sets_data(draw, min_sets: int = 1, max_sets: int = 5):
    """Список партий в перспективе победителя: ключи w/l, счёт каждой партии
    произвольный (в том числе с проигранными победителем партиями — камбэк)."""
    n = draw(st.integers(min_value=min_sets, max_value=max_sets))
    pair = st.tuples(st.integers(0, 30), st.integers(0, 30))
    return [{"w": w, "l": loser} for w, loser in (draw(pair) for _ in range(n))]


# ── Валидация счёта партии ────────────────────────────────────────────────────

@given(st.integers(min_value=0, max_value=9))
def test_normal_win_is_valid_in_both_directions(loser):
    assert validate_set_score(11, loser) is None
    assert validate_set_score(loser, 11) is None


@given(st.integers(min_value=10, max_value=500))
def test_deuce_win_by_two_is_valid(loser):
    assert validate_set_score(loser + 2, loser) is None
    assert validate_set_score(loser, loser + 2) is None


@given(st.integers(min_value=0, max_value=500))
def test_equal_scores_are_a_draw(score):
    assert validate_set_score(score, score) == "draw"


@given(st.integers(min_value=-500, max_value=-1), st.integers(min_value=-500, max_value=500))
def test_negative_score_is_rejected_whatever_the_other_side(neg, other):
    assert validate_set_score(neg, other) == "negative"
    assert validate_set_score(other, neg) == "negative"


@given(st.integers(min_value=0, max_value=500), st.integers(min_value=0, max_value=500))
def test_validation_is_symmetric(a, b):
    assert validate_set_score(a, b) == validate_set_score(b, a)


@given(st.integers(min_value=0, max_value=10), st.integers(min_value=0, max_value=10))
def test_nobody_reaching_eleven_is_invalid(a, b):
    """Партия до 11: пока победитель не набрал 11, счёт не завершён."""
    if a != b:
        assert validate_set_score(a, b) == "invalid"


@given(st.integers(min_value=10, max_value=500), st.integers(min_value=3, max_value=50))
def test_deuce_gap_other_than_two_is_invalid(loser, gap):
    assert validate_set_score(loser + gap, loser) == "invalid"


@given(st.integers(min_value=12, max_value=500))
def test_eleven_against_ten_or_more_is_invalid(winner):
    """11:10 и 11:11+ — ещё не конец; 12:5 — не бывает (до 11, не до 12)."""
    assert validate_set_score(11, 10) == "invalid"
    assert validate_set_score(winner, 5) == "invalid"


# ── Рейтинг: ожидаемый результат ──────────────────────────────────────────────

@given(ratings, ratings)
def test_win_probability_is_a_probability_and_complementary(a, b):
    p = win_probability(a, b)
    assert 0.0 <= p <= 1.0
    assert math.isclose(p + win_probability(b, a), 1.0, abs_tol=1e-9)


@given(ratings, ratings, st.floats(min_value=0.1, max_value=500.0))
def test_win_probability_grows_with_own_rating(a, b, bump):
    assert win_probability(a + bump, b) >= win_probability(a, b)


# ── Рейтинг: дельта победы ────────────────────────────────────────────────────

@given(ratings, ratings, sets_data())
def test_win_delta_is_bounded(winner, loser, sets):
    delta = calculate_rating_change(winner, loser, sets)
    # база ≤ 32, множитель счёта ≤ 1.4 → потолок 44.8; победа не отнимает очки
    assert 0.0 <= delta <= 44.8


@given(ratings, ratings, st.floats(min_value=0.1, max_value=800.0), sets_data())
def test_beating_a_stronger_opponent_never_pays_less(winner, loser, bump, sets):
    """При том же счёте победа над более сильным даёт не меньше очков."""
    weak = calculate_rating_change(winner, loser, sets)
    strong = calculate_rating_change(winner, loser + bump, sets)
    assert strong >= weak


@given(ratings, ratings, st.integers(0, 29), st.integers(0, 29), st.integers(1, 3))
def test_more_points_in_a_set_never_lowers_delta(winner, loser, w, l_pts, extra):
    """Больший перевес по очкам в той же партии не уменьшает дельту."""
    base = [{"w": w, "l": l_pts}, {"w": 11, "l": 5}]
    better = [{"w": w + extra, "l": l_pts}, {"w": 11, "l": 5}]
    assert calculate_rating_change(winner, loser, better) >= calculate_rating_change(winner, loser, base)


@given(ratings, ratings)
def test_sweep_pays_at_least_as_much_as_a_five_setter(winner, loser):
    sweep = [{"w": 11, "l": 5}] * 3
    five = [{"w": 11, "l": 5}, {"w": 5, "l": 11}, {"w": 11, "l": 5}, {"w": 5, "l": 11}, {"w": 11, "l": 5}]
    assert calculate_rating_change(winner, loser, sweep) >= calculate_rating_change(winner, loser, five)


@given(ratings, ratings)
def test_single_set_match_is_discounted(winner, loser):
    one = calculate_rating_change(winner, loser, [{"w": 11, "l": 5}])
    two = calculate_rating_change(winner, loser, [{"w": 11, "l": 5}, {"w": 11, "l": 5}])
    assert one <= two
    assert one <= round(two * SHORT_MATCH_MULT + 0.1, 1)


@given(ratings, ratings, sets_data())
def test_win_delta_is_rounded_to_one_decimal(winner, loser, sets):
    delta = calculate_rating_change(winner, loser, sets)
    assert delta == round(delta, 1)


# ── Рейтинг: ничья ────────────────────────────────────────────────────────────

@given(ratings, ratings)
def test_draw_is_antisymmetric(a, b):
    assert calculate_draw_rating_change(a, b) == -calculate_draw_rating_change(b, a)


@given(ratings)
def test_draw_of_equals_changes_nothing(r):
    assert calculate_draw_rating_change(r, r) == 0.0


@given(ratings, ratings)
def test_draw_delta_is_bounded_by_half_k(a, b):
    assert abs(calculate_draw_rating_change(a, b)) <= 16.0


@given(ratings, ratings)
def test_underdog_gains_and_favourite_loses_on_draw(a, b):
    delta = calculate_draw_rating_change(a, b)
    if a < b:
        assert delta >= 0
    if a > b:
        assert delta <= 0


# ── Калькулятор «Что если» ────────────────────────────────────────────────────

@given(ratings, ratings, st.booleans())
def test_what_if_ranges_are_ordered_and_non_negative(viewer, opponent, newcomer):
    (win_lo, win_hi), (lose_lo, lose_hi) = what_if_range(viewer, opponent, newcomer)
    assert 0.0 <= win_lo <= win_hi
    assert 0.0 <= lose_lo <= lose_hi


# ── Склонение ─────────────────────────────────────────────────────────────────

@given(st.integers(min_value=0, max_value=10_000))
def test_plural_agrees_with_russian_rules(n):
    text = _ru_plural(n, "матч", "матча", "матчей")
    assert text.startswith(f"{n} ")
    word = text.split(" ", 1)[1]
    if 11 <= n % 100 <= 14 or n % 10 == 0 or n % 10 >= 5:
        assert word == "матчей"
    elif n % 10 == 1:
        assert word == "матч"
    else:
        assert word == "матча"


# ── Звания ────────────────────────────────────────────────────────────────────

_TITLES = [title for _, title in RANK_TITLE_BANDS] + [RANK_TITLE_TOP]


@given(ratings)
def test_rank_title_is_always_known(rating):
    assert rank_title(rating) in _TITLES


@given(ratings, st.floats(min_value=0.0, max_value=1000.0))
def test_rank_never_drops_when_rating_grows(rating, bump):
    assert _TITLES.index(rank_title(rating + bump)) >= _TITLES.index(rank_title(rating))


def test_rank_boundaries_belong_to_the_higher_band():
    for threshold, _ in RANK_TITLE_BANDS:
        below, at = rank_title(threshold - 0.1), rank_title(float(threshold))
        assert _TITLES.index(at) == _TITLES.index(below) + 1


# ── Ось графика рейтинга ──────────────────────────────────────────────────────

@given(st.lists(st.floats(min_value=0.0, max_value=5000.0, allow_nan=False), min_size=0, max_size=60))
def test_axis_contains_all_values_and_the_reference_line(values):
    lo, hi, step = _rating_axis_bounds(values)
    assert step > 0 and lo < hi
    assert lo <= min([*values, 1000.0]) and hi >= max([*values, 1000.0])
    assert lo % step == 0 and hi % step == 0


# ── Стабильный выбор фразы ────────────────────────────────────────────────────

@given(st.integers(min_value=0, max_value=10**9), st.text(max_size=20), st.integers(min_value=1, max_value=100))
def test_pool_index_is_in_range_and_deterministic(seed, salt, size):
    idx = _stable_pool_index(seed, salt, size)
    assert 0 <= idx < size
    assert idx == _stable_pool_index(seed, salt, size)


# ── Подпись диапазона часа ────────────────────────────────────────────────────

@given(st.integers(min_value=0, max_value=23))
def test_hour_range_label_shape(hour):
    start, end = hour_range_label(hour).split("–")
    assert start == f"{hour:02d}:00"
    assert end == f"{(hour + 1) % 24:02d}:00"


# ── Окно голосования ──────────────────────────────────────────────────────────

@given(st.datetimes(min_value=datetime(2026, 1, 1), max_value=datetime(2030, 12, 31, 23, 59)))
def test_voting_is_closed_outside_the_late_december_window(dt):
    if dt.month != 12 or dt.day < 21 or dt.day > 30:
        assert is_voting_open(dt) is False


@given(st.integers(2026, 2035), st.integers(22, 29), st.integers(0, 23), st.integers(0, 59))
def test_voting_is_open_through_the_middle_of_the_window(year, day, hour, minute):
    assert is_voting_open(datetime(year, 12, day, hour, minute)) is True


# ── Имя экрана для счётчика использования ─────────────────────────────────────

@given(st.text(max_size=120))
def test_normalized_action_is_short_and_stable(raw):
    action = normalize_action(raw)
    assert len(action) <= 64
    assert normalize_action(action) == action


@given(st.from_regex(r"[a-z]{3,10}", fullmatch=True), st.integers(min_value=0, max_value=10**9),
       st.integers(min_value=0, max_value=10**9))
def test_ids_do_not_split_one_screen_into_many(name, a, b):
    assert normalize_action(f"{name}_{a}") == normalize_action(f"{name}_{b}") == name
