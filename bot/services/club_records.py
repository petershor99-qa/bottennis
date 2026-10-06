"""
Пинги «Рекорд клуба» (v2.157.0) — короткое сообщение всем игрокам, когда побит
один из четырёх РЕДКИХ рекордов клуба. Остальные рекорды остаются только на
экране «Рекорды клуба»: пинг по каждому рекорду превратился бы в спам.

Как это работает: `snapshot_club_records()` снимает значения четырёх рекордов
ДО матча и ПОСЛЕ, `detect_record_breaks()` (чистая функция) сравнивает снимки по
порогам, `ping_text()` собирает текст. Антиспам-журнал — `ClubRecordPing`.
Рассылку и журнал делает `send_record_pings()` (вызывается из `confirm_result`).

Пороги подобраны по истории боевой БД (2026-05…10): простое «побит рекорд»
сработало бы ~20 раз за пять месяцев, а рекорд пика рейтинга — после почти
каждой победы лидера.
"""
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape as h

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import ClubRecordPing, Match, MatchStatus, Player
from bot.utils import (
    _ru_plural,
    compute_alltime_streak,
    most_boss_fight_defenses,
    pluralize_matches,
    pluralize_times,
    safe_send,
)

logger = logging.getLogger(__name__)

KIND_PEAK = "peak"
KIND_STREAK = "streak"
KIND_DEFENSE = "defense"
KIND_DURATION = "duration"

PEAK_STEP = 100               # пинг, когда рекорд пика пересёк отметку 1500, 1600, …
STREAK_MIN = 5                # серия короче — не событие
DEFENSE_MIN = 3               # защит трона подряд
DURATION_MIN_MINUTES = 30     # обычный матч — 10–20 минут
DURATION_MAX_MINUTES = 120    # дольше — забытый ввод счёта, а не игра
PING_COOLDOWN_DAYS = 7


@dataclass(frozen=True)
class RecordValue:
    """Текущий рекорд: кто держит и значение. `extra` — id игроков матча для
    рекорда по времени (держатель — challenger, второй участник — extra)."""
    holder_id: int
    value: float
    extra: int | None = None


@dataclass(frozen=True)
class RecordBreak:
    kind: str
    holder_id: int
    value: float
    extra: int | None
    prev_holder_id: int
    prev_value: float


Snapshot = dict[str, RecordValue | None]


def best_win_streak(matches_asc: Sequence[Match]) -> RecordValue | None:
    """Самая длинная серия побед в клубе (как рекорд «Лучшая серия побед»)."""
    player_ids = {pid for m in matches_asc for pid in (m.challenger_id, m.challenged_id)}
    best: RecordValue | None = None
    for pid in player_ids:
        own = [m for m in matches_asc if pid in (m.challenger_id, m.challenged_id)]
        n = compute_alltime_streak(own, pid)
        if n > 0 and (best is None or n > best.value):
            best = RecordValue(pid, float(n))
    return best


def match_minutes(match: Match) -> float | None:
    """Длительность матча (принятие вызова → внесение результата) в минутах;
    None, если дат нет или длительность не похожа на реальную игру."""
    if not match.accepted_at or not match.completed_at:
        return None
    minutes = (match.completed_at - match.accepted_at).total_seconds() / 60
    if minutes <= 0 or minutes > DURATION_MAX_MINUTES:
        return None
    return minutes


def longest_match(matches: Sequence[Match]) -> RecordValue | None:
    best: RecordValue | None = None
    for m in matches:
        minutes = match_minutes(m)
        if minutes is not None and (best is None or minutes > best.value):
            best = RecordValue(m.challenger_id, minutes, m.challenged_id)
    return best


async def snapshot_club_records(session: AsyncSession, exclude_match_id: int | None = None) -> Snapshot:
    """Значения четырёх рекордов клуба прямо сейчас. `exclude_match_id` — матч,
    который нужно не учитывать (для снимка «до»: CAS уже перевёл его в completed)."""
    matches_r = await session.execute(
        select(Match).where(Match.status == MatchStatus.completed).order_by(Match.completed_at, Match.id)
    )
    matches = [m for m in matches_r.scalars().all() if m.id != exclude_match_id and m.completed_at]

    players_r = await session.execute(select(Player))
    peak: RecordValue | None = None
    for p in players_r.scalars().all():
        value = p.peak_rating if p.peak_rating is not None else p.rating
        if peak is None or value > peak.value:
            peak = RecordValue(p.id, float(value))

    defenses = await most_boss_fight_defenses(session)
    return {
        KIND_PEAK: peak,
        KIND_STREAK: best_win_streak(matches),
        KIND_DEFENSE: RecordValue(defenses[0], float(defenses[1])) if defenses and defenses[1] > 0 else None,
        KIND_DURATION: longest_match(matches),
    }


def detect_record_breaks(before: Snapshot, after: Snapshot) -> list[RecordBreak]:
    """Какие из рекордов побиты матчем и заслуживают пинга. Рекорд без прошлого
    значения молчит (первое измерение — не побитие)."""
    breaks: list[RecordBreak] = []
    for kind in (KIND_PEAK, KIND_STREAK, KIND_DEFENSE, KIND_DURATION):
        old, new = before.get(kind), after.get(kind)
        if old is None or new is None or old.value <= 0 or new.value <= old.value:
            continue
        if kind == KIND_PEAK and int(new.value // PEAK_STEP) <= int(old.value // PEAK_STEP):
            continue
        if kind == KIND_STREAK and new.value < STREAK_MIN:
            continue
        if kind == KIND_DEFENSE and new.value < DEFENSE_MIN:
            continue
        if kind == KIND_DURATION and (new.value < DURATION_MIN_MINUTES or new.value - old.value < 1):
            continue
        breaks.append(RecordBreak(kind, new.holder_id, new.value, new.extra, old.holder_id, old.value))
    return breaks


def _minutes(value: float) -> str:
    return _ru_plural(int(value), "минута", "минуты", "минут")


def ping_text(brk: RecordBreak, names: dict[int, str]) -> str:
    holder = h(names.get(brk.holder_id, "?"))
    prev_holder = h(names.get(brk.prev_holder_id, "?"))
    if brk.kind == KIND_PEAK:
        return (
            f"🏔 Рекорд клуба: <b>{holder}</b> дошёл до {round(brk.value, 1)}. "
            f"Выше в истории клуба никто не поднимался. "
            f"Прошлый рекорд: {prev_holder}, {round(brk.prev_value, 1)}."
        )
    if brk.kind == KIND_STREAK:
        return (
            f"🔥 Рекорд клуба: <b>{holder}</b> выиграл {pluralize_matches(int(brk.value))} подряд. "
            f"Длиннее серии в истории клуба не было. "
            f"Прошлый рекорд: {prev_holder}, {int(brk.prev_value)}."
        )
    if brk.kind == KIND_DEFENSE:
        return (
            f"🛡 Рекорд клуба: <b>{holder}</b> защитил трон {pluralize_times(int(brk.value))} подряд. "
            f"Дольше никто не держал. "
            f"Прошлый рекорд: {prev_holder}, {int(brk.prev_value)}."
        )
    opponent = h(names.get(brk.extra, "?")) if brk.extra is not None else "?"
    return (
        f"⏱ Рекорд клуба: <b>{holder}</b> vs <b>{opponent}</b>, {_minutes(brk.value)}. "
        f"Дольше матча в клубе не было. "
        f"Прошлый рекорд: {_minutes(brk.prev_value)}."
    )


async def _on_cooldown(session: AsyncSession, kind: str, player_id: int, now: datetime) -> bool:
    r = await session.execute(
        select(ClubRecordPing.id).where(
            ClubRecordPing.kind == kind,
            ClubRecordPing.player_id == player_id,
            ClubRecordPing.pinged_at >= now - timedelta(days=PING_COOLDOWN_DAYS),
        ).limit(1)
    )
    return r.first() is not None


async def send_record_pings(session: AsyncSession, bot: Bot, before: Snapshot) -> int:
    """Сравнивает снимок «до» с текущим состоянием и рассылает пинги всем игрокам.
    Возвращает число отправленных пингов. Любая ошибка здесь не должна ломать
    внесение результата — вызывающий оборачивает в try/except."""
    after = await snapshot_club_records(session)
    breaks = detect_record_breaks(before, after)
    if not breaks:
        return 0

    players_r = await session.execute(select(Player))
    players = players_r.scalars().all()
    names = {p.id: p.display_name for p in players}
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    sent = 0
    for brk in breaks:
        if await _on_cooldown(session, brk.kind, brk.holder_id, now):
            continue
        session.add(ClubRecordPing(kind=brk.kind, player_id=brk.holder_id, value=brk.value, pinged_at=now))
        await session.commit()
        text = ping_text(brk, names)
        for p in players:
            await safe_send(bot, p.telegram_id, text)
        sent += 1
    return sent
