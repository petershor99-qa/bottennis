"""
Калибровка радара стиля по прод-базе (v2.196.0, `scripts/style_report.py`,
2026-10-16, 7 игроков, обезличены). Держит то, что утвердил владелец:
архетипы разные (5 видов на семерых), камбэки считаются по новой формуле и
различают игроков, цвета на карточке не сплошь белые.
Запуск: pytest tests/test_radar_calibration.py
"""
from collections import Counter

from bot.services.stats import _style_archetype
from bot.webapp import style_tiers

AXES = ["Винрейт", "Клатч", "Дожимание", "Камбэки", "Доминирование", "Стабильность"]

# (матчей, побед; ВИН, КЛЧ/дьюсов, ДОЖ/побед в 1-й партии, КМБ/матчей с проигранной 1-й,
#  ДОМ/выигранных матчей, СТБ; архетип)
CLUB = [
    (260, 106, 40, (40, 114), (71, 121), (15, 136), (59, 106), 62, "Универсал"),
    (239, 164, 68, (61, 143), (82, 159), (42, 79), (58, 164), 62, "Феникс"),
    (217, 100, 46, (50, 101), (69, 112), (23, 97), (57, 100), 57, "Универсал"),
    (144, 29, 20, (39, 51), (62, 32), (9, 100), (56, 29), 77, "Донор рейтинга"),
    (131, 55, 41, (46, 68), (83, 54), (13, 75), (58, 55), 56, "Финишер"),
    (91, 74, 81, (59, 58), (92, 71), (40, 20), (61, 74), 77, "Терминатор"),
    (18, 1, 5, (33, 3), (100, 1), (0, 16), (54, 1), 93, "Донор рейтинга"),
]


def _player(row):
    n, wins, win, (clt, deuces), (conv, fsw), (cmb, lost_first), (dom, dm), stb, _ = row
    radar = dict(zip(AXES, map(float, (win, clt, conv, cmb, dom, stb)), strict=True))
    s = {"wins": wins, "draws": 0, "losses": n - wins, "deuce_total": deuces, "first_set_wins": fsw,
         "dominance_matches": dm, "lost_first_total": lost_first}
    return radar, s


def test_archetypes_vary_on_real_club():
    got = [_style_archetype(*_player(row)) for row in CLUB]
    assert got == [row[-1] for row in CLUB]
    assert len(set(got)) == 5 and Counter(got)["Донор рейтинга"] == 2   # было: Донор у троих, 3 вида


def test_card_colors_vary_on_real_club():
    players = [_player(row) for row in CLUB]
    ref = {a: [(i, r[a]) for i, (r, s) in enumerate(players) if style_tiers.has_sample(a, s)] for a in AXES}
    colors = {a: [style_tiers.tier(a, r[a], s, i, ref)[0] for i, (r, s) in enumerate(players)] for a in AXES}
    assert colors["Камбэки"] == ["white", "gold", "white", "white", "white", "green", "red"]
    assert colors["Клатч"] == ["red", "gold", "white", "red", "white", "green", "gray"]
    assert colors["Дожимание"] == ["white", "white", "red", "red", "green", "gold", "gray"]
    assert colors["Доминирование"] == ["white", "white", "white", "red", "white", "gold", "gray"]
    white = sum(c == "white" for col in colors.values() for c in col)
    assert white <= 19          # из 42 клеток; до v2.196.0 белых было 25, цветных 13 (стало 20)
