"""Джобы планировщика получают запас на опоздание (v2.215.0): дефолт APScheduler
misfire_grace_time=1 с молча пропускает cron-задачу, если event loop был занят
дольше секунды в момент срабатывания — так терялись итоги дня.

Проверяем job_defaults планировщика — источник, из которого add_job берёт
значения для каждой джобы (у pending-джоб до старта слоты не читаются).
APScheduler запинен ==3.10.4, приватный атрибут стабилен."""
from aiogram import Bot

from bot.scheduler import setup_scheduler


def test_scheduler_job_defaults_give_misfire_grace():
    scheduler = setup_scheduler(Bot(token="1:test"))
    defaults = scheduler._job_defaults
    assert defaults["misfire_grace_time"] >= 300
    assert defaults["coalesce"] is True
    assert scheduler.get_jobs(), "ожидаем cron/interval-джобы от setup_scheduler"
