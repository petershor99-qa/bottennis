"""Выгрузка и разбор счётчика использования (usage_events) — только чтение.

Для этапа 4 дорожной карты и разбора флоу: какие экраны открывают, что после
чего нажимают, как выглядят сессии. Базу не меняет (открывает read-only), имена
игроков не выводит — игроки в отчёте «u1, u2, …» по порядку появления.

Запуск (на сервере, из папки бота):
  /opt/bottennis/.venv/bin/python scripts/export_usage.py            # отчёт
  /opt/bottennis/.venv/bin/python scripts/export_usage.py --csv usage.csv   # сырые события
  ... --since 2026-10-07           # только с даты (МСК)

База берётся из DATABASE_URL (.env рядом со скриптом), либо --db путь/к/файлу.

Оговорка по данным: до v2.157.2 (2026-10-07) события действий с записью в БД
(вызов, ввод счёта, голос) терялись из-за блокировки SQLite, поэтому за более
ранний период такие действия занижены. Нажатия внутри Mini App сюда не попадают
(счётчик у приложения появится отдельно).
"""
import argparse
import csv
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MSK = timedelta(hours=3)
SESSION_GAP = timedelta(minutes=30)   # пауза, после которой начинается новая сессия
STEP_GAP = timedelta(minutes=10)      # «сразу после»: переход между соседними нажатиями
FIX_DATE = datetime(2026, 10, 7)      # дата исправления потери событий записи

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def load_env() -> None:
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def db_path_from_url(url: str) -> Path:
    """sqlite+aiosqlite:////data/x.db -> /data/x.db; ./x.db -> относительно папки бота."""
    m = re.match(r"^sqlite(?:\+\w+)?:///(.*)$", url)
    if not m:
        raise SystemExit(f"Не SQLite-адрес базы: {url!r}")
    raw = m.group(1)
    path = Path(raw)
    # Ведущий «/» — абсолютный путь (на Windows Path.is_absolute() для него False)
    return path if raw.startswith("/") or path.is_absolute() else (REPO / path)


def load_events(path: Path, since: datetime | None = None) -> list[tuple[int, datetime, str]]:
    """[(user_id, время МСК, действие)] по возрастанию времени."""
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT user_id, created_at, action FROM usage_events ORDER BY created_at, id").fetchall()
    finally:
        con.close()
    events = []
    for user_id, created_at, action in rows:
        ts = datetime.fromisoformat(str(created_at)) + MSK
        if since is None or ts >= since:
            events.append((user_id, ts, action))
    return events


def anonymize(events):
    ids: dict[int, str] = {}
    out = []
    for user_id, ts, action in events:
        label = ids.setdefault(user_id, f"u{len(ids) + 1}")
        out.append((label, ts, action))
    return out


def sessions_of(events):
    """Сессии одного игрока: пауза больше SESSION_GAP начинает новую. -> [[(ts, action)]]"""
    by_user: dict[str, list] = defaultdict(list)
    for user, ts, action in events:
        by_user[user].append((ts, action))
    result = []
    for items in by_user.values():
        current = [items[0]]
        for prev, cur in zip(items, items[1:]):
            if cur[0] - prev[0] > SESSION_GAP:
                result.append(current)
                current = []
            current.append(cur)
        result.append(current)
    return result


def label_of(action: str, labels: dict[str, str]) -> str:
    return labels.get(action, action)


def table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def build_report(events, labels: dict[str, str], top: int = 25) -> str:
    if not events:
        return "Событий нет."
    anon = anonymize(events)
    first, last = anon[0][1], anon[-1][1]
    players = {u for u, _, _ in anon}
    lines = [
        "# Использование бота",
        "",
        f"Период: {first:%d.%m.%Y %H:%M} — {last:%d.%m.%Y %H:%M} (МСК). "
        f"Событий: {len(anon)}, игроков: {len(players)}, дней с активностью: {len({ts.date() for _, ts, _ in anon})}.",
    ]
    if first < FIX_DATE:
        lines.append(
            f"Оговорка: до {FIX_DATE:%d.%m.%Y} действия с записью (вызов, ввод счёта, голос) занижены — "
            "события терялись из-за блокировки SQLite (v2.157.2). Нажатия в Mini App сюда не входят."
        )

    # 1. Действия
    counts = Counter(a for _, _, a in anon)
    users_by_action: dict[str, set] = defaultdict(set)
    for u, _, a in anon:
        users_by_action[a].add(u)
    total = len(anon)
    lines += ["", "## Что нажимают чаще всего", ""]
    lines.append(table(
        ["Экран / действие", "Название", "Событий", "Игроков", "Доля"],
        [[a, label_of(a, labels), c, len(users_by_action[a]), f"{c / total * 100:.1f}%"]
         for a, c in counts.most_common(top)],
    ))
    never = sorted(a for a in labels if a not in counts)
    if never:
        lines += ["", "## Ни разу не открывали за период", "", ", ".join(f"{a} ({labels[a]})" for a in never)]

    # 2. Переходы
    pairs: Counter = Counter()
    by_user: dict[str, list] = defaultdict(list)
    for u, ts, a in anon:
        by_user[u].append((ts, a))
    for items in by_user.values():
        for (t1, a1), (t2, a2) in zip(items, items[1:]):
            if t2 - t1 <= STEP_GAP and a1 != a2:
                pairs[(a1, a2)] += 1
    if pairs:
        lines += ["", f"## Что нажимают сразу после чего (в пределах {int(STEP_GAP.total_seconds() // 60)} минут)", ""]
        lines.append(table(
            ["Было", "Потом", "Раз"],
            [[f"{label_of(a, labels)} ({a})", f"{label_of(b, labels)} ({b})", c] for (a, b), c in pairs.most_common(20)],
        ))

    # 3. Сессии
    sess = sessions_of(anon)
    lengths = [len(s) for s in sess]
    firsts = Counter(s[0][1] for s in sess)
    lasts = Counter(s[-1][1] for s in sess)
    paths = Counter(" → ".join(a for _, a in s[:5]) for s in sess)
    lines += [
        "", f"## Сессии (пауза больше {int(SESSION_GAP.total_seconds() // 60)} минут начинает новую)", "",
        f"Сессий: {len(sess)}, нажатий в сессии: в среднем {sum(lengths) / len(lengths):.1f}, медиана {median(lengths):g}, "
        f"максимум {max(lengths)}.",
        "", "Чем начинают:", table(["Действие", "Сессий"], [[f"{label_of(a, labels)} ({a})", c] for a, c in firsts.most_common(8)]),
        "", "Чем заканчивают:", table(["Действие", "Сессий"], [[f"{label_of(a, labels)} ({a})", c] for a, c in lasts.most_common(8)]),
        "", "Самые частые пути (первые 5 шагов):",
        table(["Путь", "Раз"], [[p, c] for p, c in paths.most_common(12)]),
    ]

    # 4. Когда
    by_hour = Counter(ts.hour for _, ts, _ in anon)
    by_wd = Counter(ts.weekday() for _, ts, _ in anon)
    lines += [
        "", "## Когда пользуются", "",
        "По дням недели: " + ", ".join(f"{WEEKDAYS[d]} {by_wd.get(d, 0)}" for d in range(7)),
        "По часам (МСК): " + ", ".join(f"{h:02d}:00 — {c}" for h, c in sorted(by_hour.items())),
    ]

    # 5. Игроки (без имён)
    per_user = Counter(u for u, _, _ in anon)
    lines += ["", "## По игрокам (без имён)", "",
              table(["Игрок", "Событий", "Экранов разных"],
                    [[u, c, len({a for uu, _, a in anon if uu == u})] for u, c in per_user.most_common()])]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Выгрузка и разбор usage_events (только чтение).")
    parser.add_argument("--db", help="путь к файлу SQLite (по умолчанию из DATABASE_URL)")
    parser.add_argument("--since", help="только с даты ГГГГ-ММ-ДД (МСК)")
    parser.add_argument("--csv", help="записать сырые события (игроки обезличены) в файл вместо отчёта")
    parser.add_argument("--top", type=int, default=25, help="сколько действий в таблице (по умолчанию 25)")
    args = parser.parse_args(argv)

    load_env()
    path = Path(args.db) if args.db else db_path_from_url(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bottennis.db"))
    if not path.exists():
        raise SystemExit(f"Файл базы не найден: {path}")
    since = datetime.fromisoformat(args.since) if args.since else None
    events = load_events(path, since)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["player", "time_msk", "action"])
            for user, ts, action in anonymize(events):
                writer.writerow([user, ts.strftime("%Y-%m-%d %H:%M:%S"), action])
        print(f"Записано событий: {len(events)} -> {args.csv}")
        return

    try:
        from bot.services.usage import ACTION_LABELS
    except Exception:
        ACTION_LABELS = {}
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(build_report(events, ACTION_LABELS, args.top))


if __name__ == "__main__":
    main()
