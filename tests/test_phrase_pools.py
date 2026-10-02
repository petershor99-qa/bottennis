"""Расширенные пулы фраз (v2.144.0): прогноз на экране матча и репортаж."""
import pytest

from bot.utils import (
    BLOWOUT_PHRASES,
    DRAW_PHRASES,
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
    "draw": DRAW_PHRASES,
}


@pytest.mark.parametrize("name", POOLS)
def test_pool_has_no_duplicates(name):
    pool = POOLS[name]
    assert len(pool) == len(set(pool))


@pytest.mark.parametrize("name", [n for n in POOLS if n != "draw"])
def test_pool_is_grown(name):
    # было 13–15 фраз в пуле, после v2.144.0 — минимум 20
    assert len(POOLS[name]) >= 20


@pytest.mark.parametrize("name", POOLS)
def test_phrases_are_safe_for_html_parse_mode(name):
    for phrase in POOLS[name]:
        assert "<" not in phrase and ">" not in phrase and "&" not in phrase, phrase
        assert phrase.strip() == phrase and phrase


@pytest.mark.parametrize("name", [n for n in POOLS if n != "draw"])
def test_every_pool_has_phenibut_reference(name):
    assert any("фенибут" in p.lower() for p in POOLS[name])


def test_draw_blowout_plain_win_got_new_phrases():
    assert len(DRAW_PHRASES) == 8
    assert any("калькулятор рейтинга" in p for p in DRAW_PHRASES)
    assert any("демонстрационный показ" in p for p in BLOWOUT_PHRASES)
    assert any("рабочий вторник" in p for p in PLAIN_WIN_PHRASES)


@pytest.mark.parametrize(
    ("chance", "pool"),
    [(80, FAVORITE_PHRASES), (50, EVEN_PHRASES), (20, UNDERDOG_PHRASES)],
)
def test_match_phrase_reaches_every_phrase_of_its_pool(chance, pool):
    reached = {match_phrase(chance, i) for i in range(len(pool))}
    assert reached == set(pool)


# ── Пулы репортажа и «прошлых встреч» (v2.149.0) ───────────────────────────────

from bot.utils import (  # noqa: E402
    CLOSE_DECIDER_FRAGMENTS,
    COMEBACK_OPENERS,
    DEUCE_FRAGMENTS,
    H2H_DRAW_PHRASES,
    H2H_REVENGE_PHRASES,
    H2H_STREAK_PHRASES,
    MARATHON_FRAGMENTS,
    MID_DEUCE_FRAGMENTS,
    SOFT_COMEBACK_OPENERS,
    UPSET_FRAGMENT_TEMPLATES,
)

REPORT_POOLS = {
    "soft_comeback": SOFT_COMEBACK_OPENERS,
    "close_decider": CLOSE_DECIDER_FRAGMENTS,
    "mid_deuce": MID_DEUCE_FRAGMENTS,
    "deuce": DEUCE_FRAGMENTS,
    "comeback": COMEBACK_OPENERS,
    "marathon": MARATHON_FRAGMENTS,
    "upset": UPSET_FRAGMENT_TEMPLATES,
    "h2h_revenge": H2H_REVENGE_PHRASES,
    "h2h_streak": H2H_STREAK_PHRASES,
    "h2h_draw": H2H_DRAW_PHRASES,
}


@pytest.mark.parametrize("name", REPORT_POOLS)
def test_report_pool_has_no_duplicates_and_is_html_safe(name):
    pool = REPORT_POOLS[name]
    assert len(pool) == len(set(pool))
    for phrase in pool:
        assert "<" not in phrase and ">" not in phrase and "&" not in phrase, phrase


@pytest.mark.parametrize("name", REPORT_POOLS)
def test_report_pool_templates_format_without_errors(name):
    """Шаблоны с подстановками ({name}/{n}/{ord}/{ord_nom}/{delta}) не должны падать
    на .format() — лишняя фигурная скобка в новой фразе сломала бы репортаж."""
    for phrase in REPORT_POOLS[name]:
        phrase.format(name="Имя", n=5, ord="пятой", ord_nom="пятая", delta=21.5)


def test_report_pools_got_the_new_phrases():
    assert any("ловушки" in p for p in SOFT_COMEBACK_OPENERS)
    assert any("аптечек" in p for p in CLOSE_DECIDER_FRAGMENTS)
    assert any("чекпойнт" in p for p in DEUCE_FRAGMENTS)
    assert any("перезапуск" in p for p in COMEBACK_OPENERS)
    assert any("созвоны" in p for p in MARATHON_FRAGMENTS)
    assert any("кофемашин" in p for p in UPSET_FRAGMENT_TEMPLATES)
    assert any("Месть" in p for p in H2H_REVENGE_PHRASES)
    assert any("электричек" in p for p in H2H_STREAK_PHRASES)
    assert any("кредитом" in p for p in H2H_DRAW_PHRASES)


def test_placeholders_kept_in_templates():
    assert all("{name}" in p for p in COMEBACK_OPENERS[-5:])
    assert all("{delta}" in p for p in UPSET_FRAGMENT_TEMPLATES[-5:])
