"""Цифры для подбора порогов радара стиля — только чтение, игроки обезличены.

v2.187.0: ось «Камбэки» собираются перевести с «победы, начатые с 0:2» (в
формате до двух побед из 0:2 не отыграться — у всех 0–6%) на «из матчей, где
проиграна первая партия, какая доля выиграна» — так камбэк понимают штамп
матча и «Цели недели». Скрипт показывает обе доли по каждому игроку, чтобы
подобрать порог архетипа «Феникс» по настоящим данным клуба.

Запуск (на сервере, из папки бота):
  /opt/bottennis/.venv/bin/python scripts/style_calibration.py
База — из DATABASE_URL (.env рядом со скриптом) или --db путь/к/файлу.
"""
import argparse
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

from export_usage import db_path_from_url, load_env

MIN_MATCHES = 5   # как MIN_MATCHES_FOR_RADAR: с меньшим числом матчей радара нет


def rows(db: Path) -> list[tuple]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT challenger_id, challenged_id, winner_id, sets_data FROM matches "
            "WHERE status = 'completed' ORDER BY completed_at"
        ).fetchall()
    finally:
        con.close()


def comeback_stats(matches: list[tuple]) -> dict[int, dict[str, int]]:
    """По игроку: матчей, побед, побед с 0:2, матчей с проигранной первой партией
    (от двух партий) и побед в них. sets_data — в перспективе победителя, при
    ничьей — challenger."""
    st: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a, b, winner, raw in matches:
        sets = json.loads(raw) if isinstance(raw, str) else (raw or [])
        persp = winner if winner is not None else a
        for pid in (a, b):
            s = st[pid]
            s["matches"] += 1
            if winner == pid:
                s["wins"] += 1
            mine = [(x["w"], x["l"]) if persp == pid else (x["l"], x["w"]) for x in sets]
            if len(mine) >= 2 and mine[0][0] < mine[0][1]:
                s["lost_first"] += 1
                if winner == pid:
                    s["after_lost_first"] += 1
            if winner == pid and len(mine) >= 2 and mine[0][0] < mine[0][1] and mine[1][0] < mine[1][1]:
                s["from_0_2"] += 1
    return st


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    args = ap.parse_args()
    load_env()
    db = Path(args.db) if args.db else db_path_from_url(os.environ.get("DATABASE_URL", "sqlite:///bottennis.db"))
    st = comeback_stats(rows(db))
    players = sorted((s for s in st.values() if s["matches"] >= MIN_MATCHES), key=lambda s: -s["matches"])
    print("Игрок  матчей  побед  сейчас(с 0:2 от побед)  предлагается(победы после проигранной 1-й)")
    for i, s in enumerate(players, 1):
        old = round(s["from_0_2"] / s["wins"] * 100) if s["wins"] else 0
        new = round(s["after_lost_first"] / s["lost_first"] * 100) if s["lost_first"] else 0
        print(f"u{i:<5} {s['matches']:>6} {s['wins']:>6} {old:>10}%  {new:>18}%  ({s['after_lost_first']} из {s['lost_first']})")


if __name__ == "__main__":
    main()
