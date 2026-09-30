"""Тесты сравнения стилей двух игроков (v2.138.0)."""
import json
import urllib.parse
from datetime import datetime
from unittest.mock import AsyncMock

from bot.handlers.history import MIN_MATCHES_FOR_RADAR, show_style_comparison
from bot.keyboards.inline import h2h_kb
from bot.services.stats import (
    STYLE_COMPARE_MIN_DIFF,
    _compare_styles,
    _style_comparison_caption,
)
from bot.utils import style_compare_url
from tests.conftest import _callback, _completed, _player

AXES = ["Винрейт", "Клатч", "Дожимание", "Камбэки", "Доминирование", "Стабильность"]


def _radar(**over) -> dict[str, float]:
    base = {a: 50.0 for a in AXES}
    base.update(over)
    return base


def _neutral(**over) -> dict[str, float]:
    """Радар, на котором ни один архетип не срабатывает (все оси в «серой зоне»)."""
    base = {
        "Винрейт": 50.0, "Клатч": 45.0, "Дожимание": 50.0,
        "Камбэки": 5.0, "Доминирование": 58.0, "Стабильность": 60.0,
    }
    base.update(over)
    return base


# ── чистые функции ─────────────────────────────────────────────────────────────

def test_compare_styles_splits_axes_by_who_leads():
    a, b = _radar(Клатч=70.0, Камбэки=10.0), _radar(Клатч=40.0, Камбэки=30.0)
    a_better, b_better = _compare_styles(a, b)
    assert a_better == ["Клатч"]
    assert b_better == ["Камбэки"]


def test_compare_styles_small_difference_is_a_draw():
    a = _radar(Клатч=50.0 + STYLE_COMPARE_MIN_DIFF - 1)
    assert _compare_styles(a, _radar()) == ([], [])
    a = _radar(Клатч=50.0 + STYLE_COMPARE_MIN_DIFF)
    assert _compare_styles(a, _radar()) == (["Клатч"], [])


def test_caption_summary_and_bold_leader():
    text = _style_comparison_caption("Боб", _neutral(Клатч=80.0), _neutral(Клатч=40.0, Камбэки=30.0))
    assert "Ты</b> 🆚 <b>Боб" in text
    assert "Клатч: <b>80%</b> vs 40%" in text
    assert "Камбэки: 5% vs <b>30%</b>" in text
    assert "💪 Ты сильнее: Клатч" in text
    assert "🎯 Боб сильнее: Камбэки" in text


def test_caption_identical_styles_say_so():
    text = _style_comparison_caption("Боб", _neutral(), _neutral())
    assert "почти одинаковые" in text
    assert "🏷" not in text  # архетипа нет ни у кого — строку не показываем
    assert "сильнее" not in text.replace("почти", "")


def test_caption_escapes_opponent_name():
    text = _style_comparison_caption("<b>X</b>", _neutral(), _neutral())
    assert "<b>X</b>" not in text.replace("<b>&lt;b&gt;X&lt;/b&gt;</b>", "")
    assert "&lt;b&gt;X&lt;/b&gt;" in text


def test_caption_shows_archetypes_when_present():
    text = _style_comparison_caption("Боб", _neutral(Винрейт=80.0), _neutral())
    assert "Ты — <b>Терминатор</b>, Боб — <b>—</b>" in text


def test_caption_worst_case_fits_photo_caption_limit():
    """Подпись к фото в Telegram — максимум 1024 символа. Худший случай:
    длинное имя соперника, у обоих архетипы, все оси различаются."""
    a = _radar(Винрейт=90.0, Клатч=10.0, Дожимание=90.0, Камбэки=10.0, Доминирование=90.0, Стабильность=10.0)
    b = _radar(Винрейт=10.0, Клатч=90.0, Дожимание=10.0, Камбэки=90.0, Доминирование=10.0, Стабильность=90.0)
    text = _style_comparison_caption("Очень Длинное Имя Соперника" * 2, a, b)
    assert len(text) < 1024 * 0.85, len(text)


def test_compare_url_has_two_datasets_and_legend():
    url = style_compare_url("Алиса", _radar(), "Боб", _radar(Клатч=90.0))
    config = json.loads(urllib.parse.unquote(url.split("&c=")[1]))
    assert [d["label"] for d in config["data"]["datasets"]] == ["Алиса", "Боб"]
    assert config["options"]["legend"]["display"] is True
    assert config["data"]["labels"] == AXES
    assert config["data"]["datasets"][1]["data"][1] == 90.0


def test_h2h_kb_has_compare_button():
    kb = h2h_kb(7)
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "style_cmp_7" in cbs
    # и когда «Вызвать» скрыта
    cbs = [b.callback_data for row in h2h_kb(7, can_challenge=False).inline_keyboard for b in row]
    assert "style_cmp_7" in cbs


# ── хендлер ────────────────────────────────────────────────────────────────────

async def _two_players_with_matches(db, n_viewer: int, n_target: int):
    a, b, c = _player(1, "Алиса"), _player(2, "Боб"), _player(3, "Вера")
    db.add_all([a, b, c])
    await db.flush()
    for i in range(n_viewer):
        db.add(_completed(a, c, a.id, 5.0, datetime(2026, 1, 1 + i, 12, 0, 0)))
    for i in range(n_target):
        db.add(_completed(b, c, b.id, 5.0, datetime(2026, 2, 1 + i, 12, 0, 0)))
    await db.commit()
    return a, b


async def test_compare_sends_photo_with_caption(db):
    a, b = await _two_players_with_matches(db, MIN_MATCHES_FOR_RADAR, MIN_MATCHES_FOR_RADAR)
    cb, bot = _callback(1, f"style_cmp_{b.id}"), AsyncMock()
    await show_style_comparison(cb, db, bot)

    bot.send_photo.assert_awaited_once()
    caption = bot.send_photo.await_args.kwargs["caption"]
    assert "Сравнение стилей" in caption and "Боб" in caption
    assert "quickchart.io" in bot.send_photo.await_args.args[1]


async def test_compare_needs_enough_matches_from_viewer(db):
    a, b = await _two_players_with_matches(db, MIN_MATCHES_FOR_RADAR - 1, MIN_MATCHES_FOR_RADAR)
    cb, bot = _callback(1, f"style_cmp_{b.id}"), AsyncMock()
    await show_style_comparison(cb, db, bot)
    bot.send_photo.assert_not_awaited()
    assert cb.answer.await_args.args[0].startswith("У тебя")


async def test_compare_needs_enough_matches_from_opponent(db):
    a, b = await _two_players_with_matches(db, MIN_MATCHES_FOR_RADAR, MIN_MATCHES_FOR_RADAR - 1)
    cb, bot = _callback(1, f"style_cmp_{b.id}"), AsyncMock()
    await show_style_comparison(cb, db, bot)
    bot.send_photo.assert_not_awaited()
    assert cb.answer.await_args.args[0].startswith("У Боб")


async def test_compare_rejects_self_bad_data_unknown_player_and_unregistered(db):
    a, b = await _two_players_with_matches(db, 5, 5)
    bot = AsyncMock()

    cb = _callback(1, f"style_cmp_{a.id}")
    await show_style_comparison(cb, db, bot)
    assert "самого с собой" in cb.answer.await_args.args[0]

    cb = _callback(1, "style_cmp_abc")
    await show_style_comparison(cb, db, bot)
    cb.answer.assert_awaited_with("Некорректные данные.", show_alert=True)

    cb = _callback(1, "style_cmp_999")
    await show_style_comparison(cb, db, bot)
    cb.answer.assert_awaited_with("Игрок не найден.", show_alert=True)

    cb = _callback(555, f"style_cmp_{b.id}")
    await show_style_comparison(cb, db, bot)
    cb.answer.assert_awaited_with("Сначала напиши /start", show_alert=True)
    bot.send_photo.assert_not_awaited()


async def test_compare_photo_error_is_reported_not_raised(db):
    a, b = await _two_players_with_matches(db, 5, 5)
    cb, bot = _callback(1, f"style_cmp_{b.id}"), AsyncMock()
    bot.send_photo.side_effect = RuntimeError("boom")
    await show_style_comparison(cb, db, bot)
    assert "Не удалось" in cb.answer.await_args.args[0]


def test_profile_kb_has_compare_button_only_for_other_players():
    from bot.keyboards.inline import player_profile_kb

    def cbs(kb):
        return [b.callback_data for row in kb.inline_keyboard for b in row]

    assert "style_cmp_7" in cbs(player_profile_kb(7, viewer_id=1))
    assert "style_cmp_7" in cbs(player_profile_kb(7, viewer_id=1, can_challenge=False))
    assert "style_cmp_7" not in cbs(player_profile_kb(7, viewer_id=7))  # свой профиль
    assert "style_cmp_7" not in cbs(player_profile_kb(7))  # зритель неизвестен
