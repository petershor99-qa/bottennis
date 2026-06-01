"""
Тесты логики «Матч дня» и форматирования счёта.
Запуск: pytest tests/test_match_of_day.py
"""
from datetime import datetime
from types import SimpleNamespace

from bot.utils import (
    DRAMA_THRESHOLD,
    match_drama_reason,
    match_drama_score,
    match_score_challenger_first,
    pick_match_of_day,
)


def make_match(sets, winner_id=1, challenger_id=1, challenged_id=2,
               rating_change=10.0, completed_at=None):
    """Лёгкая заглушка матча (атрибуты, которых хватает функциям драмы)."""
    return SimpleNamespace(
        sets_data=sets,
        winner_id=winner_id,
        challenger_id=challenger_id,
        challenged_id=challenged_id,
        rating_change=rating_change,
        completed_at=completed_at or datetime(2026, 5, 29, 12, 0, 0),
    )


# ── match_drama_score ─────────────────────────────────────────────────────────

def test_empty_sets_zero_drama():
    assert match_drama_score(make_match([])) == 0.0


def test_single_set_blowout_is_low():
    """Сухой 1-партийный разгром — минимальная драма, ниже порога."""
    m = make_match([{"w": 11, "l": 2}], rating_change=10.0)
    assert match_drama_score(m) < DRAMA_THRESHOLD


def test_five_set_thriller_is_high():
    """5 партий с дьюсами и концовкой впритык — высокая драма."""
    sets = [
        {"w": 11, "l": 9}, {"w": 9, "l": 11}, {"w": 11, "l": 8},
        {"w": 7, "l": 11}, {"w": 13, "l": 11},
    ]
    m = make_match(sets, winner_id=1)
    assert match_drama_score(m) >= DRAMA_THRESHOLD


def test_deuce_adds_drama():
    """Партия за 11 (дьюс) добавляет балл."""
    base = make_match([{"w": 11, "l": 5}, {"w": 11, "l": 5}, {"w": 11, "l": 5}])
    deuce = make_match([{"w": 11, "l": 5}, {"w": 11, "l": 5}, {"w": 13, "l": 11}])
    assert match_drama_score(deuce) > match_drama_score(base)


def test_comeback_adds_drama():
    """Победитель проиграл стартовую партию (камбэк) — больше драмы."""
    no_cb = make_match([{"w": 11, "l": 6}, {"w": 11, "l": 6}, {"w": 6, "l": 11}], winner_id=1)
    cb = make_match([{"w": 6, "l": 11}, {"w": 11, "l": 6}, {"w": 11, "l": 6}], winner_id=1)
    assert match_drama_score(cb) > match_drama_score(no_cb)


def test_draw_has_no_comeback_bonus():
    """У ничьей (winner_id=None) не начисляется камбэк-бонус, не падает."""
    m = make_match([{"w": 6, "l": 11}, {"w": 11, "l": 6}], winner_id=None)
    assert match_drama_score(m) >= 0


# ── pick_match_of_day ─────────────────────────────────────────────────────────

def test_pick_none_when_all_trivial():
    """Если все матчи — сухие разгромы, матча дня нет."""
    matches = [
        make_match([{"w": 11, "l": 2}], rating_change=5.0),
        make_match([{"w": 11, "l": 3}, {"w": 11, "l": 1}], rating_change=5.0),
    ]
    assert pick_match_of_day(matches) is None


def test_pick_highest_drama():
    """Выбирается самый драматичный матч."""
    boring = make_match([{"w": 11, "l": 2}], rating_change=5.0)
    thriller = make_match(
        [{"w": 11, "l": 9}, {"w": 9, "l": 11}, {"w": 13, "l": 11}],
        winner_id=1, rating_change=20.0,
    )
    chosen = pick_match_of_day([boring, thriller])
    assert chosen is thriller


def test_pick_empty_list():
    assert pick_match_of_day([]) is None


# ── match_drama_reason ────────────────────────────────────────────────────────

def test_reason_marathon():
    sets = [{"w": 11, "l": 9}] * 5
    assert "марафон" in match_drama_reason(make_match(sets, winner_id=1)).lower()


def test_reason_comeback():
    sets = [{"w": 6, "l": 11}, {"w": 11, "l": 6}, {"w": 11, "l": 8}]
    assert "камбэк" in match_drama_reason(make_match(sets, winner_id=1)).lower()


def test_reason_draw():
    sets = [{"w": 11, "l": 6}, {"w": 6, "l": 11}]
    assert "ничья" in match_drama_reason(make_match(sets, winner_id=None)).lower()


def test_reason_capitalized():
    """Подпись начинается с заглавной буквы."""
    r = match_drama_reason(make_match([{"w": 11, "l": 9}] * 5, winner_id=1))
    assert r[0].isupper()


# ── match_score_challenger_first ──────────────────────────────────────────────

def test_score_winner_is_challenger():
    """Победил challenger — счёт без инверсии."""
    m = make_match([{"w": 11, "l": 7}, {"w": 11, "l": 5}], winner_id=1, challenger_id=1)
    assert match_score_challenger_first(m) == "11:7, 11:5"


def test_score_winner_is_challenged_inverts():
    """Победил challenged — счёт инвертируется в перспективу challenger."""
    # winner_id=2 (challenged), sets хранятся в перспективе победителя
    m = make_match([{"w": 11, "l": 7}, {"w": 11, "l": 5}],
                   winner_id=2, challenger_id=1, challenged_id=2)
    assert match_score_challenger_first(m) == "7:11, 5:11"


def test_score_draw_challenger_perspective():
    """Ничья — счёт уже в перспективе challenger, без инверсии."""
    m = make_match([{"w": 11, "l": 9}, {"w": 9, "l": 11}], winner_id=None, challenger_id=1)
    assert match_score_challenger_first(m) == "11:9, 9:11"


def test_score_empty():
    assert match_score_challenger_first(make_match([])) == ""
