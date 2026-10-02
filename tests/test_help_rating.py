"""Справка «как считается рейтинг» в /help (v2.146.0)."""
from unittest.mock import AsyncMock

from bot.handlers.start import RATING_HELP_TEXT, _help_section_text, cmd_help
from bot.services.rating import SHORT_MATCH_MULT
from bot.utils import NEWCOMER_THRESHOLD


async def _help_text() -> str:
    msg = AsyncMock()
    await cmd_help(msg)
    return msg.answer.await_args.args[0]


async def test_help_rating_section_is_the_explanation():
    text = _help_section_text("rating")
    assert text == RATING_HELP_TEXT
    assert "модифицированный ELO" in text


async def test_help_main_screen_is_short_and_fits_telegram():
    assert len(await _help_text()) < 300


def test_rating_text_matches_formula_constants():
    """Цифры в справке не должны разойтись с формулой."""
    assert f"меньше {NEWCOMER_THRESHOLD} матчей" in RATING_HELP_TEXT
    assert f"{round((1 - SHORT_MATCH_MULT) * 100)}%" in RATING_HELP_TEXT   # 25%
    assert "×1,2" in RATING_HELP_TEXT
    assert "1000" in RATING_HELP_TEXT and "900" in RATING_HELP_TEXT
    assert "×2" in RATING_HELP_TEXT   # босс-файт


def test_rating_text_safe_for_html_parse_mode():
    assert "<" not in RATING_HELP_TEXT.replace("<b>", "").replace("</b>", "")
    assert "&" not in RATING_HELP_TEXT
