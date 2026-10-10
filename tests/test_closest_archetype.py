"""Подсказка «ближе всего к …» для «Универсала» (v2.159.0)."""
from bot.services.stats import (
    NEUTRAL_ARCHETYPE,
    _closest_archetype_hint,
    _style_archetype,
)

AXES = ("Винрейт", "Клатч", "Дожимание", "Камбэки", "Доминирование", "Стабильность")


def _radar(**over):
    base = {"Винрейт": 50.0, "Клатч": 50.0, "Дожимание": 70.0, "Камбэки": 2.0,
            "Доминирование": 55.0, "Стабильность": 60.0}
    base.update(over)
    return base


def _stats(**over):
    s = {"wins": 20, "draws": 0, "losses": 10, "deuce_total": 8, "first_set_wins": 10,
         "dominance_matches": 10}
    s.update(over)
    return s


def test_hint_names_the_nearest_positive_archetype_and_the_missing_points():
    radar = _radar()                   # Дожимание 70 → до 80 не хватает 10; Клатч 50 → до 55 только 5
    assert _style_archetype(radar, _stats()) == NEUTRAL_ARCHETYPE
    hint = _closest_archetype_hint(radar, _stats())
    assert hint == "🧭 Ближе всего к «Нервы стальные»: не хватает 5 пунктов по оси «Клатч»"


def test_hint_rounds_the_gap_up_and_uses_correct_plural():
    hint = _closest_archetype_hint(_radar(Клатч=53.4), _stats())      # 1.6 -> 2
    assert "не хватает 2 пунктов по оси «Клатч»" in hint
    hint = _closest_archetype_hint(_radar(Клатч=54.2), _stats())      # 0.8 -> 1
    assert "не хватает 1 пункта по оси «Клатч»" in hint


def test_hint_ignores_negative_archetypes():
    radar = _radar(Винрейт=41.0, Клатч=50.0, Дожимание=70.0)          # рядом «Донор рейтинга» (<=40)
    hint = _closest_archetype_hint(radar, _stats())
    assert "Донор" not in hint and "Мандраж" not in hint and "Сдувается" not in hint


def test_hint_skips_axes_without_enough_sample():
    radar = _radar(Клатч=54.0)                                        # почти «Нервы стальные»
    thin = _stats(deuce_total=2)                                      # но дьюсов слишком мало
    hint = _closest_archetype_hint(radar, thin)
    assert "Клатч" not in hint


def test_no_hint_when_every_positive_archetype_is_reached():
    radar = _radar(Винрейт=70, Клатч=60, Дожимание=90, Камбэки=10, Доминирование=70, Стабильность=80)
    assert _closest_archetype_hint(radar, _stats(wins=20)) is None


def test_caption_with_hint_stays_under_telegram_photo_limit():
    from bot.services.stats import AXIS_GLOSSARY

    axes = "\n".join(f"{n}: 55% — {AXIS_GLOSSARY[n]}" for n in AXES)
    hint = _closest_archetype_hint(_radar(), _stats())
    caption = "🕸 Стиль игры — " + "Ж" * 32 + "\n🏷 Архетип: Универсал — ровный игрок\n" + hint + "\n\n" + axes
    assert len(caption) < 1024
