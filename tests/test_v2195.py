"""
v2.195.0: `scripts/style_report.py` — разбор радара стиля по базе (только
чтение, обезличенно) для калибровки показателей и архетипов.
Запуск: pytest tests/test_v2195.py
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bot.db.models import Base, Match, MatchStatus, Player

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import style_report  # noqa: E402


def _seed(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        names = ["Алиса", "Боб", "Вика", "Гоша"]
        ps = [Player(telegram_id=100 + i, username=None, display_name=n, rating=1000.0 + i * 20) for i, n in enumerate(names)]
        s.add_all(ps)
        s.flush()
        t = datetime(2026, 9, 1, 12)
        k = 0
        for i, a in enumerate(ps):
            for b in ps[i + 1:]:
                for j in range(6):
                    w = a if (j + i) % 3 else b
                    sets = [{"w": 11, "l": 13 - j % 3 * 3}, {"w": 12, "l": 10}, {"w": 11, "l": 6}] if j % 2 else \
                        [{"w": 7, "l": 11}, {"w": 11, "l": 8}, {"w": 13, "l": 11}]
                    k += 1
                    s.add(Match(challenger_id=a.id, challenged_id=b.id, status=MatchStatus.completed, winner_id=w.id,
                                sets_data=sets, rating_change=8.0, completed_at=t + timedelta(hours=k)))
        s.commit()
    engine.dispose()


def test_comeback_after_lost_first():
    class M:
        def __init__(self, a, b, w, sets):
            self.challenger_id, self.challenged_id, self.winner_id = a, b, w
            self.sets_data = [{"w": x, "l": y} for x, y in sets]
    ms = [M(1, 2, 1, [(7, 11), (11, 5), (11, 9)]), M(1, 2, 2, [(11, 6), (8, 11), (11, 7)]), M(1, 2, 1, [(11, 3)])]
    assert style_report.comeback_after_lost_first(ms, 1) == (1, 2)     # отыгрался 1 раз из 2
    assert style_report.comeback_after_lost_first(ms, 2) == (0, 0)     # первую партию не проигрывал


def test_crushing_sets():
    """v2.196.0: кандидат на ДОМ — выигранные партии, где соперник набрал не больше 6."""
    class M:
        def __init__(self, a, b, w, sets):
            self.challenger_id, self.challenged_id, self.winner_id = a, b, w
            self.sets_data = [{"w": x, "l": y} for x, y in sets]
    ms = [M(1, 2, 1, [(11, 6), (9, 11), (11, 9)]), M(1, 2, 2, [(11, 4), (7, 11), (11, 2)])]
    assert style_report.crushing_sets(ms, 1) == (1, 3)     # 11:6 разгромная; 11:9 и 11:7 — нет
    assert style_report.crushing_sets(ms, 2) == (2, 3)     # 9:11 нет; 11:4 и 11:2 — да


def test_report_runs_read_only_and_anonymized(tmp_path):
    db = tmp_path / "club.db"
    _seed(db)
    before = db.stat().st_mtime_ns
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("DATABASE_URL", None)
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "style_report.py"), "--db", str(db)],
                         capture_output=True, text=True, encoding="utf-8", env=env, check=True).stdout
    assert "Игроков от 5 матчей: 4" in out and "u1" in out and "u4" in out
    assert "разгромные партии" in out and "Архетипы:" in out and "ДОМ" in out
    assert not any(n in out for n in ("Алиса", "Боб", "Вика", "Гоша"))
    assert db.stat().st_mtime_ns == before                     # база не тронута
