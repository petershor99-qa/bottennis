"""Сборка набора иконок ачивок для Mini App (v2.165.0).

Берёт линейные иконки Tabler Icons (лицензия MIT, https://github.com/tabler/tabler-icons)
по смыслу каждой ачивки и собирает их в один файл-набор webapp/static/icons.svg
(`<symbol id="ti-имя">`), а выбранное сопоставление «id ачивки -> иконка» пишет в
bot/webapp/achievement_icons.py. Страница использует иконки через
`<use href="static/icons.svg#ti-имя">` — внешнего CDN нет.

Для каждой ачивки перечислены иконки по убыванию желательности: берётся первая,
которая есть в библиотеке. Запуск (нужна сеть, один раз; результат лежит в репо):
  py -3.13 scripts/build_icons.py
"""
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW = "https://raw.githubusercontent.com/tabler/tabler-icons/main/icons/outline/{}.svg"
LICENSE_URL = "https://raw.githubusercontent.com/tabler/tabler-icons/main/LICENSE"

# id ачивки -> кандидаты. Лестницы (серии, матчи, очки, партии, поражения)
# намеренно делят одну иконку — ступень различается цветом (бронза/серебро/золото).
CANDIDATES: dict[str, list[str]] = {
    # Старт карьеры
    "press_start": ["eye", "binoculars"],
    "first_blood": ["droplet", "bandage"],
    "beginners_luck": ["clover", "sparkles"],
    "first_pancake": ["mood-sad", "mood-confuzed"],
    "first_draw": ["equal", "scale"],
    # Серии
    "hat_trick": ["flame"],
    "im_on_fire": ["flame"],
    "god_mode": ["flame"],
    "phoenix": ["feather", "bird"],
    "dominator": ["skull"],
    "fk_tyumen": ["trending-down"],
    "valley_of_tears": ["trending-down"],
    "personal_prey": ["target-arrow", "target"],
    "takova_zhis": ["arrows-exchange", "arrows-shuffle"],
    # Особые победы
    "david_goliath": ["ship", "mountain"],
    "marathon": ["hourglass", "clock"],
    "fatality": ["sword", "swords"],
    "no_sweat": ["mood-happy", "mood-smile"],
    "revenge": ["arrow-back-up", "rotate-clockwise"],
    "comeback": ["trending-up", "arrow-up-right"],
    "blown_lead": ["mood-cry", "mood-sad"],
    "loser_full_set": ["package", "backpack"],
    "deuce_maker": ["repeat", "refresh"],
    "titans": ["swords", "sword"],
    "terminator_slain": ["robot", "cpu"],
    "night_owl": ["moon", "moon-stars"],
    "deuce_storm": ["tornado", "wind"],
    "no_rest_win": ["bolt", "flare"],
    "absolute_zero": ["snowflake", "temperature-minus"],
    "weekend_warrior": ["calendar-event", "calendar"],
    # Объём и вехи
    "fifty": ["ball-tennis", "circle"],
    "veteran": ["ball-tennis", "circle"],
    "legend": ["ball-tennis", "circle"],
    "workhorse": ["ball-tennis", "circle"],
    "monument": ["ball-tennis", "circle"],
    "superstar": ["ball-tennis", "circle"],
    "point_saver": ["coins", "coin"],
    "sturdy_grinder": ["coins", "coin"],
    "point_farmer": ["coins", "coin"],
    "set_sniper": ["target", "focus"],
    "set_veteran": ["target", "focus"],
    "set_legend": ["target", "focus"],
    "maniac": ["activity", "bolt"],
    "rating_1200": ["chart-line", "trending-up"],
    "punching_bag": ["shield-half", "shield"],
    "relentless": ["medal", "award"],
    "groundhog_day": ["calendar-repeat", "repeat"],
    "night_king": ["moon-stars", "moon"],
    "round_hundred": ["number-100-small", "hash"],
    "rock_bottom": ["arrow-bar-to-down", "arrow-down"],
    "unbreakable": ["shield-half", "shield"],
    "tempered": ["shield-half", "shield"],
    "resilient": ["heart-handshake", "heart"],
    "battle_tested": ["heart-handshake", "heart"],
    "through_fire": ["heart-handshake", "heart"],
    # Дух клуба
    "diplomat": ["handshake", "heart-handshake"],
    "collector": ["users", "user-plus"],
    "anchorage_spirit": ["ban", "circle-x"],
    "full_circle_week": ["refresh", "rotate-clockwise-2"],
    "draw_double": ["equal", "scale"],
    "club_school": ["school", "book"],
    # Трон
    "highlander": ["crown"],
    "throne_defended": ["shield-check", "shield"],
    "throne_denied": ["crown-off", "x"],
    "chance_blown": ["flag-off", "flag"],
    "first_crown": ["crown"],
    "fortress": ["building-fortress", "shield"],
    "guard_change": ["arrows-exchange", "swords"],
    "abdication": ["zzz", "armchair"],
}

# Интерфейсные значки (замок, закрытое, общие)
UI_ICONS = [
    "lock", "trophy", "question-mark", "star",
    # Интерфейс приложения (v2.169.0): вкладки, строки меню, кнопки, пустые
    # экраны, подсказка первого запуска, реакции на матчи, «Когда играют»
    "home", "chart-bar", "user", "crown", "calendar", "list", "flag", "flame",
    "target", "chart-histogram", "users", "swords", "chevron-right", "mood-empty",
    "ball-tennis", "info-circle", "x", "heart", "thumb-up", "mood-happy", "clock",
    "chart-pie", "flask", "poo",
    # v2.176.0: листание итогов дня, «Поделиться», фильтр истории
    "chevron-left", "share", "filter",
    # v2.177.0: «Твой месяц», летопись, счета клуба
    "sparkles", "book", "list-numbers",
]


def fetch(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 — фиксированные https-адреса
            return resp.read().decode("utf-8")
    except Exception:
        return None


def inner(svg: str) -> str:
    """Содержимое <svg>…</svg> без рамки 24x24 (пустой контур-подложки)."""
    body = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
    body = re.sub(r'<path[^>]*stroke="none"[^>]*/>', "", body)
    return re.sub(r"\s+", " ", body).strip()


def main() -> None:
    wanted = {name for names in CANDIDATES.values() for name in names} | set(UI_ICONS)
    found: dict[str, str] = {}
    for name in sorted(wanted):
        svg = fetch(RAW.format(name))
        if svg:
            found[name] = inner(svg)
    missing = sorted(wanted - found.keys())
    print(f"Найдено иконок: {len(found)} из {len(wanted)}")
    if missing:
        print("Нет в библиотеке (берутся запасные):", ", ".join(missing))

    mapping: dict[str, str] = {}
    for ach, names in CANDIDATES.items():
        choice = next((n for n in names if n in found), None)
        if choice is None:
            sys.exit(f"Для ачивки {ach} не нашлось ни одной иконки: {names}")
        mapping[ach] = choice
    used = sorted(set(mapping.values()) | set(UI_ICONS) & found.keys())

    symbols = "\n".join(
        f'  <symbol id="ti-{name}" viewBox="0 0 24 24">{found[name]}</symbol>' for name in used
    )
    sprite = (
        '<svg xmlns="http://www.w3.org/2000/svg" style="display:none">\n'
        "  <!-- Tabler Icons, MIT License, https://github.com/tabler/tabler-icons -->\n"
        f"{symbols}\n</svg>\n"
    )
    (REPO / "webapp/static/icons.svg").write_text(sprite, encoding="utf-8", newline="\n")

    lines = [
        '"""Иконка каждой ачивки для Mini App (v2.165.0). Файл создаёт scripts/build_icons.py,',
        "руками не править: смысл сопоставления и запасные варианты — там.\"\"\"",
        "",
        "ACHIEVEMENT_ICONS: dict[str, str] = {",
    ]
    lines += [f'    "{ach}": "{icon}",' for ach, icon in mapping.items()]
    lines += ["}", "", f"UI_ICONS = {used!r}", ""]
    (REPO / "bot/webapp/achievement_icons.py").write_text("\n".join(lines), encoding="utf-8", newline="\n")

    lic = fetch(LICENSE_URL)
    if lic:
        (REPO / "webapp/static/icons-LICENSE.txt").write_text(lic, encoding="utf-8", newline="\n")
    print(f"Иконок в наборе: {len(used)}; ачивок сопоставлено: {len(mapping)}")


if __name__ == "__main__":
    main()
