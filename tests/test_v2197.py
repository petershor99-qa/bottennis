"""
v2.197.0: квартальный «техосмотр» — `scripts/quarterly_report.py` (одна команда
на сервере, только чтение, обезличенно) и напоминание администратору раз в
квартал (15 января, апреля, июля, октября).
Запуск: pytest tests/test_v2197.py
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import bot.scheduler as sched
from bot.db.models import AppBoot, ClubRecordPing, Match, MatchStatus, Player
from tests.test_v2195 import _seed

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

ROOT = Path(__file__).resolve().parent.parent


def _seed_recent(path: Path) -> None:
    """К базе из test_v2195 — свежие матчи (для «Целей недели»), ачивки, пинги, запуски."""
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as s:
        ps = s.query(Player).order_by(Player.id).all()
        ps[0].achievements = json.dumps(["first_blood", "first_pancake"])
        ps[1].achievements = json.dumps(["first_blood"])
        ps[3].rating = 1000.0
        for d in range(1, 20):
            a, b = ps[d % 2], ps[2 + d % 2]
            s.add(Match(challenger_id=a.id, challenged_id=b.id, status=MatchStatus.completed, winner_id=a.id,
                        sets_data=[{"w": 11, "l": 5}, {"w": 12, "l": 10}], rating_change=9.5,
                        completed_at=now - timedelta(days=d, hours=2)))
        s.add_all([ClubRecordPing(kind="streak", player_id=ps[0].id, value=6, pinged_at=now - timedelta(days=3)),
                   ClubRecordPing(kind="streak", player_id=ps[1].id, value=6, pinged_at=now - timedelta(days=3)),
                   AppBoot(ms=1800, platform="android", created_at=now - timedelta(days=1)),
                   AppBoot(ms=6200, platform="tdesktop", created_at=now - timedelta(days=2)),
                   AppBoot(ms=0, platform="ios", failed=True, created_at=now - timedelta(days=2))])
        s.commit()
    engine.dispose()


def test_quarterly_report_all_sections_read_only_anonymized(tmp_path):
    db = tmp_path / "club.db"
    _seed(db)
    _seed_recent(db)
    before = db.stat().st_mtime_ns
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("DATABASE_URL", None)
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "quarterly_report.py"), "--db", str(db)],
                         capture_output=True, text=True, encoding="utf-8", env=env, check=True).stdout
    for head in ("== 1. Радар стиля ==", "== 2. Звания и рейтинг", "== 3. Начисления", "== 4. «Цели недели»",
                 "== 5. Ачивки", "== 6. Пинги", "== 7. Запуск приложения"):
        assert head in out
    assert "Звания: Джун" in out and "Инфляция:" in out and "На полу 900/1000 сейчас: u" in out
    assert "Полных недель с играми ещё нет" in out or "закрыто целиком" in out
    assert "Не получил никто:" in out and "Событий: 1, сообщений: 2" in out
    assert "Запусков: 3; медиана 4.0 с; от 5 с: 1 (50%); не запустилось: 1" in out
    assert not any(n in out for n in ("Алиса", "Боб", "Вика", "Гоша"))
    assert db.stat().st_mtime_ns == before


def test_goals_lines_count_offered_and_done():
    import quarterly_report as qr

    from bot.webapp import api_return

    a, b = Player(id=1, display_name="A"), Player(id=2, display_name="B")
    monday = datetime(2026, 11, 1, 21, 0)                       # пн 02.11 по МСК
    def m(day, winner):
        return Match(challenger_id=1, challenged_id=2, status=MatchStatus.completed, winner_id=winner,
                     sets_data=[{"w": 11, "l": 4}, {"w": 11, "l": 6}], rating_change=8.0,
                     completed_at=monday + timedelta(days=day, hours=12))
    week = [m(0, 1), m(1, 1), m(2, 1), m(3, 2)]                    # у A 3 победы и поражение
    by_player = {1: week, 2: week}
    lines = qr.goals_lines([a, b], by_player, {1: "A", 2: "B"}, monday, monday + timedelta(weeks=1))
    assert lines[0].startswith("Недель-игроков: 2;")
    assert any(line.startswith("matches: выпадала 2, закрыта 2") for line in lines)   # 4 матча при цели 3
    assert api_return.week_start(monday) == monday
    assert qr.goals_lines([a, b], by_player, {}, monday, monday) == ["Полных недель с играми ещё нет"]


async def test_checkup_reminder_goes_to_admin_only(monkeypatch):
    bot = AsyncMock()
    monkeypatch.setenv("ADMIN_ID", "777")
    await sched.send_checkup_reminder(bot)
    bot.send_message.assert_awaited_once()
    args = bot.send_message.await_args.args
    assert args[0] == 777 and args[1] == sched.CHECKUP_TEXT
    assert "scripts/quarterly_report.py" in sched.CHECKUP_TEXT and "&amp;&amp;" in sched.CHECKUP_TEXT
    monkeypatch.setenv("ADMIN_ID", "")
    bot2 = AsyncMock()
    await sched.send_checkup_reminder(bot2)
    bot2.send_message.assert_not_called()


def test_checkup_job_every_quarter_on_15th():
    s = sched.setup_scheduler(AsyncMock())
    job = s.get_job("quarterly_checkup")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["month"] == "1,4,7,10" and fields["day"] == "15" and fields["hour"] == "10"
    assert str(job.trigger.timezone) == "Europe/Moscow"
