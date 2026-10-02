"""«Рейтинг клуба» / «Мой профиль» (v2.156.0): переименование без расхождений и экран
«Все матчи клуба»."""
import re
from datetime import datetime, timedelta
from pathlib import Path

import bot.utils as utils
from bot.handlers.leaderboard import (
    CLUB_MATCHES_PAGE_SIZE,
    _build_leaderboard_screen,
    show_club_matches,
)
from bot.keyboards.inline import club_matches_kb, main_menu_kb, main_reply_kb
from tests.conftest import _callback, _completed, _player

BOT_DIR = Path(__file__).resolve().parent.parent / "bot"


def _buttons(kb):
    return [b for row in kb.inline_keyboard for b in row]


# ── Переименование ─────────────────────────────────────────────────────────────

def test_main_menu_uses_new_names():
    texts = [b.text for b in _buttons(main_menu_kb())]
    assert "🏆 Рейтинг клуба" in texts and "👤 Мой профиль" in texts
    assert "📊 Рейтинг" not in texts and "📈 Статистика" not in texts


def test_reply_keyboard_is_shortened_and_matches_constants():
    row = main_reply_kb().keyboard[0]
    assert [b.text for b in row][1:] == ["🏆 Клуб", "👤 Профиль"]
    assert utils.REPLY_KB_LEADERBOARD == "🏆 Клуб" and utils.REPLY_KB_PROFILE == "👤 Профиль"


def test_old_reply_keyboard_texts_still_accepted():
    """У игрока может стоять прежняя нижняя клавиатура — её кнопки не должны замолчать."""
    assert {"🏆 Клуб", "📊 Рейтинг"} == utils.REPLY_KB_LEADERBOARD_ALL
    assert {"👤 Профиль", "📈 Статистика"} == utils.REPLY_KB_PROFILE_ALL


def test_no_old_screen_names_left_in_bot_sources():
    """Страховка от расхождений названий: старые подписи экранов встречаются только в
    определении принимаемых старых текстов нижней клавиатуры (bot/utils.py)."""
    forbidden = ["« К статистике", "Рейтинг игроков", "Статистика — ", "К рейтингу\"", "К рейтингу'"]
    legacy_ok = {"📊 Рейтинг", "📈 Статистика"}
    problems = []
    for path in BOT_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                problems.append((path.name, needle))
        for old in legacy_ok:
            if old in text and path.name != "utils.py":
                problems.append((path.name, old))
    assert not problems, problems


def test_back_buttons_use_new_names():
    from bot.keyboards.inline import (
        back_to_leaderboard_kb,
        back_to_stats_kb,
        stats_section_kb,
    )

    assert _buttons(back_to_stats_kb())[0].text == "« В мой профиль"
    assert _buttons(stats_section_kb())[0].text == "« В мой профиль"
    assert _buttons(back_to_leaderboard_kb())[0].text == "« К рейтингу клуба"


async def test_leaderboard_title_is_club_rating(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()
    text, kb = await _build_leaderboard_screen(db, 1)
    assert "🏆 <b>Рейтинг клуба:</b>" in text
    assert "📋 Все матчи клуба" in [b.text for b in _buttons(kb)]


# ── Все матчи клуба ────────────────────────────────────────────────────────────

async def _club(db, n: int, name_len: int = 6):
    p1, p2 = _player(1, "А" * name_len), _player(2, "Б" * name_len)
    db.add_all([p1, p2])
    await db.flush()
    base = datetime(2026, 6, 1, 9, 0, 0)
    for i in range(n):
        m = _completed(p1, p2, p1.id if i % 2 == 0 else p2.id, 10.0, base + timedelta(hours=i))
        m.sets_data = [{"w": 11, "l": 7}, {"w": 9, "l": 11}, {"w": 11, "l": 5}]
        db.add(m)
    await db.commit()
    return p1, p2


async def test_empty_club_shows_placeholder(db):
    cb = _callback(1, "club_matches_0")
    await show_club_matches(cb, db)
    assert "Матчей ещё не было" in cb.message.edit_text.await_args.args[0]


async def test_first_page_newest_first_with_score_date_and_bold_winner(db):
    await _club(db, 3)
    cb = _callback(1, "club_matches_0")
    await show_club_matches(cb, db)
    text = cb.message.edit_text.await_args.args[0]
    assert "Все матчи клуба" in text and "стр. 1/1, всего 3" in text
    lines = text.split("\n")[2:]
    assert len(lines) == 3
    # новые сверху: последний (i=2, 11:00 UTC = 14:00 МСК) первым
    assert lines[0].startswith("01.06")
    assert "11:7, 9:11, 11:5" in lines[0]
    assert "<b>" in lines[0]                       # победитель жирным
    # i=2 (чётный) выиграл p1 → «<b>ААА…</b> vs ББ…», i=1 выиграл p2
    assert lines[0].split("  ")[1].startswith("<b>А")
    assert " vs <b>Б" in lines[1]


async def test_pagination_pages_and_clamping(db):
    await _club(db, CLUB_MATCHES_PAGE_SIZE + 5)
    cb0 = _callback(1, "club_matches_0")
    await show_club_matches(cb0, db)
    t0 = cb0.message.edit_text.await_args.args[0]
    assert "стр. 1/2" in t0 and len(t0.split("\n")[2:]) == CLUB_MATCHES_PAGE_SIZE
    kb0 = cb0.message.edit_text.await_args.kwargs["reply_markup"]
    assert "club_matches_1" in [b.callback_data for b in _buttons(kb0)]

    cb1 = _callback(1, "club_matches_1")
    await show_club_matches(cb1, db)
    t1 = cb1.message.edit_text.await_args.args[0]
    assert "стр. 2/2" in t1 and len(t1.split("\n")[2:]) == 5

    cb9 = _callback(1, "club_matches_99")             # за пределом — последняя страница
    await show_club_matches(cb9, db)
    assert "стр. 2/2" in cb9.message.edit_text.await_args.args[0]


async def test_invalid_page_data_alerts(db):
    cb = _callback(1, "club_matches_abc")
    await show_club_matches(cb, db)
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    cb.message.edit_text.assert_not_awaited()


async def test_page_fits_telegram_limit_with_long_names(db):
    await _club(db, CLUB_MATCHES_PAGE_SIZE, name_len=30)
    cb = _callback(1, "club_matches_0")
    await show_club_matches(cb, db)
    assert len(cb.message.edit_text.await_args.args[0]) < 4096 * 0.7


def test_club_matches_kb_navigation_and_back():
    first = [b.callback_data for b in _buttons(club_matches_kb(0, 3))]
    assert first == ["club_matches_1", "menu_leaderboard"]
    middle = [b.callback_data for b in _buttons(club_matches_kb(1, 3))]
    assert middle == ["club_matches_0", "club_matches_2", "menu_leaderboard"]
    last = [b.callback_data for b in _buttons(club_matches_kb(2, 3))]
    assert last == ["club_matches_1", "menu_leaderboard"]
    assert _buttons(club_matches_kb(0, 1))[0].text == "« К рейтингу клуба"


def test_help_mentions_new_names_and_club_matches():
    from bot.handlers.start import _help_section_text

    screens = _help_section_text("screens")
    assert "Рейтинг клуба" in screens and "Мой профиль" in screens and "Все матчи клуба" in screens
    play = _help_section_text("play")
    assert "🏆 Клуб" in play and "👤 Профиль" in play
    assert re.search(r"📊 Рейтинг|📈 Статистика", screens + play) is None
