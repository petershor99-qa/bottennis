"""Тесты статистики/профиля в два уровня (v2.137.0): короткий основной экран +
разделы на кнопках, и проверка лимита Telegram."""
from bot.handlers.profile import (
    STATS_SECTIONS,
    _available_sections,
    _build_stats_screen,
    _build_stats_section,
    show_my_stats_section,
    show_player_profile,
    show_player_stats_section,
)
from bot.keyboards.inline import stats_kb
from bot.services.stats import _compute_player_stats
from bot.utils import get_career_matches
from tests.conftest import _callback
from tests.test_records_screen import _club_with_history

TELEGRAM_LIMIT = 4096


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


async def test_main_stats_screen_is_short_and_has_section_buttons(db):
    ps = await _club_with_history(db, players=6, matches=120)
    text, kb = await _build_stats_screen(db, ps[0])

    assert "Последние матчи" not in text  # переехало в раздел
    assert "Партий сыграно" not in text
    assert len(text) < 1500
    section_cbs = [b.callback_data for b in _buttons(kb) if b.callback_data.startswith("stat_sec_")]
    assert section_cbs
    # разделы идут первыми рядами, по 2 в ряд
    assert kb.inline_keyboard[0][0].callback_data.startswith("stat_sec_")


async def test_every_available_section_opens_with_back_button(db):
    ps = await _club_with_history(db, players=6, matches=120)
    all_matches = await get_career_matches(db, ps[0].id, with_opponents=True)
    s = _compute_player_stats(ps[0], all_matches)
    sections = _available_sections(ps[0], s, all_matches, include_growth=True)
    assert {k for k, _ in sections} >= {"opp", "rating", "game"}
    assert "recent" not in {k for k, _ in sections}   # убран в v2.148.0 (дубль истории)

    for key, title in sections:
        cb = _callback(ps[0].telegram_id, f"stat_sec_{key}")
        await show_my_stats_section(cb, db)
        text = cb.message.edit_text.await_args.args[0]
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        assert title in text, key
        assert kb.inline_keyboard[-1][0].callback_data == "menu_stats"


async def test_recent_section_is_gone(db):
    """«Последние матчи» убраны в v2.148.0 — их заменяет «📜 История матчей»."""
    ps = await _club_with_history(db, players=4, matches=80)
    assert await _build_stats_section(db, ps[0], "recent", personal=True) is None


async def test_unknown_section_alerts(db):
    ps = await _club_with_history(db, players=3, matches=20)
    cb = _callback(ps[0].telegram_id, "stat_sec_nonsense")
    await show_my_stats_section(cb, db)
    cb.answer.assert_awaited_with("Раздел не найден или пока пуст.", show_alert=True)
    cb.message.edit_text.assert_not_awaited()


async def test_section_requires_registration(db):
    cb = _callback(99999, "stat_sec_rating")
    await show_my_stats_section(cb, db)
    cb.answer.assert_awaited_with("Сначала напиши /start", show_alert=True)


async def test_growth_area_only_in_personal_game_section(db):
    """Слабость («Есть над чем поработать», _growth_area) показываем только
    себе — в чужом профиле её быть не должно."""
    from bot.handlers.profile import _growth_area

    ps = await _club_with_history(db, players=6, matches=120)
    checked = 0
    for p in ps:
        all_matches = await get_career_matches(db, p.id, with_opponents=True)
        growth = _growth_area(_compute_player_stats(p, all_matches))
        if not growth:
            continue
        mine = await _build_stats_section(db, p, "game", personal=True)
        theirs = await _build_stats_section(db, p, "game", personal=False)
        assert growth in mine
        assert theirs is None or growth not in theirs
        checked += 1
    assert checked, "в сценарии должен быть игрок со слабой чертой"


async def test_public_profile_has_section_buttons_and_no_growth(db):
    ps = await _club_with_history(db, players=6, matches=120)
    cb = _callback(ps[1].telegram_id, f"player_profile_{ps[0].id}")
    await show_player_profile(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    cbs = [b.callback_data for b in _buttons(kb)]
    assert any(c.startswith(f"pstat_{ps[0].id}_") for c in cbs)
    assert "Последние матчи" not in text


async def test_public_section_opens_and_goes_back_to_profile(db):
    ps = await _club_with_history(db, players=6, matches=120)
    cb = _callback(ps[1].telegram_id, f"pstat_{ps[0].id}_rating")
    await show_player_stats_section(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert ps[0].display_name in text
    assert kb.inline_keyboard[-1][0].callback_data == f"player_profile_{ps[0].id}"


async def test_public_section_bad_data_and_missing_player(db):
    cb = _callback(1, "pstat_abc_recent")
    await show_player_stats_section(cb, db)
    cb.answer.assert_awaited_with("Некорректные данные.", show_alert=True)

    cb = _callback(1, "pstat_999_recent")
    await show_player_stats_section(cb, db)
    cb.answer.assert_awaited_with("Игрок не найден.", show_alert=True)


def test_stats_kb_without_sections_is_unchanged():
    kb = stats_kb()
    assert not any(b.callback_data.startswith("stat_sec_") for b in _buttons(kb))


def test_stats_kb_sections_two_per_row_first():
    kb = stats_kb(STATS_SECTIONS)
    assert [len(r) for r in kb.inline_keyboard[:2]] == [2, 1]   # 3 раздела: пара + одиночный
    assert kb.inline_keyboard[0][0].callback_data == "stat_sec_opp"


async def test_main_and_every_section_stay_under_telegram_limit(db):
    """СТРАХОВКА (v2.137.0): у личной статистики/профиля не было проверки лимита
    Telegram (4096). Проверяем основной экран и каждый раздел на богатой
    истории, запас не менее 15%."""
    ps = await _club_with_history(db, players=7, matches=250)
    for p in ps:
        text, _ = await _build_stats_screen(db, p)
        assert len(text) < TELEGRAM_LIMIT * 0.85, ("main", p.display_name, len(text))
        for key, _title in STATS_SECTIONS:
            for personal in (True, False):
                sec = await _build_stats_section(db, p, key, personal=personal)
                if sec:
                    assert len(sec) < TELEGRAM_LIMIT * 0.85, (key, personal, len(sec))
