#!/usr/bin/env python3
"""Синхронизирует счётчики тестов и достижений в README.md/TESTING.md с
фактическими цифрами из pytest/ACHIEVEMENTS_LIST — самая частая ручная
рутина в релизном флоу (grep+sed по нескольким файлам на каждый релиз;
ловится не только руками — при отладке этого скрипта он же и нашёл
реальный рассинхрон: код уже на 58/27 ачивок, часть доков ещё на 57/26).

НЕ трогает CLAUDE.md — там оба числа встречаются и в исторических заметках
("Запас теперь ýже (57 ачивок)" — точный снимок момента, когда решение
принималось), и в форвард-ссылках, где слепая замена исказила бы смысл.
CLAUDE.md остаётся на ручной/Клодовской сверке — см. шаг 4 в SKILL.md.

ВАЖНО про безопасность замены: числа заменяются ТОЛЬКО внутри заранее
известных, заякоренных фраз (см. PATTERNS ниже) — никогда не голым свипом
"заменить все вхождения числа N в файле". При живой отладке голый
`\bN\b`-свип по всему файлу один раз реально сломал не связанную фразу
("напоминание через 24 часа" превратилось в "через 27 часа", когда
скрипт синхронизировал число скрытых ачивок 24->27) — числа вроде 24-27
слишком малы и слишком часто совпадают с чем-то посторонним в прозе,
в отличие от количества тестов (трёхзначное, коллизий не было).

Использование (из корня репозитория):
    py -3.13 .claude/skills/release/scripts/sync_test_count.py
"""
import re
import subprocess
import sys
from pathlib import Path

# Windows-консоль по умолчанию не в UTF-8 — без этого кириллица в print()
# превращается в кракозябры (живой пример был при отладке этого скрипта).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[4]  # .claude/skills/release/scripts/ -> repo root

# Каждая запись — (файл, заякоренный шаблон с ОДНОЙ числовой группой, имя
# для лога). Шаблон должен однозначно матчить фразу целиком, не голое
# число — так замена никогда не заденет посторонний текст.
TEST_COUNT_PATTERNS = [
    ("README.md", r"(сейчас их \*\*)\d+(\*\*)", "«сейчас их **N**»"),
    ("README.md", r"(pytest -v {10}# )\d+( автотеста, ~7 секунд)", "«# N автотеста, ~7 секунд»"),
    ("README.md", r"(\*\*)\d+( автотеста\*\* на `pytest`)", "«**N автотеста** на pytest»"),
    ("README.md", r"(tests/ {22}# )\d+( автотеста \(юнит \+ хендлеры\))", "«# N автотеста (юнит + хендлеры)»"),
    ("TESTING.md", r"(Сейчас \*\*)\d+( тестов\*\*)", "«Сейчас **N тестов**»"),
]

ACHIEVEMENT_COUNT_PATTERNS = [
    ("README.md", r"(все )\d+( ачивок, идемпотентность)", "«все N ачивок» (таблица покрытия)"),
]
ACHIEVEMENT_TOTAL_HIDDEN_PATTERNS = [
    # (файл, шаблон с ДВУМЯ группами: total, hidden)
    ("README.md", r"(\*\*)\d+( достижений\*\* \()\d+( скрытых\))", "«**N достижений** (M скрытых)»"),
    ("README.md", r"(Система достижений \()\d+( ачивок, )\d+( из них скрыты)", "«N ачивок, M из них скрыты»"),
]


def get_current_test_count() -> int:
    result = subprocess.run(
        ["py", "-3.13", "-m", "pytest", "-q"],
        cwd=ROOT, capture_output=True, text=True,
    )
    match = re.search(r"(\d+) passed", result.stdout)
    if not match:
        print("Не удалось распарсить число тестов из вывода pytest — тесты красные?", file=sys.stderr)
        print(result.stdout[-3000:], file=sys.stderr)
        sys.exit(1)
    return int(match.group(1))


def get_current_achievement_counts() -> tuple[int, int]:
    sys.path.insert(0, str(ROOT))
    from bot.services.achievements import ACHIEVEMENTS_LIST
    total = len(ACHIEVEMENTS_LIST)
    hidden = sum(1 for a in ACHIEVEMENTS_LIST if a.hidden)
    return total, hidden


def apply_single_group(new_value: int, patterns: list[tuple[str, str, str]]) -> int:
    """Подставляет new_value в первую числовую группу каждого шаблона.
    Возвращает число реально изменённых файлов-вхождений."""
    changed = 0
    for filename, pattern, label in patterns:
        path = ROOT / filename
        text = path.read_text(encoding="utf-8")
        new_text, n = re.subn(pattern, rf"\g<1>{new_value}\g<2>", text)
        if n:
            path.write_text(new_text, encoding="utf-8")
            print(f"  {filename} [{label}]: обновлено")
            changed += n
        elif re.search(pattern.replace(r"\d+", r"\\d+", 1), text) is None:
            pass  # шаблон и так не встретился — например, число уже совпадает
    return changed


def apply_double_group(new_total: int, new_hidden: int, patterns: list[tuple[str, str, str]]) -> int:
    changed = 0
    for filename, pattern, label in patterns:
        path = ROOT / filename
        text = path.read_text(encoding="utf-8")
        new_text, n = re.subn(pattern, rf"\g<1>{new_total}\g<2>{new_hidden}\g<3>", text)
        if n:
            path.write_text(new_text, encoding="utf-8")
            print(f"  {filename} [{label}]: обновлено")
            changed += n
    return changed


def main() -> None:
    new_tests = get_current_test_count()
    print(f"Тесты (факт): {new_tests}")
    apply_single_group(new_tests, TEST_COUNT_PATTERNS)

    new_total, new_hidden = get_current_achievement_counts()
    print(f"Ачивки (факт): {new_total} всего, {new_hidden} скрытых")
    apply_single_group(new_total, ACHIEVEMENT_COUNT_PATTERNS)
    apply_double_group(new_total, new_hidden, ACHIEVEMENT_TOTAL_HIDDEN_PATTERNS)

    print(
        "\nГотово. Каждая замена — по заякоренной фразе, не по голому числу, "
        "так что уже актуальные места просто молча пропускаются (0 вхождений "
        "— это нормально, не ошибка).\n"
        "CLAUDE.md НЕ тронут — оба числа там соседствуют с историческим "
        "контекстом (версия момента принятия решения и т.п.), нужна сверка "
        "вручную/через Клода, см. SKILL.md шаг 4."
    )


if __name__ == "__main__":
    main()
