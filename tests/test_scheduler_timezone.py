"""
Регрессия на таймзону планировщика.

CronTrigger без явного timezone берёт ЛОКАЛЬНУЮ tz сервера, а не timezone
самого AsyncIOScheduler. На сервере в зоне Europe/Amsterdam расписание уезжало
на 2 часа (итоги дня в 19:30 вместо 21:30 МСК). Эти тесты фиксируют, что каждая
cron-задача срабатывает в нужный момент по UTC — независимо от локальной tz,
на которой гоняется CI (на GitHub Actions это UTC, поэтому баг там не всплывал).

Запуск: pytest tests/test_scheduler_timezone.py
"""
from datetime import datetime, timezone

from bot.scheduler import setup_scheduler


def _trigger(job_id: str):
    scheduler = setup_scheduler(bot=object())
    for job in scheduler.get_jobs():
        if job.id == job_id:
            return job.trigger
    raise AssertionError(f"job {job_id} not found")


def _next_utc(job_id: str, after: datetime) -> datetime:
    trig = _trigger(job_id)
    nxt = trig.get_next_fire_time(None, after)
    return nxt.astimezone(timezone.utc)


# Пятница 2026-06-19 00:00 UTC — точка отсчёта
REF = datetime(2026, 6, 19, 0, 0, tzinfo=timezone.utc)


def test_daily_summary_2130_msk():
    """Итоги дня: 21:30 МСК = 18:30 UTC (МСК без перехода на летнее время, +3)."""
    nxt = _next_utc("daily_summary", REF)
    assert (nxt.hour, nxt.minute) == (18, 30)
    assert nxt.date() == REF.date()


def test_weekly_digest_mon_0900_msk():
    """Недельный дайджест: пн 9:00 МСК = 6:00 UTC. Ближайший пн — 2026-06-22."""
    nxt = _next_utc("weekly_digest", REF)
    assert (nxt.hour, nxt.minute) == (6, 0)
    assert nxt.weekday() == 0  # понедельник
    assert nxt.date() == datetime(2026, 6, 22).date()


def test_db_backup_mon_0930_msk():
    """Offsite-бэкап: пн 9:30 МСК = 6:30 UTC."""
    nxt = _next_utc("db_backup", REF)
    assert (nxt.hour, nxt.minute) == (6, 30)
    assert nxt.weekday() == 0


def test_monthly_summary_1st_1000_msk():
    """Итоги месяца: 1-го числа 10:00 МСК = 7:00 UTC. Ближайшее 1-е — 2026-07-01."""
    nxt = _next_utc("monthly_summary", REF)
    assert (nxt.hour, nxt.minute) == (7, 0)
    assert nxt.day == 1
    assert nxt.date() == datetime(2026, 7, 1).date()
