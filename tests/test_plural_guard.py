"""
Страховка склонений (v2.212.0): в f-строках бота число не подставляется в готовое
слово («{n} партий», «{n} побед», «{n} матчей»…) — для этого есть `pluralize_*`
(bot/utils.py). Так в v2.205.1 нашлись «2 партий» и «21 матчей».
Запуск: pytest tests/test_plural_guard.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "bot"
WORDS = "партий|очков|побед|поражений|матчей|ничьих|игроков|защит"
PATTERN = re.compile(r"\{[^{}]*\}(?:</b>)?\*{0,2} (?:%s)\b" % WORDS)

# Допустимо: число заведомо ≥5 (константы), пулы фраз с {n} от 5 партий. Ключ — файл,
# значение — подстроки строк, которые можно не склонять.
ALLOWED = {
    "phrases.py": ("{n} партий",),                       # марафон: n ≥ 5 по условию
    "utils.py": ("марафон на {n} партий",),              # то же
    "handlers/match_result.py": ("{MAX_SETS} партий", "{milestone} очков"),   # 9; тысячи
    "handlers/history.py": ("{MIN_MATCHES_FOR_RADAR} матчей",),               # 5
    "handlers/leaderboard.py": ("(последние {FORM_WINDOW} матчей)",),         # 10
    "webapp/api.py": ("{MIN_MATCHES_FOR_RADAR} матчей",),
}


def test_no_hardcoded_plural_after_number():
    bad = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#") or not PATTERN.search(line):
                continue
            if any(ok in line for ok in ALLOWED.get(rel, ())):
                continue
            bad.append(f"{rel}:{lineno}: {line.strip()[:110]}")
    assert not bad, "число подставлено в готовое слово — используй pluralize_*:\n" + "\n".join(bad)
