"""
Пинги «Рекорд клуба» (v2.157.0) — короткое сообщение игрокам, когда побит
один из трёх РЕДКИХ рекордов клуба. Остальные рекорды остаются только на
экране «Рекорды клуба»: пинг по каждому рекорду превратился бы в спам.

Как это работает: `snapshot_club_records()` снимает значения рекордов ДО матча и
ПОСЛЕ, `detect_record_breaks()` (чистая функция) сравнивает снимки по порогам,
`ping_text()` собирает текст. Антиспам-журнал — `ClubRecordPing`. Рассылку и
журнал делает `send_record_pings()` (вызывается из `confirm_result`).

Пороги подобраны по истории боевой БД (2026-05…10): простое «побит рекорд»
сработало бы ~20 раз за пять месяцев, а рекорд пика рейтинга — после почти
каждой победы лидера.

Рекорд «самый долгий матч по времени» сознательно НЕ пингуется: у матча нет
момента начала игры (accepted_at — это принятие вызова, до игры может пройти
сколько угодно), поэтому «длительность» на деле измеряет ожидание.
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
    most_boss_fight_defenses,
    pluralize_matches,
    pluralize_times,
    safe_send,
)

logger = logging.getLogger(__name__)

KIND_PEAK = "peak"
KIND_STREAK = "streak"
KIND_DEFENSE = "defense"
KINDS = (KIND_PEAK, KIND_STREAK, KIND_DEFENSE)

PEAK_STEP = 100               # пинг, когда рекорд пика пересёк отметку 1500, 1600, …
STREAK_MIN = 5                # серия короче — не событие
DEFENSE_MIN = 3               # защит трона подряд
PING_COOLDOWN_DAYS = 7


@dataclass(frozen=True)
class RecordValue:
    """Текущий рекорд: кто держит и значение."""
    holder_id: int
    value: float


@dataclass(frozen=True)
class RecordBreak:
    kind: str
    holder_id: int
    value: float
    prev_holder_id: int
    prev_value: float


Snapshot = dict[str, RecordValue | None]


def best_win_streak(matches_asc: Sequence[Match]) -> RecordValue | None:
    """Самая длинная серия побед в клубе (как рекорд «Лучшая серия побед»).
    Один проход по матчам от старых к новым; при равенстве рекорд остаётся у того,
    кто достиг значения первым (стабильно, не зависит от порядка обхода игроков).
    Ничья и поражение обрывают серию."""
    current: dict[int, int] = {}
    best: RecordValue | None = None
    for m in matches_asc:
        for pid in (m.challenger_id, m.challenged_id):
            if m.winner_id == pid:
                current[pid] = current.get(pid, 0) + 1
                if best is None or current[pid] > best.value:
                    best = RecordValue(pid, float(current[pid]))
            else:
                current[pid] = 0
    return best


async def snapshot_club_records(session: AsyncSession, exclude_match_id: int | None = None) -> Snapshot:
    """Значения рекордов клуба прямо сейчас. `exclude_match_id` — матч, который
    нужно не учитывать (для снимка «до»: CAS уже перевёл его в completed)."""
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
    }


def detect_record_breaks(before: Snapshot, after: Snapshot) -> list[RecordBreak]:
    """Какие из рекордов побиты матчем и заслуживают пинга. Рекорд без прошлого
    значения молчит (первое измерение — не побитие)."""
    breaks: list[RecordBreak] = []
    for kind in KINDS:
        old, new = before.get(kind), after.get(kind)
        if old is None or new is None or old.value <= 0 or new.value <= old.value:
            continue
        if kind == KIND_PEAK and int(new.value // PEAK_STEP) <= int(old.value // PEAK_STEP):
            continue
        if kind == KIND_STREAK and new.value < STREAK_MIN:
            continue
        if kind == KIND_DEFENSE and new.value < DEFENSE_MIN:
            continue
        breaks.append(RecordBreak(kind, new.holder_id, new.value, old.holder_id, old.value))
    return breaks


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
    return (
        f"🛡 Рекорд клуба: <b>{holder}</b> защитил трон {pluralize_times(int(brk.value))} подряд. "
        f"Дольше никто не держал. "
        f"Прошлый рекорд: {prev_holder}, {int(brk.prev_value)}."
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
    """Сравнивает снимок «до» с текущим состоянием и рассылает пинги игрокам клуба
    (с хотя бы одним завершённым матчем — как и в рейтинге, пустые регистрации
    не получают). Возвращает число отправленных пингов. Любая ошибка здесь не
    должна ломать внесение результата — вызывающий оборачивает в try/except.

    Правило антиспама намеренно простое: один игрок по одному рекорду не чаще
    раза в `PING_COOLDOWN_DAYS` дней, даже если рекорд за это время вырос
    (серия 12, 13, 14 подряд — одно сообщение, а не три)."""
    after = await snapshot_club_records(session)
    breaks = detect_record_breaks(before, after)
    if not breaks:
        return 0

    players_r = await session.execute(select(Player))
    players = players_r.scalars().all()
    names = {p.id: p.display_name for p in players}
    active_r = await session.execute(
        select(Match.challenger_id, Match.challenged_id).where(Match.status == MatchStatus.completed)
    )
    active_ids = {pid for pair in active_r.all() for pid in pair}
    recipients = [p for p in players if p.id in active_ids]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    sent = 0
    for brk in breaks:
        if await _on_cooldown(session, brk.kind, brk.holder_id, now):
            continue
        session.add(ClubRecordPing(kind=brk.kind, player_id=brk.holder_id, value=brk.value, pinged_at=now))
        await session.commit()
        text = ping_text(brk, names)
        for p in recipients:
            await safe_send(bot, p.telegram_id, text)
        sent += 1
    return sent
