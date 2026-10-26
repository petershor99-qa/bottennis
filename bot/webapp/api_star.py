"""
«Игрок недели» (v2.214.0): значок на аватаре в рейтинге и ленте. Берётся последняя
закончившаяся неделя (пн–вс по МСК): больше всего набранного рейтинга, не меньше
STAR_MIN_MATCHES матчей и только в плюс; при равенстве — больше побед. Значок виден
с понедельника до следующего воскресенья; хранения нет, считается на лету.
"""
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from bot.utils import MSK_OFFSET, match_rating_delta
from bot.webapp.api import _signed
from bot.webapp.api_club import _now
from bot.webapp.api_day import _matches_between

STAR_MIN_MATCHES = 3


def pick_star(matches: list) -> tuple[int, float] | None:
    """Игрок недели по матчам недели: (id, прирост) или None."""
    played: dict[int, int] = {}
    delta: dict[int, float] = {}
    wins: dict[int, int] = {}
    for m in matches:
        for pid in (m.challenger_id, m.challenged_id):
            played[pid] = played.get(pid, 0) + 1
            delta[pid] = delta.get(pid, 0.0) + match_rating_delta(m, pid)
            wins[pid] = wins.get(pid, 0) + (m.winner_id == pid)
    candidates = [pid for pid in played if played[pid] >= STAR_MIN_MATCHES and round(delta[pid], 1) > 0]
    if not candidates:
        return None
    best = max(candidates, key=lambda pid: (round(delta[pid], 1), wins[pid], -pid))
    return best, delta[best]


async def week_star(session: AsyncSession) -> dict | None:
    today = (_now() + MSK_OFFSET).date()
    monday = today - timedelta(days=today.weekday())
    start = datetime(monday.year, monday.month, monday.day) - MSK_OFFSET - timedelta(days=7)
    end = datetime(monday.year, monday.month, monday.day) - MSK_OFFSET
    star = pick_star(await _matches_between(session, start, end))
    if star is None:
        return None
    return {"id": star[0], "delta": _signed(round(star[1], 1))}
