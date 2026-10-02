"""Экраны и команды v2.148.0: переименования кнопок, легенда рейтинга, «Сегодня в клубе»
со всеми матчами, справка в меню, /name, ввод счёта без кнопки, счётчик нижней клавиатуры."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

import bot.scheduler as sched
from bot.db.models import Match, MatchStatus, Player
from bot.handlers.challenge import _build_challenge_screen
from bot.handlers.leaderboard import (
    LEADERBOARD_LEGEND,
    TODAY_LOG_MAX,
    _build_leaderboard_screen,
    show_today_stats,
)
from bot.handlers.match_result import fsm_reset_notice, handle_direct_score
from bot.handlers.profile import (
    _available_sections,
    _build_stats_screen,
    _build_stats_section,
    show_player_profile,
)
from bot.handlers.start import (
    NAME_MAX_LEN,
    cmd_help,
    cmd_name,
    cmd_start,
    show_help_from_menu,
)
from bot.keyboards.inline import (
    busy_with_match_kb,
    h2h_kb,
    leaderboard_kb,
    main_menu_kb,
    player_profile_kb,
    stats_kb,
)
from bot.middleware import UsageMiddleware
from bot.services.stats import _compute_player_stats
from bot.utils import CHALLENGE_BUTTON_LABELS, get_career_matches, match_log_line
from tests.conftest import _callback, _completed, _player, _state
from tests.test_records_screen import _club_with_history


def _flat(kb):
    return [b for row in kb.inline_keyboard for b in row]


def _texts(kb):
    return [b.text for b in _flat(kb)]


def _cbs(kb):
    return [b.callback_data for b in _flat(kb)]


# ── Клавиатуры: названия и состав ──────────────────────────────────────────────

def test_main_menu_has_help_and_no_report_buttons():
    kb = main_menu_kb()
    assert "menu_help" in _cbs(kb)
    assert "❓ Справка" in _texts(kb)
    assert not any((c or "").startswith("report_") for c in _cbs(kb))


def test_stats_kb_new_names_and_no_duplicate_recent():
    kb = stats_kb([("opp", "🆚 С кем играю"), ("rating", "📈 Рейтинг в цифрах")])
    texts = _texts(kb)
    for expected in (
        "📊 График рейтинга", "🔥 Карта активности", "🕸 Радар стиля",
        "🎬 Моя карьера", "🏅 Достижения", "📜 История матчей", "📅 Сегодня в клубе",
    ):
        assert expected in texts
    assert not any("Последние матчи" in t for t in texts)
    # «История матчей» стоит выше «Сегодня в клубе» — самый открываемый обзор матчей
    assert _cbs(kb).index("history_0") < _cbs(kb).index("menu_today")


def test_leaderboard_kb_new_button_names():
    texts = _texts(leaderboard_kb([]))
    assert {"🏆 Рекорды клуба", "⚔️ Кто кого бьёт", "🌡 Кто в форме", "🏛 Зал славы"} <= set(texts)


def test_profile_kb_other_player_new_names():
    kb = player_profile_kb(5, viewer_id=1, can_challenge=True)
    texts = _texts(kb)
    for expected in (
        "🎲 Сколько очков за матч", "🆚 Личные встречи", "🆚 Сравнить стили",
        "📊 График рейтинга", "🕸 Радар стиля", "🏅 Достижения", "📜 История матчей",
    ):
        assert expected in texts
    assert "🎲 Что если?" not in texts and "📜 Вся история матчей" not in texts
    # «Что если» остаётся и когда вызвать нельзя
    assert "🎲 Сколько очков за матч" in _texts(player_profile_kb(5, viewer_id=1, can_challenge=False))


def test_h2h_kb_compare_button_renamed():
    assert "🆚 Сравнить стили" in _texts(h2h_kb(5))


def test_busy_kb_only_cancel_and_menu():
    cbs = _cbs(busy_with_match_kb(7))
    assert cbs == ["cancel_match_7", "back_to_menu"]


# ── Статистика / профиль ───────────────────────────────────────────────────────

async def test_personal_and_other_section_titles(db):
    ps = await _club_with_history(db, players=5, matches=100)
    matches = await get_career_matches(db, ps[0].id, with_opponents=True)
    s = _compute_player_stats(ps[0], matches)
    mine = dict(_available_sections(ps[0], s, matches, include_growth=True))
    other = dict(_available_sections(ps[0], s, matches, include_growth=False))
    assert mine["opp"] == "🆚 С кем играю"
    assert other["opp"] == "🆚 С кем играет"   # чужой профиль — от третьего лица
    assert mine["rating"] == other["rating"] == "📈 Рейтинг в цифрах"
    assert mine["game"] == "🎮 Привычки и советы"
    head = await _build_stats_section(db, ps[0], "opp", personal=False)
    assert "С кем играет" in head.split("\n")[0]


async def test_winrate_line_and_legend_index_hint_on_stats_and_profile(db):
    ps = await _club_with_history(db, players=5, matches=100)
    text, _ = await _build_stats_screen(db, ps[0])
    assert "Винрейт: матчи" in text and "партии" in text
    assert "📊 Матчи:" not in text
    assert "ачивки, рекорды, боссфайты" in text

    cb = _callback(ps[1].telegram_id, f"player_profile_{ps[0].id}")
    await show_player_profile(cb, db)
    profile = cb.message.edit_text.await_args.args[0]
    assert "Винрейт: матчи" in profile and "партии" in profile
    assert "ачивки, рекорды, боссфайты" in profile


# ── Рейтинг: легенда ───────────────────────────────────────────────────────────

async def test_leaderboard_has_badge_legend(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()
    text, _ = await _build_leaderboard_screen(db, 1)
    assert LEADERBOARD_LEGEND in text
    for badge in ("👑", "🗡", "🌟", "🔥", "❄️", "▲▼"):
        assert badge in LEADERBOARD_LEGEND


# ── Сегодня в клубе ────────────────────────────────────────────────────────────

def test_match_log_line_winner_bold_draw_and_escaping():
    ch = SimpleNamespace(challenger_id=1, challenged_id=2, winner_id=1,
                         sets_data=[{"w": 11, "l": 7}, {"w": 9, "l": 11}, {"w": 11, "l": 5}])
    assert match_log_line(ch, "Alice", "Bob").startswith("<b>Alice</b> vs Bob")
    assert "11:7" in match_log_line(ch, "Alice", "Bob")
    ch.winner_id = 2
    assert match_log_line(ch, "Alice", "Bob").startswith("Alice vs <b>Bob</b>")
    ch.winner_id = None
    assert "🤝" in match_log_line(ch, "Alice", "Bob")
    assert "&lt;i&gt;" in match_log_line(ch, "<i>", "Bob")


async def test_today_screen_lists_all_matches_with_scores(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    m1 = _completed(p1, p2, p1.id, 10.0, now - timedelta(minutes=3))
    m1.sets_data = [{"w": 11, "l": 7}, {"w": 11, "l": 5}]
    m2 = _completed(p3, p1, p1.id, 10.0, now - timedelta(minutes=1))
    m2.sets_data = [{"w": 11, "l": 9}]
    db.add_all([m1, m2])
    await db.commit()

    cb = _callback(1, "menu_today")
    await show_today_stats(cb, db)
    text = cb.message.edit_text.await_args.args[0]
    assert "Сегодня в клубе" in text
    assert "📋 <b>Все матчи:</b>" in text
    assert "<b>Alice</b> vs Bob" in text and "11:7, 11:5" in text
    # по порядку игры: сначала более ранний матч
    assert text.index("Alice</b> vs Bob") < text.index("vs <b>Alice</b>")


async def test_today_screen_caps_log_and_fits_telegram_limit(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    p1, p2 = _player(1, "А" * 30), _player(2, "Б" * 30)
    db.add_all([p1, p2])
    await db.flush()
    for i in range(TODAY_LOG_MAX + 25):
        m = _completed(p1, p2, p1.id, 10.0, now - timedelta(seconds=i))
        m.sets_data = [{"w": 11, "l": 7}, {"w": 9, "l": 11}, {"w": 11, "l": 8}]
        db.add(m)
    await db.commit()

    cb = _callback(1, "menu_today")
    await show_today_stats(cb, db)
    text = cb.message.edit_text.await_args.args[0]
    assert f"из {TODAY_LOG_MAX + 25})" in text         # показана только часть
    assert text.count(" vs ") <= TODAY_LOG_MAX
    assert len(text) < 4096 * 0.9


async def test_today_screen_empty_title(db):
    db.add(_player(1, "Alice"))
    await db.commit()
    cb = _callback(1, "menu_today")
    await show_today_stats(cb, db)
    assert "Сегодня в клубе" in cb.message.edit_text.await_args.args[0]


# ── Справка в меню и /name ─────────────────────────────────────────────────────

async def test_help_from_menu_shows_same_text_with_notifications_button():
    cb = _callback(1, "menu_help")
    await show_help_from_menu(cb)
    text = cb.message.edit_text.await_args.args[0]
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert "Справка bottennis" in text
    assert "menu_notifications" in _cbs(kb)

    msg = AsyncMock()
    await cmd_help(msg)
    assert msg.answer.await_args.args[0] == text


async def test_help_sections_mention_name_command_and_new_screen_names():
    from bot.handlers.start import _help_section_text

    assert "/name — сменить имя в боте (например: /name Пётр)" in _help_section_text("commands")
    screens = _help_section_text("screens")
    assert "С кем сыграть?" in screens and "Рекомендации" not in screens
    assert "Сегодня в клубе" in screens


def _name_msg(user_id: int):
    m = AsyncMock()
    m.from_user = SimpleNamespace(id=user_id, username="u", full_name="U")
    m.answer = AsyncMock()
    return m


async def test_name_changes_display_name(db):
    db.add(_player(1, "Старое"))
    await db.commit()
    msg = _name_msg(1)
    await cmd_name(msg, SimpleNamespace(args="  Пётр   Иванов "), db)
    fresh = (await db.execute(select(Player).where(Player.telegram_id == 1))).scalar_one()
    assert fresh.display_name == "Пётр Иванов"           # пробелы схлопнуты
    assert msg.answer.await_args.args[0] == "✅ Имя изменено: <b>Пётр Иванов</b>"


async def test_name_without_args_shows_usage_and_keeps_name(db):
    db.add(_player(1, "Старое"))
    await db.commit()
    msg = _name_msg(1)
    await cmd_name(msg, SimpleNamespace(args=None), db)
    assert "/name Пётр" in msg.answer.await_args.args[0]
    fresh = (await db.execute(select(Player).where(Player.telegram_id == 1))).scalar_one()
    assert fresh.display_name == "Старое"


async def test_name_too_long_rejected(db):
    db.add(_player(1, "Старое"))
    await db.commit()
    msg = _name_msg(1)
    await cmd_name(msg, SimpleNamespace(args="Я" * (NAME_MAX_LEN + 1)), db)
    assert str(NAME_MAX_LEN) in msg.answer.await_args.args[0]
    fresh = (await db.execute(select(Player).where(Player.telegram_id == 1))).scalar_one()
    assert fresh.display_name == "Старое"
    # ровно NAME_MAX_LEN — можно
    await cmd_name(_name_msg(1), SimpleNamespace(args="Я" * NAME_MAX_LEN), db)
    fresh = (await db.execute(select(Player).where(Player.telegram_id == 1))).scalar_one()
    assert fresh.display_name == "Я" * NAME_MAX_LEN


async def test_name_html_is_escaped_in_reply(db):
    db.add(_player(1, "Старое"))
    await db.commit()
    msg = _name_msg(1)
    await cmd_name(msg, SimpleNamespace(args="<b>Хак</b>"), db)
    assert "&lt;b&gt;" in msg.answer.await_args.args[0]


async def test_name_must_be_unique_case_insensitive(db):
    db.add_all([_player(1, "Старое"), _player(2, "Пётр")])
    await db.commit()
    msg = _name_msg(1)
    await cmd_name(msg, SimpleNamespace(args="пётр"), db)
    assert "уже занято" in msg.answer.await_args.args[0]
    fresh = (await db.execute(select(Player).where(Player.telegram_id == 1))).scalar_one()
    assert fresh.display_name == "Старое"
    # а свой регистр поменять можно
    msg2 = _name_msg(2)
    await cmd_name(msg2, SimpleNamespace(args="ПЁТР"), db)
    fresh2 = (await db.execute(select(Player).where(Player.telegram_id == 2))).scalar_one()
    assert fresh2.display_name == "ПЁТР"


async def test_old_recent_buttons_get_moved_notice(db):
    from bot.handlers.profile import (
        MOVED_RECENT_NOTICE,
        show_my_stats_section,
        show_player_stats_section,
    )

    ps = await _club_with_history(db, players=3, matches=20)
    cb = _callback(ps[0].telegram_id, "stat_sec_recent")
    await show_my_stats_section(cb, db)
    assert cb.answer.await_args.args[0] == MOVED_RECENT_NOTICE
    cb2 = _callback(ps[1].telegram_id, f"pstat_{ps[0].id}_recent")
    await show_player_stats_section(cb2, db)
    assert cb2.answer.await_args.args[0] == MOVED_RECENT_NOTICE
    assert "История матчей" in MOVED_RECENT_NOTICE


async def test_name_requires_registration(db):
    msg = _name_msg(99)
    await cmd_name(msg, SimpleNamespace(args="Пётр"), db)
    assert "/start" in msg.answer.await_args.args[0]


# ── Ввод счёта без кнопки ──────────────────────────────────────────────────────

async def _active(db, a, b):
    m = Match(
        challenger_id=a.id, challenged_id=b.id, status=MatchStatus.accepted,
        accepted_at=datetime(2026, 6, 1, 12, 0, 0),
    )
    db.add(m)
    await db.flush()
    return m


async def test_greeting_hint_tells_to_write_score_and_has_no_report_button(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    await _active(db, p1, p2)
    await db.commit()

    msg = AsyncMock()
    msg.from_user = SimpleNamespace(id=1, username="u", full_name="Alice")
    msg.chat = SimpleNamespace(id=1)
    msg.answer = AsyncMock(return_value=SimpleNamespace(message_id=5))
    await cmd_start(msg, SimpleNamespace(args=None), db, _state(1), AsyncMock())
    first = msg.answer.await_args_list[0]
    assert "Просто напиши счёт сюда" in first.args[0]
    assert not any((c or "").startswith("report_") for c in _cbs(first.kwargs["reply_markup"]))


async def test_busy_screen_hints_score_and_offers_only_cancel(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    await _active(db, p1, p2)
    await db.commit()

    text, kb = await _build_challenge_screen(db, 1)
    assert "Напиши счёт сюда" in text
    assert _cbs(kb) == ["cancel_match_1", "back_to_menu"] or _cbs(kb)[-2:] == ["cancel_match_1", "back_to_menu"]
    assert not any((c or "").startswith("report_") for c in _cbs(kb))


async def test_reminder_text_asks_for_score_and_has_no_report_button(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)
    async with db_factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(Match(challenger_id=p1.id, challenged_id=p2.id,
                    status=MatchStatus.accepted, accepted_at=old))
        await s.commit()

    bot = AsyncMock()
    await sched.send_match_reminders(bot)
    call = bot.send_message.await_args_list[0]
    assert "напишите счёт сюда" in call.args[1]
    assert not any((c or "").startswith("report_") for c in _cbs(call.kwargs["reply_markup"]))


async def test_fsm_reset_notice_points_to_text_input():
    cb = _callback(1, "confirm_5")
    await fsm_reset_notice(cb)
    text = cb.message.edit_text.await_args.args[0]
    assert "просто напиши счёт" in text and "Внести результат" not in text


async def test_multiple_active_matches_still_offer_chooser(db):
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _active(db, p1, p2)
    await _active(db, p1, p3)
    msg = AsyncMock()
    msg.from_user = SimpleNamespace(id=1, username="u", full_name="A")
    msg.text = "11:7"
    msg.chat = SimpleNamespace(id=1)
    msg.answer = AsyncMock()
    await handle_direct_score(msg, db, _state(1))
    markup = msg.answer.await_args.kwargs["reply_markup"]
    assert len([c for c in _cbs(markup) if c.startswith("report_")]) == 2


# ── Счётчик: кнопки нижней клавиатуры ──────────────────────────────────────────

def test_usage_counts_reply_keyboard_taps_under_menu_names():
    mw = UsageMiddleware("command")
    ev = lambda t: SimpleNamespace(text=t)   # noqa: E731
    assert mw._extract_raw_action(ev("📊 Рейтинг")) == "menu_leaderboard"
    assert mw._extract_raw_action(ev("📈 Статистика")) == "menu_stats"
    for label in CHALLENGE_BUTTON_LABELS:
        assert mw._extract_raw_action(ev(label)) == "menu_play"
    assert mw._extract_raw_action(ev("/start ABC")) == "/start ABC"
    assert mw._extract_raw_action(ev("11:7 9:11")) is None     # ввод счёта не считается
    assert mw._extract_raw_action(ev("привет")) is None
    assert mw._extract_raw_action(ev(None)) is None


# ── Справка в два уровня (v2.152.0) ────────────────────────────────────────────

async def test_help_toc_has_section_buttons_and_notifications():
    from bot.handlers.start import HELP_INTRO, HELP_SECTIONS

    msg = AsyncMock()
    await cmd_help(msg)
    assert msg.answer.await_args.args[0] == HELP_INTRO
    kb = msg.answer.await_args.kwargs["reply_markup"]
    texts = _texts(kb)
    for expected in (
        "⚔️ Как играть", "🧮 Как считается рейтинг", "🗺 Карта экранов", "🔣 Значки и иконки",
        "⏰ Когда приходят сводки", "⌨️ Команды", "🔔 Настроить рассылки", "« В меню",
    ):
        assert expected in texts
    assert [k for k, _ in HELP_SECTIONS] == ["play", "rating", "screens", "icons", "digests", "commands"]
    assert "Автосводки" not in " ".join(texts) and "Где что искать" not in " ".join(texts)


async def test_every_help_section_opens_with_back_buttons_and_fits_limit():
    from bot.handlers.start import HELP_SECTIONS, show_help_section

    for key, title in HELP_SECTIONS:
        cb = _callback(1, f"help_sec_{key}")
        await show_help_section(cb)
        text = cb.message.edit_text.await_args.args[0]
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        assert len(text) < 3500, key
        assert _cbs(kb)[-2:] == ["menu_help", "back_to_menu"], key
        assert "« К справке" in _texts(kb)


async def test_help_section_contents():
    from bot.handlers.start import _help_section_text

    play = _help_section_text("play")
    assert "11:7 9:11 11:5" in play and "Реванш" in play and "ничья" in play.lower()
    digests = _help_section_text("digests")
    for needle in ("21:30", "понедельник", "1-го числа", "квартала", "30 декабря", "21 по 30 декабря"):
        assert needle in digests, needle
    assert "Спокойной" not in digests


async def test_digests_help_section_offers_notifications_button():
    from bot.handlers.start import show_help_section

    cb = _callback(1, "help_sec_digests")
    await show_help_section(cb)
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert "menu_notifications" in _cbs(kb)
    other = _callback(1, "help_sec_play")
    await show_help_section(other)
    assert "menu_notifications" not in _cbs(other.message.edit_text.await_args.kwargs["reply_markup"])


async def test_unknown_help_section_alerts():
    from bot.handlers.start import show_help_section

    cb = _callback(1, "help_sec_nonsense")
    await show_help_section(cb)
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    cb.message.edit_text.assert_not_awaited()
