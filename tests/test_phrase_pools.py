"""Расширенные пулы фраз (v2.144.0): прогноз на экране матча и репортаж."""
import pytest

from bot.utils import (
    BLOWOUT_PHRASES,
    EVEN_PHRASES,
    FAVORITE_PHRASES,
    PLAIN_WIN_PHRASES,
    UNDERDOG_PHRASES,
    match_phrase,
)

POOLS = {
    "favorite": FAVORITE_PHRASES,
    "even": EVEN_PHRASES,
    "underdog": UNDERDOG_PHRASES,
    "blowout": BLOWOUT_PHRASES,
    "plain_win": PLAIN_WIN_PHRASES,
}


@pytest.mark.parametrize("name", POOLS)
def test_pool_has_no_duplicates(name):
    pool = POOLS[name]
    assert len(pool) == len(set(pool))


@pytest.mark.parametrize("name", POOLS)
def test_pool_is_grown(name):
    # было 13–15 фраз в пуле, после v2.144.0 — минимум 20
    assert len(POOLS[name]) >= 20


@pytest.mark.parametrize("name", POOLS)
def test_phrases_are_safe_for_html_parse_mode(name):
    for phrase in POOLS[name]:
        assert "<" not in phrase and ">" not in phrase and "&" not in phrase, phrase
        assert phrase.strip() == phrase and phrase


@pytest.mark.parametrize("name", POOLS)
def test_every_pool_has_phenibut_reference(name):
    assert any("фенибут" in p.lower() for p in POOLS[name])


@pytest.mark.parametrize(
    ("chance", "pool"),
    [(80, FAVORITE_PHRASES), (50, EVEN_PHRASES), (20, UNDERDOG_PHRASES)],
)
def test_match_phrase_reaches_every_phrase_of_its_pool(chance, pool):
    reached = {match_phrase(chance, i) for i in range(len(pool))}
    assert reached == set(pool)
