"""
Данные таблицы «Рейтинг клуба» — одно определение для экрана бота и для
Mini App (v2.160.0). Раньше весь расчёт жил внутри хендлера
`_build_leaderboard_screen` вперемешку с HTML; Mini App нужны те же места,
значки и ▲▼ за неделю, поэтому расчёт вынесен сюда, а бот и веб-часть только
по-разному рисуют одни и те же строки.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, MatchStatus, Player
from bot.utils import _pin_champion, get_champion_and_challenger, get_mvp_of_month


@dataclass(frozen=True)
class LeaderboardRow:
    rank: int
    player_id: int
    name: str
    rating: float
    matches: int
    wins: int
    win_rate: int          # процент побед, целое (как на экране бота)
    streak: int            # текущая серия побед подряд
    week_change: int       # >0 — поднялся на N мест за неделю, <0 — опустился
    is_champion: bool
    is_challenger: bool
    is_mvp: bool
    inactive: bool         # не играл 7+ дней


async def compute_leaderboard(session: AsyncSession) -> list[LeaderboardRow]:
    """Игравшие по местам: чемпион закреплён на #1 (место не занимается по
    очкам), остальные по рейтингу; игроки без матчей не входят."""
    players = (await session.execute(select(Player).order_by(desc(Player.rating)))).scalars().all()
    if not players:
        return []

    all_matches = (await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed)
        .order_by(desc(Match.completed_at))
    )).scalars().all()

    match_count: dict[int, int] = {}
    win_count: dict[int, int] = {}
    player_matches: dict[int, list] = {}
    for m in all_matches:
        for pid in (m.challenger_id, m.challenged_id):
            match_count[pid] = match_count.get(pid, 0) + 1
            player_matches.setdefault(pid, []).append(m)
        if m.winner_id:
            win_count[m.winner_id] = win_count.get(m.winner_id, 0) + 1

    streak_map: dict[int, int] = {}
    for pid, ms in player_matches.items():
        s = 0
        for m in ms:
            if m.winner_id == pid:
                s += 1
            else:
                break
        streak_map[pid] = s

    played = [p for p in players if match_count.get(p.id, 0) > 0]
    if not played:
        return []

    champion, challenger_player = await get_champion_and_challenger(session)
    champion_id = champion.id if champion else None
    challenger_id = challenger_player.id if challenger_player else None
    mvp_id = await get_mvp_of_month(session)

    ordered = _pin_champion(sorted(played, key=lambda p: -p.rating), champion_id)

    week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    active_7day: set[int] = {
        pid
        for m in all_matches
        if m.completed_at and m.completed_at >= week_ago
        for pid in (m.challenger_id, m.challenged_id)
    }

    # Изменение позиции за неделю: рейтинги «неделю назад» восстанавливаются
    # откатом дельт матчей за 7 дней. Пол рейтинга при откате игнорируется —
    # это приблизительный индикатор.
    snap = {p.id: p.rating for p in ordered}
    for m in all_matches:
        if not (m.completed_at and m.completed_at >= week_ago) or m.rating_change is None:
            continue
        d = m.rating_change
        if m.winner_id is None:
            snap[m.challenger_id] = round(snap.get(m.challenger_id, 1000.0) - d, 1)
            snap[m.challenged_id] = round(snap.get(m.challenged_id, 1000.0) + d, 1)
        else:
            wid = m.winner_id
            lid = m.challenged_id if wid == m.challenger_id else m.challenger_id
            snap[wid] = round(snap.get(wid, 1000.0) - d, 1)
            snap[lid] = round(snap.get(lid, 1000.0) + d, 1)

    old_count: dict[int, int] = {}
    for m in all_matches:
        if m.completed_at and m.completed_at < week_ago:
            for pid in (m.challenger_id, m.challenged_id):
                old_count[pid] = old_count.get(pid, 0) + 1

    prev_order = _pin_champion(
        sorted(ordered, key=lambda p: (old_count.get(p.id, 0) == 0, -snap.get(p.id, p.rating))),
        champion_id,
    )
    prev_pos = {p.id: i for i, p in enumerate(prev_order)}

    rows = []
    for i, p in enumerate(ordered):
        count = match_count.get(p.id, 0)
        wins = win_count.get(p.id, 0)
        rows.append(LeaderboardRow(
            rank=i + 1,
            player_id=p.id,
            name=p.display_name,
            rating=round(p.rating, 1),
            matches=count,
            wins=wins,
            win_rate=int(wins / count * 100) if count else 0,
            streak=streak_map.get(p.id, 0),
            week_change=prev_pos.get(p.id, i) - i,
            is_champion=champion_id is not None and p.id == champion_id,
            is_challenger=challenger_id is not None and p.id == challenger_id,
            is_mvp=mvp_id is not None and p.id == mvp_id,
            inactive=p.id not in active_7day,
        ))
    return rows
