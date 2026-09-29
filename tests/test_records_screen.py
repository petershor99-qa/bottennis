"""Тесты экрана «Рекорды клуба» в два уровня (v2.136.0): оглавление с
категориями на кнопках + экран категории, и проверка лимита Telegram."""
import random
from datetime import datetime, timedelta

from bot.handlers.leaderboard import (
    RECORD_CATEGORIES,
    _collect_club_records,
    _render_records_category,
    show_club_records,
    show_club_records_category,
)
from tests.conftest import _callback, _completed, _player

TELEGRAM_LIMIT = 4096


async def _club_with_history(db, players: int = 6, matches: int = 80):
    """Клуб с богатой историей — чтобы сработало максимум рекордов сразу."""
    ps = [_player(i + 1, f"Игрок{i + 1}") for i in range(players)]
    db.add_all(ps)
    await db.flush()
    rnd = random.Random(7)
    start = datetime(2026, 1, 5, 12, 0, 0)
    for i in range(matches):
        a, b = rnd.sample(ps, 2)
        winner = rnd.choice([a, b])
        sets = []
        for _ in range(rnd.choice([1, 2, 2, 3, 5])):
            win_pts, lose_pts = rnd.choice([(11, 4), (11, 9), (12, 10), (13, 11), (11, 7), (15, 13)])
            sets.append({"w": win_pts, "l": lose_pts})
        m = _completed(a, b, winner.id, rnd.choice([4.0, 12.5, 26.0]), start + timedelta(hours=i * 7))
        m.sets_data = sets
        db.add(m)
    await db.commit()
    return ps


async def test_records_toc_shows_category_buttons_with_counts(db):
    await _club_with_history(db)
    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    buttons = [b for row in kb.inline_keyboard for b in row]
    cat_buttons = [b for b in buttons if b.callback_data.startswith("rec_cat_")]
    assert cat_buttons, "должна быть хотя бы одна категория"
    assert all(b.text[-1].isdigit() for b in cat_buttons)  # число рекордов на кнопке
    assert buttons[-1].callback_data == "menu_leaderboard"
    assert "Рекорды клуба" in text
    # само оглавление не должно быть простынёй с рекордами
    assert len(text) < 300


async def test_records_toc_empty_club(db):
    cb = _callback(1, "club_records")
    await show_club_records(cb, db)
    assert "Матчей ещё не было" in cb.message.edit_text.await_args.args[0]


async def test_records_category_screen_lists_records_and_goes_back(db):
    await _club_with_history(db)
    groups = await _collect_club_records(db)
    key = next(k for k, _ in RECORD_CATEGORIES if groups[k])

    cb = _callback(1, f"rec_cat_{key}")
    await show_club_records_category(cb, db)

    text = cb.message.edit_text.await_args.args[0]
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert dict(RECORD_CATEGORIES)[key] in text
    assert kb.inline_keyboard[-1][0].callback_data == "club_records"
    assert groups[key][0] in text


async def test_records_unknown_category_alerts(db):
    cb = _callback(1, "rec_cat_nonsense")
    await show_club_records_category(cb, db)
    cb.answer.assert_awaited_with("Раздел не найден.", show_alert=True)
    cb.message.edit_text.assert_not_awaited()


async def test_records_every_record_lives_in_exactly_one_category(db):
    await _club_with_history(db)
    groups = await _collect_club_records(db)
    assert set(groups) == {k for k, _ in RECORD_CATEGORIES}
    all_records = [r for k, _ in RECORD_CATEGORIES for r in groups[k]]
    assert len(all_records) == len(set(all_records))


async def test_records_each_category_stays_under_telegram_limit(db):
    """РЕГРЕССИЯ-СТРАХОВКА (v2.136.0): у рекордов не было проверки лимита
    Telegram (4096 символов), хотя достижения на нём упирались трижды. Проверяем
    каждую категорию на богатой истории. Запас — не менее 15%: рекордов будет
    только больше."""
    await _club_with_history(db, players=7, matches=200)
    groups = await _collect_club_records(db)
    assert sum(len(g) for g in groups.values()) >= 10, "сценарий должен включать много рекордов"
    for key, title in RECORD_CATEGORIES:
        text = _render_records_category(title, groups[key])
        assert len(text) < TELEGRAM_LIMIT * 0.85, (key, len(text))


async def test_records_worst_case_synthetic_category_fits():
    """Синтетический худший случай: 12 самых длинных записей в одной категории
    (двухстрочные эпичные матчи с 10 партиями) — на вырост, если категория
    разрастётся."""
    long_record = (
        "🌟 <b>Самый эпичный матч</b>\n"
        "<b>Очень Длинное Имя Игрока</b> vs <b>Другое Очень Длинное Имя</b> — "
        + ", ".join("13:11" for _ in range(10)) + "  <i>31.12.26</i>\n"
        "<i>" + "Фраза репортажа " * 8 + "</i>"
    )
    text = _render_records_category("🌟 Топ-моменты", [long_record] * 12)
    assert len(text) < TELEGRAM_LIMIT

