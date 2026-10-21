"""
v2.205.1: склонения в сообщениях бота — «2 партии», «3 победы», «21 матч», «22 очка» —
вместо «2 партий», «3 побед», «21 матчей», «22 очков».
Запуск: pytest tests/test_v2205_1.py
"""
import pytest

from bot.handlers.profile import _bold_count
from bot.utils import pluralize_draws, pluralize_losses, pluralize_wins


@pytest.mark.parametrize("n,expected", [
    (1, "1 ничья"), (2, "2 ничьи"), (4, "4 ничьи"), (5, "5 ничьих"),
    (11, "11 ничьих"), (21, "21 ничья"), (22, "22 ничьи"),
])
def test_pluralize_draws(n, expected):
    assert pluralize_draws(n) == expected


def test_bold_count_keeps_number_bold_and_word_declined():
    assert _bold_count(3, pluralize_wins) == "<b>3</b> победы"
    assert _bold_count(1, pluralize_losses) == "<b>1</b> поражение"
    assert _bold_count(12, pluralize_wins) == "<b>12</b> побед"
    assert _bold_count(21, pluralize_draws) == "<b>21</b> ничья"
