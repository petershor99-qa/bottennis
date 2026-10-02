"""Пулы фраз вынесены в bot/phrases.py (v2.150.0) — поведение не меняется."""
import bot.phrases as phrases
import bot.utils as utils

POOL_NAMES = [
    "BLOWOUT_PHRASES", "PLAIN_WIN_PHRASES", "DRAW_PHRASES", "COMEBACK_OPENERS", "MARATHON_FRAGMENTS",
    "_ORDINAL_FEM", "_ORDINAL_FEM_NOM", "DEUCE_FRAGMENTS", "UPSET_FRAGMENT_TEMPLATES",
    "SOFT_COMEBACK_OPENERS", "CLOSE_DECIDER_FRAGMENTS", "MID_DEUCE_FRAGMENTS",
    "H2H_REVENGE_PHRASES", "H2H_STREAK_PHRASES", "H2H_DRAW_PHRASES",
    "CHALLENGE_BUTTON_LABELS", "CHALLENGE_HEADER_GREETINGS",
    "FAVORITE_PHRASES", "EVEN_PHRASES", "UNDERDOG_PHRASES",
]


def test_every_pool_is_reexported_from_utils_as_the_same_object():
    for name in POOL_NAMES:
        assert getattr(utils, name) is getattr(phrases, name), name


def test_phrases_module_is_pure_data():
    """В bot/phrases.py только данные: ни функций, ни классов (логика — в utils)."""
    public = [n for n in vars(phrases) if not n.startswith("__")]
    egg_names = [n for n in public if n.startswith("EGG_")]     # пасхалки (v2.153.0)
    assert egg_names, "пулы пасхалок должны жить в bot/phrases.py"
    assert sorted(public) == sorted(POOL_NAMES + egg_names)
    for name in POOL_NAMES + egg_names:
        assert isinstance(getattr(phrases, name), (list, dict)), name


def test_pools_still_used_by_selection_logic():
    assert utils.match_phrase(80, 0) in phrases.FAVORITE_PHRASES
    assert utils.match_phrase(50, 0) in phrases.EVEN_PHRASES
    assert utils.match_phrase(10, 0) in phrases.UNDERDOG_PHRASES
    assert utils.random_challenge_button_label() in phrases.CHALLENGE_BUTTON_LABELS
