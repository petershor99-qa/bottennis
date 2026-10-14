"""
Сборка страницы Mini App (v2.176.0): `webapp/static/app.js` склеивается из
частей `webapp/src/app/*.js` по порядку имён. Сборщика нет и не нужно — это
обычная склейка: части — куски одной функции-обёртки (общее состояние страницы
живёт в её замыкании), поэтому по отдельности они не запускаются.

Правите часть -> запускаете скрипт -> коммитите и часть, и app.js.
Тест `tests/test_app_build.py` и CI ловят, если app.js забыли пересобрать.

    py -3.13 scripts/build_app_js.py          # собрать
    py -3.13 scripts/build_app_js.py --check  # только проверить (код 1 — не совпадает)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
OUT = ROOT / "webapp" / "static" / "app.js"
HEADER = "// Собрано scripts/build_app_js.py из webapp/src/app/*.js — правьте части там.\n"


def build() -> str:
    parts = sorted(SRC.glob("*.js"))
    return HEADER + "".join(p.read_text(encoding="utf-8") for p in parts)


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("webapp/static/app.js не совпадает с webapp/src/app — запустите scripts/build_app_js.py")
            return 1
        print("app.js собран из актуальных частей")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"app.js: {len(text.splitlines())} строк из {len(sorted(SRC.glob('*.js')))} частей")
    return 0


if __name__ == "__main__":
    sys.exit(main())
