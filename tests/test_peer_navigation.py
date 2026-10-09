"""Соседние экраны верхнего уровня — в одном тапе (v2.158.0).

По данным `/usage` «В меню» нажимали 144 раза за 12 дней, и в 125 случаях сразу
после этого уходили на другой верхний экран (после матча в рейтинг — 24 из 29).
Теперь рядом с «В меню» стоит кнопка соседнего экрана; меню остаётся запасным выходом.
"""
from bot.keyboards.inline import history_kb, leaderboard_kb, rematch_kb, stats_kb


def _last_row(kb):
    return [(b.text, b.callback_data) for b in kb.inline_keyboard[-1]]


MENU = ("« В меню", "back_to_menu")


def test_result_screen_has_rating_next_to_menu():
    assert _last_row(rematch_kb(5)) == [("🏆 Рейтинг клуба", "menu_leaderboard"), MENU]


def test_result_screen_after_boss_fight_keeps_rating_button_without_rematch():
    kb = rematch_kb(5, can_rematch=False)
    assert _last_row(kb) == [("🏆 Рейтинг клуба", "menu_leaderboard"), MENU]
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "rematch_5" not in callbacks


def test_result_screen_keeps_share_and_rematch_above():
    kb = rematch_kb(5, share_match_id=9)
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert callbacks[:2] == ["share_card_9", "rematch_5"]


def test_rating_has_profile_next_to_menu():
    assert _last_row(leaderboard_kb([])) == [("👤 Мой профиль", "menu_stats"), MENU]


def test_profile_has_rating_next_to_menu():
    assert _last_row(stats_kb()) == [("🏆 Рейтинг клуба", "menu_leaderboard"), MENU]
    assert _last_row(stats_kb([])) == [("🏆 Рейтинг клуба", "menu_leaderboard"), MENU]


def test_history_has_rating_next_to_menu_and_keeps_paging():
    kb = history_kb(1, 3)
    assert _last_row(kb) == [("🏆 Рейтинг клуба", "menu_leaderboard"), MENU]
    first = [b.callback_data for b in kb.inline_keyboard[0]]
    assert first == ["history_0", "history_2"]


def test_history_single_page_has_only_the_pair_row():
    assert len(history_kb(0, 1).inline_keyboard) == 1


def test_no_screen_loses_its_menu_exit():
    for kb in (rematch_kb(1), leaderboard_kb([]), stats_kb(), history_kb(0, 2)):
        assert MENU in _last_row(kb)
