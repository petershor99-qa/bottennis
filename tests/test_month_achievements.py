"""«Ачивки месяца» в итогах месяца (v2.149.0)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import bot.scheduler as sched
from bot.db.models import AchievementEarned
from bot.services.achievements import ACHIEVEMENTS_LIST, ACHIEVEMENTS_MAP
from tests.conftest import _completed, _player

START = datetime(2026, 9, 1, 0, 0, 0)
END = datetime(2026, 10, 1, 0, 0, 0)
IDS = [a.id for a in ACHIEVEMENTS_LIST[:6]]


def _earned(player_id: int, ach_id: str, when) -> AchievementEarned:
    return AchievementEarned(player_id=player_id, achievement_id=ach_id, earned_at=when)


async def _players(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    return p1, p2


async def test_line_none_without_achievements(db):
    p1, p2 = await _players(db)
    line = await sched._month_achievements_line(db, START, END, {p1.id: "Alice", p2.id: "Bob"})
    assert line is None


async def test_line_lists_only_achievements_inside_the_window(db):
    p1, p2 = await _players(db)
    db.add_all([
        _earned(p1.id, IDS[0], datetime(2026, 9, 10)),               # внутри
        _earned(p1.id, IDS[1], datetime(2026, 8, 31, 23, 59)),       # до окна
        _earned(p2.id, IDS[2], datetime(2026, 10, 1, 0, 0)),         # конец окна не включается
        _earned(p2.id, IDS[3], None),                                # дата неизвестна — не показываем
    ])
    await db.commit()
    line = await sched._month_achievements_line(db, START, END, {p1.id: "Alice", p2.id: "Bob"})
    assert line is not None
    assert line.startswith("🏅 Ачивки месяца: ")
    assert f"«{ACHIEVEMENTS_MAP[IDS[0]].name}»" in line
    assert ACHIEVEMENTS_MAP[IDS[1]].name not in line
    assert "Bob" not in line


async def test_line_caps_per_player_and_orders_by_count_then_name(db):
    p1, p2 = await _players(db)
    for i, aid in enumerate(IDS[:5]):             # у Alice 5 ачивок
        db.add(_earned(p1.id, aid, datetime(2026, 9, 2 + i)))
    db.add(_earned(p2.id, IDS[5], datetime(2026, 9, 3)))   # у Bob 1
    await db.commit()
    line = await sched._month_achievements_line(db, START, END, {p1.id: "Alice", p2.id: "Bob"})
    assert line.index("Alice") < line.index("Bob")          # у кого больше — первым
    assert "(+2)" in line                                   # 5 - 3
    alice_part = line.split("; ")[0]
    assert alice_part.count("«") == sched.MONTH_ACHIEVEMENTS_PER_PLAYER
    # показаны первые по времени получения
    assert ACHIEVEMENTS_MAP[IDS[0]].name in alice_part
    assert ACHIEVEMENTS_MAP[IDS[4]].name not in alice_part


async def test_line_escapes_player_names_and_skips_unknown_ids(db):
    p1, _ = await _players(db)
    db.add_all([
        _earned(p1.id, IDS[0], datetime(2026, 9, 5)),
        _earned(p1.id, "no_such_achievement", datetime(2026, 9, 6)),
    ])
    await db.commit()
    line = await sched._month_achievements_line(db, START, END, {p1.id: "<i>Alice</i>"})
    assert "&lt;i&gt;Alice&lt;/i&gt;" in line
    assert "no_such_achievement" not in line


async def test_monthly_summary_includes_line_only_when_achievements_exist(monkeypatch, db_factory):
    monkeypatch.setattr(sched, "async_session", db_factory)
    msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + sched.MSK_OFFSET
    month_end = msk_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start = (month_end - timedelta(days=1)).replace(day=1)
    inside = (month_start + timedelta(days=5)) - sched.MSK_OFFSET

    async with db_factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(_completed(p1, p2, p1.id, 10.0, inside))
        await s.commit()

    bot = AsyncMock()
    await sched.send_monthly_summary(bot)
    assert "Ачивки месяца" not in bot.send_message.call_args_list[0].args[1]

    async with db_factory() as s:
        s.add(_earned(1, IDS[0], inside))
        await s.commit()
    bot = AsyncMock()
    await sched.send_monthly_summary(bot)
    text = bot.send_message.call_args_list[0].args[1]
    assert "Ачивки месяца" in text
    assert ACHIEVEMENTS_MAP[IDS[0]].name in text
