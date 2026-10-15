"""Облегчённый шрифт Raleway для Mini App (v2.180.0).

Исходник — webapp/fonts-src/Raleway-Variable.woff2 (OFL, все начертания и знаки,
79 КБ) — на страницу не отдаётся. Приложению нужны только знаки из unicode-range
в app.css (латиница, кириллица, пунктуация; цифры — из системного шрифта и
шрифта табло). Скрипт урезает исходник до них (все толщины остаются: проверено,
что ограничение толщин файл не уменьшает, а увеличивает) и пишет
webapp/static/fonts/Raleway-Subset.woff2 — первый запуск приложения легче.

Запуск после смены unicode-range или толщин в app.css (нужен fonttools):
  py -3.13 scripts/subset_font.py
"""
import io
import re
import sys
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "webapp" / "fonts-src" / "Raleway-Variable.woff2"
TARGET = ROOT / "webapp" / "static" / "fonts" / "Raleway-Subset.woff2"
CSS = ROOT / "webapp" / "static" / "app.css"
FEATURES = ["kern", "liga", "case", "locl"]


def css_unicode_range() -> list[int]:
    """Кодовые точки из unicode-range шрифта Raleway в app.css."""
    css = CSS.read_text(encoding="utf-8")
    block = re.search(r'font-family: "Raleway";.*?unicode-range:(.*?);', css, re.S)
    if not block:
        sys.exit("В app.css не найден unicode-range шрифта Raleway")
    points: list[int] = []
    for part in block.group(1).split(","):
        m = re.match(r"\s*U\+([0-9A-Fa-f]+)(?:-([0-9A-Fa-f]+))?", part)
        if m:
            start = int(m.group(1), 16)
            end = int(m.group(2) or m.group(1), 16)
            points.extend(range(start, end + 1))
    return points


def build() -> bytes:
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = FEATURES
    opts.name_IDs = ["*"]                # лицензия OFL и имя шрифта остаются
    opts.notdef_outline = True
    font = subset.load_font(str(SOURCE), opts)
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=css_unicode_range())
    sub.subset(font)
    out = io.BytesIO()
    subset.save_font(font, out, opts)
    return out.getvalue()


def main() -> None:
    data = build()
    TARGET.write_bytes(data)
    print(f"{TARGET.name}: {SOURCE.stat().st_size // 1024} -> {len(data) // 1024} КБ")


if __name__ == "__main__":
    main()
