import json
import math
import os
import random
import urllib.parse
from datetime import datetime, timedelta, timezone
from html import escape as h

from aiogram import Bot
from sqlalchemy import and_, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import ChampionReign, Match, MatchStatus, Player

MSK_OFFSET = timedelta(hours=3)

# Порог новичок/ветеран, переиспользуется и для рейтингового пола (match_result.py),
# и для права на босс-файт (get_challenger, ниже) — здесь, чтобы оба места
# не тянули друг друга и не заводили циклический импорт.
NEWCOMER_THRESHOLD = 15


def env_int(name: str, default: int = 0) -> int:
    """Безопасно читает целочисленную переменную окружения.

    int(os.getenv(name, "0")) падает с ValueError, если переменная задана,
    но пустая (ADMIN_ID=) или содержит мусор — getenv возвращает "" вместо
    дефолта. Здесь любое некорректное значение тихо превращается в default.
    """
    raw = os.getenv(name, "")
    try:
        return int(raw.strip())
    except ValueError:
        return default


def msk_day_start() -> datetime:
    """Начало текущего дня по МСК в naive-UTC (как хранятся даты в БД).

    Единая граница «сегодня» для экрана Сегодня, итогов дня и пасхалок —
    иначе день считался то по UTC (с 03:00 МСК), то по МСК.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    msk_midnight = (now + MSK_OFFSET).replace(hour=0, minute=0, second=0, microsecond=0)
    return msk_midnight - MSK_OFFSET


def as_naive(dt: datetime) -> datetime:
    """Приводит datetime к naive (как хранятся все даты в БД).

    Защита от tz-aware объектов, которые изредка проскакивают — без неё
    арифметика с MSK_OFFSET/timedelta падает TypeError на миксе aware/naive.
    """
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def msk_hour_and_weekday(dt: datetime) -> tuple[int, int]:
    """Час и день недели (0=Пн) момента dt по МСК.

    Общий хелпер для realtime-проверок ачивок (achievements.py), их бэкфилла
    и пасхалок по времени (match_result.py) — раньше каждое место считало это
    независимо, с расхождением в защите от tz-aware дат.
    """
    msk = as_naive(dt) + MSK_OFFSET
    return msk.hour, msk.weekday()


def rating_tenths(rating: float) -> int:
    """rating × 10, округлённое до целого.

    Общая защита от погрешности float при проверке уже округлённого до 1
    знака рейтинга на кратность/равенство конкретному значению (ачивки
    round_hundred/rock_bottom, пасхалка «Ровно N» в match_result.py).
    """
    return round(rating * 10)


def _match_line(m: Match, player_id: int) -> str:
    """Форматирует одну строку матча для истории/статистики/дайджеста.

    Формат: иконка  дд.мм  vs Имя  счёт партий  (дельта)
    Счёт всегда показывается с перспективы player_id.
    """
    opponent = m.challenged if m.challenger_id == player_id else m.challenger
    is_draw = m.winner_id is None
    won = m.winner_id == player_id
    i_am_challenger = m.challenger_id == player_id
    icon = "🤝" if is_draw else ("✅" if won else "❌")
    date_str = m.completed_at.strftime("%d.%m") if m.completed_at else ""

    sets_str = ""
    if m.sets_data:
        parts = []
        for s in m.sets_data:
            if won or (is_draw and i_am_challenger):
                parts.append(f"{s['w']}:{s['l']}")
            else:
                parts.append(f"{s['l']}:{s['w']}")
        sets_str = "  " + ", ".join(parts)

    delta_str = ""
    if m.rating_change is not None:
        if is_draw:
            d = m.rating_change if i_am_challenger else -m.rating_change
            delta_str = f"  <i>({'+' if d >= 0 else ''}{d})</i>"
        elif won:
            delta_str = f"  <i>(+{m.rating_change})</i>"
        else:
            delta_str = f"  <i>(-{m.rating_change})</i>"

    return f"{icon} {date_str} vs {h(opponent.display_name)}{sets_str}{delta_str}"


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение по числу: 1 → one, 2-4 → few, 5+/11-14 → many."""
    if 11 <= n % 100 <= 14:
        word = many
    else:
        r = n % 10
        if r == 1:
            word = one
        elif 2 <= r <= 4:
            word = few
        else:
            word = many
    return f"{n} {word}"


def pluralize_matches(n: int) -> str:
    """1 матч / 2 матча / 5 матчей"""
    return _ru_plural(n, "матч", "матча", "матчей")


def pluralize_sets(n: int) -> str:
    """1 партия / 2 партии / 5 партий"""
    return _ru_plural(n, "партия", "партии", "партий")


def pluralize_days(n: int) -> str:
    """1 день / 2 дня / 5 дней"""
    return _ru_plural(n, "день", "дня", "дней")


def pluralize_wins(n: int) -> str:
    """1 победа / 2 победы / 5 побед"""
    return _ru_plural(n, "победа", "победы", "побед")


def pluralize_defenses(n: int) -> str:
    """1 защита / 2 защиты / 5 защит"""
    return _ru_plural(n, "защита", "защиты", "защит")


def pluralize_losses(n: int) -> str:
    """1 поражение / 2 поражения / 5 поражений"""
    return _ru_plural(n, "поражение", "поражения", "поражений")


def pluralize_times(n: int) -> str:
    """1 раз / 2 раза / 5 раз"""
    return _ru_plural(n, "раз", "раза", "раз")


# ── Звания по диапазону рейтинга (косметика, IT-тематика) ─────────────────────
# Чисто декоративная строка в статистике/профиле — не влияет ни на рейтинг, ни
# на матчмейкинг, ни на боссфайт. Пороги подняты на +100 в v2.103.0 — на
# исходных границах (<950/…/1250+) уже 2 из 7 игроков клуба были «Ген дир»,
# а новичок с рейтингом 1000 (старт с нуля) сразу оказывался «Миддлом». С
# новыми границами звание «Ген дир» держит только реальный лидер клуба, а
# новичок стартует «Джуном» — рейтинг 1000 < 1050.

RANK_TITLE_BANDS = [
    (1050, "Джун"),
    (1150, "Миддл"),
    (1250, "Сеньор"),
    (1350, "Тим лид"),
]
RANK_TITLE_TOP = "Ген дир"


def rank_title(rating: float) -> str:
    """IT-звание по диапазону рейтинга: джун → миддл → сеньор → тим лид → ген дир."""
    for threshold, title in RANK_TITLE_BANDS:
        if rating < threshold:
            return title
    return RANK_TITLE_TOP


def pluralize_points(n: int) -> str:
    """1 очко / 2 очка / 5 очков"""
    return _ru_plural(n, "очко", "очка", "очков")


async def get_player(session: AsyncSession, telegram_id: int) -> Player | None:
    r = await session.execute(select(Player).where(Player.telegram_id == telegram_id))
    return r.scalar_one_or_none()


async def get_career_matches(
    session: AsyncSession, player_id: int, *, with_opponents: bool = False,
) -> list[Match]:
    """Все завершённые матчи игрока за карьеру, desc(completed_at).

    Общий источник карьерной истории — раньше запрашивался независимо тем же
    паттерном в profile.py (дважды) и в еженедельном дайджесте (scheduler.py).

    with_opponents=True догружает Match.challenger/challenged (selectinload) —
    нужно там, где сразу читается opponent.display_name (profile.py). В
    scheduler.py не нужно: все игроки клуба уже в identity map сессии к этому
    моменту (полный select(Player) чуть выше), лишнего похода в БД не будет.
    """
    stmt = (
        select(Match)
        .where(
            or_(Match.challenger_id == player_id, Match.challenged_id == player_id),
            Match.status == MatchStatus.completed,
        )
        .order_by(desc(Match.completed_at))
    )
    if with_opponents:
        stmt = stmt.options(selectinload(Match.challenger), selectinload(Match.challenged))
    r = await session.execute(stmt)
    return r.scalars().all()


async def get_h2h_matches(
    session: AsyncSession, id_a: int, id_b: int, exclude_match_id: int | None = None,
) -> list[Match]:
    """Завершённые матчи между id_a и id_b, desc(completed_at) — свежие первыми.

    Единый источник h2h-истории после подтверждения матча: раньше запрашивался
    независимо в 3 местах за одно подтверждение результата (check_win_achievements
    для revenge/no_rest_win, _collect_egg_context и _send_quick_rematch_egg
    в match_result.py) — теперь считается один раз и переиспользуется.
    """
    conditions = [
        Match.status == MatchStatus.completed,
        or_(
            and_(Match.challenger_id == id_a, Match.challenged_id == id_b),
            and_(Match.challenger_id == id_b, Match.challenged_id == id_a),
        ),
    ]
    if exclude_match_id is not None:
        conditions.append(Match.id != exclude_match_id)
    r = await session.execute(
        select(Match).where(*conditions).order_by(desc(Match.completed_at))
    )
    return r.scalars().all()


async def get_active_match(session: AsyncSession, player_id: int) -> Match | None:
    """Текущий активный (accepted) матч игрока, если есть — с кем угодно.

    Единый источник правды для правила «только один активный матч
    одновременно» (стол один, матчи строго последовательные) — используется
    и при вызове (send_challenge), и везде, где нужно решить, показывать ли
    кнопку «Вызвать»/«⚔️» (профиль, H2H, «С кем сыграть?»), чтобы не вести
    игрока к заведомо тупиковому нажатию.
    """
    r = await session.execute(
        select(Match).where(
            or_(Match.challenger_id == player_id, Match.challenged_id == player_id),
            Match.status == MatchStatus.accepted,
        )
    )
    return r.scalars().first()


# ── Босс-файт: чемпион и претендент ───────────────────────────────────────────
# Чемпион (Player.is_champion) — единственный источник правды для места #1:
# нельзя занять по очкам, только выиграв босс-файт (или получив трон при
# авто-освобождении, см. scheduler.py). Если чемпион не назначен ни у кого —
# фича полностью неактивна (аварийный выключатель, см. bootstrap_champion).


async def get_champion(session: AsyncSession) -> Player | None:
    """Текущий чемпион (владелец 1-го места), либо None если фича не активирована."""
    r = await session.execute(select(Player).where(Player.is_champion == True))  # noqa: E712
    return r.scalar_one_or_none()


def _challenger_among(candidates: list[Player], champion: Player, match_counts: dict[int, int]) -> Player | None:
    """Претендент среди уже загруженного списка игроков — та же логика, что и
    get_challenger(), но без похода в БД. Для мест, где players_all и
    match_counts уже на руках (профиль/статистика) — экономит повторный
    полный скан завершённых матчей на каждый просмотр экрана."""
    eligible = [
        p for p in candidates
        if p.id != champion.id and p.rating > champion.rating
        and match_counts.get(p.id, 0) >= NEWCOMER_THRESHOLD
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda p: (-p.rating, p.id))
    return eligible[0]


async def get_challenger(
    session: AsyncSession, champion: Player | None, match_counts: dict[int, int] | None = None,
) -> Player | None:
    """Претендент — игрок, обошедший чемпиона по очкам и имеющий право вызвать
    его на босс-файт. Условия: не чемпион, рейтинг строго выше чемпионского,
    ≥NEWCOMER_THRESHOLD завершённых матчей. При равенстве рейтинга — меньший id.

    None, если чемпиона нет ВООБЩЕ, либо если единственные кандидаты выше
    чемпиона по очкам не набрали порог матчей (претендент НЕ откатывается на
    следующего по рейтингу — право появляется только по достижении порога).

    match_counts — передать уже посчитанный словарь, если он есть у вызывающего
    (иначе считается заново полным сканом завершённых матчей).
    """
    if champion is None:
        return None
    counts = match_counts if match_counts is not None else await get_match_counts(session)
    r = await session.execute(
        select(Player).where(Player.id != champion.id, Player.rating > champion.rating)
    )
    return _challenger_among(r.scalars().all(), champion, counts)


async def _close_open_reign(session: AsyncSession, ended_at: datetime) -> None:
    """Закрывает текущее открытое правление (ended_at IS NULL), если оно есть."""
    r = await session.execute(select(ChampionReign).where(ChampionReign.ended_at.is_(None)))
    open_reign = r.scalar_one_or_none()
    if open_reign is not None:
        open_reign.ended_at = ended_at


async def _open_reign(session: AsyncSession, player_id: int, started_at: datetime) -> None:
    session.add(ChampionReign(player_id=player_id, started_at=started_at))


async def bootstrap_champion(session: AsyncSession) -> None:
    """Назначает чемпиона, если ещё никого нет и есть хотя бы 1 сыгранный матч.

    Фиксирует ТЕКУЩЕЕ положение (топ по рейтингу среди игравших), без порога
    NEWCOMER_THRESHOLD — это не передача власти через босс-файт, а разовая
    инициализация. Идемпотентна: если чемпион уже есть, ничего не делает.

    Если правление уже когда-либо было (есть хоть одна запись ChampionReign),
    но сейчас чемпиона нет — значит админ осознанно снял is_champion=0 руками
    (аварийный рубильник, см. CLAUDE.md). Автобутстрап тогда НЕ переизбирает
    чемпиона заново — иначе рубильник не переживал бы следующий деплой/рестарт
    (init_db вызывает эту функцию на каждом старте процесса).

    Апгрейд с версии до ChampionReign (v2.85.0): если чемпион уже назначен
    старым кодом, а записи о правлении ещё ни одной нет — заводим её только
    сейчас (точный момент начала того правления неизвестен, это лучшее
    приближение). Без этого «Дольше всех лидировал» никогда бы не появился
    у клубов, где чемпион был назначен ещё до этого апдейта.

    Вызывается при старте (init_db), после _migrate_db.
    """
    existing = await get_champion(session)
    ever_r = await session.execute(select(ChampionReign.id).limit(1))
    reign_ever_existed = ever_r.first() is not None

    if existing is not None:
        if not reign_ever_existed:
            await _open_reign(session, existing.id, datetime.now(timezone.utc).replace(tzinfo=None))
            await session.commit()
        return
    if reign_ever_existed:
        return
    ever_r = await session.execute(select(ChampionReign.id).limit(1))
    if ever_r.first() is not None:
        return
    counts = await get_match_counts(session)
    if not counts:
        return
    players_r = await session.execute(select(Player))
    played = [p for p in players_r.scalars().all() if counts.get(p.id, 0) > 0]
    if not played:
        return
    top = max(played, key=lambda p: p.rating)
    top.is_champion = True
    await _open_reign(session, top.id, datetime.now(timezone.utc).replace(tzinfo=None))
    await session.commit()


async def try_transfer_champion(
    session: AsyncSession, from_id: int, to_id: int, at: datetime
) -> bool:
    """Атомарно переносит трон from_id → to_id (CAS: UPDATE ... WHERE is_champion=True
    AND id=from_id) — тот же паттерн, что и CAS-guard на Match.status в confirm_result,
    применённый к Player.is_champion. Без этого два независимых места, меняющих трон
    (авто-освобождение и исход босс-файта), могли гонкой оставить двух чемпионов сразу,
    и следующий get_champion() падал бы с MultipleResultsFound.

    Возвращает False, если from_id уже не чемпион — трон сменился где-то ещё между
    проверкой вызывающего и этим вызовом; тогда вызывающий не должен продолжать
    логику передачи (уведомления, ачивки и т.д.), исход того матча/джобы устарел.
    """
    guard = await session.execute(
        update(Player)
        .where(Player.id == from_id, Player.is_champion == True)  # noqa: E712
        .values(is_champion=False)
    )
    if guard.rowcount == 0:
        return False
    await session.execute(update(Player).where(Player.id == to_id).values(is_champion=True))
    await _close_open_reign(session, at)
    await _open_reign(session, to_id, at)
    return True


async def longest_champion_reign(session: AsyncSession) -> tuple[int, int] | None:
    """(player_id, дней) самого долгого правления на #1 — либо None, если правлений
    не было (фича ни разу не бутстрапилась). Текущее (незакрытое) правление
    считается по «сейчас»."""
    r = await session.execute(select(ChampionReign))
    reigns = r.scalars().all()
    if not reigns:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    best_days = -1
    best_player_id: int | None = None
    for reign in reigns:
        end = reign.ended_at or now
        days = (end - reign.started_at).days
        if days > best_days:
            best_days = days
            best_player_id = reign.player_id
    return (best_player_id, best_days) if best_player_id is not None else None


async def most_boss_fight_defenses(session: AsyncSession) -> tuple[int, int] | None:
    """(player_id, число побед) — больше всего успешных защит трона за ОДНО
    правление. Защита = боссфайт-победа чемпиона в рамках его ChampionReign;
    поражение в боссфайте по конструкции try_transfer_champion немедленно
    закрывает правление, поэтому все боссфайты чемпиона внутри его правления
    заведомо победы — отдельно сверять исход каждого не нужно.

    None, если правлений не было (фича ни разу не бутстрапилась) или ни в одном
    правлении не было ни одной защиты."""
    r = await session.execute(select(ChampionReign))
    reigns = r.scalars().all()
    if not reigns:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    best_count = 0
    best_player_id: int | None = None
    for reign in reigns:
        end = reign.ended_at or now
        cnt_r = await session.execute(
            select(func.count()).select_from(Match).where(
                Match.is_boss_fight == True,  # noqa: E712
                Match.status == MatchStatus.completed,
                or_(Match.challenger_id == reign.player_id, Match.challenged_id == reign.player_id),
                Match.completed_at >= reign.started_at,
                Match.completed_at <= end,
            )
        )
        cnt = cnt_r.scalar() or 0
        if cnt > best_count:
            best_count = cnt
            best_player_id = reign.player_id
    return (best_player_id, best_count) if best_player_id is not None else None


async def shortest_champion_reign(session: AsyncSession) -> tuple[int, int] | None:
    """(player_id, дней) самого короткого ЗАКРЫТОГО правления на #1 — антипод
    longest_champion_reign(). Только завершённые правления (ended_at не None) —
    текущее незакрытое ещё растёт, сравнивать его как «короткое» нечестно.
    None, если закрытых правлений нет."""
    r = await session.execute(select(ChampionReign).where(ChampionReign.ended_at.isnot(None)))
    reigns = r.scalars().all()
    if not reigns:
        return None
    best_days: int | None = None
    best_player_id: int | None = None
    for reign in reigns:
        days = (reign.ended_at - reign.started_at).days
        if best_days is None or days < best_days:
            best_days = days
            best_player_id = reign.player_id
    return (best_player_id, best_days) if best_player_id is not None else None


async def most_throne_ascensions(session: AsyncSession) -> tuple[int, int] | None:
    """(player_id, число правлений) — кто чаще всех ВОСХОДИЛ на трон: число
    отдельных периодов ChampionReign, не их длительность (тот же игрок может
    занимать трон несколько раз за карьеру, теряя и отвоёвывая его). None, если
    правлений не было, или ни у кого не было больше одного — единственное
    восхождение это не рекорд, а просто факт активации боссфайта."""
    r = await session.execute(select(ChampionReign))
    reigns = r.scalars().all()
    if not reigns:
        return None
    counts: dict[int, int] = {}
    for reign in reigns:
        counts[reign.player_id] = counts.get(reign.player_id, 0) + 1
    best_pid = max(counts, key=counts.get)
    return (best_pid, counts[best_pid]) if counts[best_pid] >= 2 else None


async def longest_awaited_revenge(session: AsyncSession) -> tuple[int, int, int] | None:
    """(avenger_id, opponent_id, дней) — самый большой разрыв по времени между
    поражением и следующей ПОБЕДОЙ над тем же соперником (не любым следующим
    матчем вообще — считаются только очные матчи этой пары, промежуточные
    матчи с другими соперниками разрыв не прерывают). Порог 7 дней — «на
    следующий день отыгрался» не тянет на «долгожданную», нужна пауза хотя бы
    в неделю. None, если ни разу не набралось порога."""
    r = await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed, Match.winner_id.isnot(None))
        .order_by(Match.completed_at)
    )
    matches = r.scalars().all()
    pair_matches: dict[tuple[int, int], list[Match]] = {}
    for m in matches:
        key = (min(m.challenger_id, m.challenged_id), max(m.challenger_id, m.challenged_id))
        pair_matches.setdefault(key, []).append(m)

    best: tuple[int, int, int] | None = None
    best_days = -1
    for (a_id, b_id), ms in pair_matches.items():
        last_loss_at: dict[int, datetime] = {}
        for m in ms:
            winner_id = m.winner_id
            loser_id = b_id if winner_id == a_id else a_id
            if winner_id in last_loss_at:
                gap_days = (m.completed_at - last_loss_at[winner_id]).days
                if gap_days > best_days:
                    best_days = gap_days
                    best = (winner_id, loser_id, gap_days)
                del last_loss_at[winner_id]
            last_loss_at[loser_id] = m.completed_at
    return best if best is not None and best_days >= 7 else None


async def steadiest_career(session: AsyncSession) -> tuple[int, float] | None:
    """(player_id, средняя волатильность рейтинга за матч) — наименьший разброс
    рейтинга за ВСЮ карьеру, антипод «Американских горок» (scheduler.py,
    _biggest_swing), но за карьеру целиком, а не за период, и нормировано на
    число матчей — иначе игрок с более короткой историей искусственно выглядел
    бы «стабильнее» просто за счёт меньшей выборки. Порог NEWCOMER_THRESHOLD
    матчей (та же планка, что и у права претендовать на трон) — отсекает
    слишком короткую выборку. None, если ни у кого нет достаточно матчей."""
    r = await session.execute(select(Match).where(Match.status == MatchStatus.completed))
    matches = r.scalars().all()
    net: dict[int, float] = {}
    abs_total: dict[int, float] = {}
    count: dict[int, int] = {}
    for m in matches:
        for pid in (m.challenger_id, m.challenged_id):
            d = match_rating_delta(m, pid)
            net[pid] = net.get(pid, 0.0) + d
            abs_total[pid] = abs_total.get(pid, 0.0) + abs(d)
            count[pid] = count.get(pid, 0) + 1
    eligible = {
        pid: (abs_total[pid] - abs(net.get(pid, 0.0))) / count[pid]
        for pid in abs_total if count[pid] >= NEWCOMER_THRESHOLD
    }
    if not eligible:
        return None
    best_pid = min(eligible, key=eligible.get)
    return (best_pid, round(eligible[best_pid], 1))


async def notify_all_players(bot: Bot, session: AsyncSession, text: str) -> None:
    """Рассылает text всем зарегистрированным игрокам, молча пропуская недоступных."""
    players_r = await session.execute(select(Player))
    for p in players_r.scalars().all():
        try:
            await bot.send_message(p.telegram_id, text)
        except Exception:
            pass


async def get_champion_and_challenger(session: AsyncSession) -> tuple[Player | None, Player | None]:
    """Чемпион + текущий претендент одним вызовом — сокращает повторяющийся код
    в местах, которым нужны оба сразу (лидерборд, экран вызова)."""
    champion = await get_champion(session)
    challenger = await get_challenger(session, champion)
    return champion, challenger


async def boss_fight_rematch_blocked(session: AsyncSession, id_a: int, id_b: int) -> bool:
    """После завершённого босс-файта между этими двумя пара не играет друг с
    другом, пока НЕЧЕМПИОН пары (сейчас, на момент проверки — не чемпион на
    момент создания того матча: роль переопределяется живьём, никаких новых
    полей) не завершит матч с ТРЕТЬИМ игроком. Матчи текущего чемпиона пары
    блок не снимают. Отменённый (declined) боссфайт блок не включает — он не
    попадает в выборку completed. Фича выключена (чемпиона нет) → блока нет.
    """
    r = await session.execute(
        select(Match)
        .where(
            Match.is_boss_fight == True,  # noqa: E712
            Match.status == MatchStatus.completed,
            or_(
                and_(Match.challenger_id == id_a, Match.challenged_id == id_b),
                and_(Match.challenger_id == id_b, Match.challenged_id == id_a),
            ),
        )
        .order_by(Match.completed_at.desc())
        .limit(1)
    )
    last_bf = r.scalar_one_or_none()
    if last_bf is None or not last_bf.completed_at:
        return False

    champion = await get_champion(session)
    if champion is None:
        return False
    if champion.id not in (id_a, id_b):
        return False
    non_champion_id = id_b if champion.id == id_a else id_a
    other_id = id_a if non_champion_id == id_b else id_b

    later_r = await session.execute(
        select(Match).where(
            or_(Match.challenger_id == non_champion_id, Match.challenged_id == non_champion_id),
            Match.status == MatchStatus.completed,
            Match.completed_at > last_bf.completed_at,
        )
    )
    for m in later_r.scalars().all():
        opp_id = m.challenged_id if m.challenger_id == non_champion_id else m.challenger_id
        if opp_id != other_id:
            return False  # нечемпион пары сыграл с третьим — блок снят
    return True


# ── Ранги игроков (единый источник правды) ───────────────────────────────────
# Лидерборд показывает только игроков с ≥1 сыгранным матчем. Чтобы «#N из M»
# совпадал на всех экранах (/start, профиль, статистика, список вызова, дайджест),
# ранг считается ОДНОЙ функцией среди игравших — игроки с 0 матчей вне рейтинга.


async def get_match_counts(session: AsyncSession) -> dict[int, int]:
    """Число завершённых матчей у каждого игрока: {player_id: count}."""
    r = await session.execute(
        select(Match.challenger_id, Match.challenged_id).where(
            Match.status == MatchStatus.completed
        )
    )
    counts: dict[int, int] = {}
    for a, b in r.all():
        counts[a] = counts.get(a, 0) + 1
        counts[b] = counts.get(b, 0) + 1
    return counts


def compute_ranks(
    players: list[Player],
    match_counts: dict[int, int],
    champion_id: int | None = None,
) -> dict[int, int]:
    """Ранг среди игравших (рейтинг по убыванию). {player_id: rank}, rank с 1.

    Игроки с 0 матчей в рейтинг-таблицу не входят (как на лидерборде) и в
    результат не попадают. Не путать с проверками «кто #1 по рейтингу» в ачивках
    и пасхалках — там сравнивается чистый рейтинг, а не место в таблице.

    champion_id — если задан и найден среди игравших, чемпион всегда получает
    ранг 1 (даже если чей-то чистый рейтинг выше — место #1 в боссфайте не
    занимается по очкам), остальные сортируются по рейтингу под ним. None
    (чемпион не назначен — фича выключена) даёт прежнее поведение: чистая
    сортировка по рейтингу.
    """
    played = [p for p in players if match_counts.get(p.id, 0) > 0]
    champion = next((p for p in played if p.id == champion_id), None) if champion_id else None
    if champion is not None:
        rest = sorted((p for p in played if p.id != champion_id), key=lambda p: -p.rating)
        ranked = [champion, *rest]
    else:
        ranked = sorted(played, key=lambda p: -p.rating)
    return {p.id: i + 1 for i, p in enumerate(ranked)}


def _pin_champion(ranked: list[Player], champion_id: int | None) -> list[Player]:
    """Ставит чемпиона первым в уже отсортированном списке, не трогая порядок
    остальных. Общая для лидерборда (текущий и «недельный» снапшот — иначе
    пиннинг применяется только к одному из них, и ▲▼ показывают ложное
    движение позиции у чемпиона и вытесненных им игроков) и списка вызова
    (challenge.py) — там список раньше сортировался чисто по рейтингу, из-за
    чего порядок строк расходился с показанным рангом #N (тот уже учитывал
    пиннинг через compute_ranks)."""
    if champion_id is not None and any(p.id == champion_id for p in ranked):
        champ_p = next(p for p in ranked if p.id == champion_id)
        rest = [p for p in ranked if p.id != champion_id]
        return [champ_p, *rest]
    return ranked


def format_rank(ranks: dict[int, int], player_id: int) -> str:
    """«#N из M» для игравшего, либо «вне рейтинга» для игрока с 0 матчей."""
    r = ranks.get(player_id)
    return f"#{r} из {len(ranks)}" if r else "вне рейтинга"


# ── «Матч дня» — индекс драмы ────────────────────────────────────────────────

DRAMA_THRESHOLD = 8.0   # минимальный балл, чтобы матч мог стать «матчем дня»


def match_drama_score(m: Match) -> float:
    """Балл «драматичности» матча. Чем выше — тем эпичнее.

    Факторы: длина (число партий), дьюсы (партии за 11), концовка впритык
    (разница в 1 партию), камбэк (победитель проиграл стартовую партию),
    значимость по дельте рейтинга (апсет).
    """
    sets = m.sets_data or []
    if not sets:
        return 0.0
    n = len(sets)
    score = n * 2.0
    deuces = sum(1 for s in sets if min(s["w"], s["l"]) >= 10)
    score += deuces * 3.0
    w_sets = sum(1 for s in sets if s["w"] > s["l"])
    l_sets = n - w_sets
    if n >= 3 and abs(w_sets - l_sets) == 1:
        score += 4.0
    # Камбэк: победитель проиграл первую партию (только для побед, не ничьих).
    # sets_data для побед хранится в перспективе победителя (w = очки победителя).
    if m.winner_id is not None and sets[0]["w"] < sets[0]["l"]:
        score += 5.0
    if m.rating_change:
        score += min(abs(m.rating_change), 30.0) * 0.2
    return round(score, 1)


def match_drama_reason(m: Match) -> str:
    """Короткая авто-подпись «почему этот матч эпичный»."""
    sets = m.sets_data or []
    n = len(sets)
    deuces = sum(1 for s in sets if min(s["w"], s["l"]) >= 10)
    w_sets = sum(1 for s in sets if s["w"] > s["l"])
    l_sets = n - w_sets
    comeback = m.winner_id is not None and bool(sets) and sets[0]["w"] < sets[0]["l"]

    reasons: list[str] = []
    if m.winner_id is None:
        reasons.append("ничья в равной борьбе")
    if n >= 5:
        reasons.append(f"марафон на {n} партий")
    if comeback:
        reasons.append("камбэк после проигранного старта")
    if deuces >= 1:
        reasons.append("дьюсы" if deuces > 1 else "дьюс на тоненького")
    if m.winner_id is not None and (m.rating_change or 0) >= 20:
        reasons.append("апсет — фаворит повержен")
    if m.winner_id is not None and n >= 3 and abs(w_sets - l_sets) == 1 and not reasons:
        reasons.append("решилось в последней партии")
    if not reasons:
        # ничего «драматичного» не сработало — победа всухую
        reasons.append("уверенный разгром" if l_sets == 0 else "напряжённый матч")

    text = ", ".join(reasons)
    return text[0].upper() + text[1:]


# ── Репортаж «плоского» матча (v2.105.0) ───────────────────────────────────────
# match_report() ниже раньше откатывался на match_drama_reason() целиком, когда
# ни один из 4 факторов драмы не сработал — но match_drama_reason() тоже часто
# падает на ОДНУ из двух совсем плоских строк («Уверенный разгром» / «Напряжённый
# матч»), которые на карточке победы выглядели скучно (жалоба пользователя на
# живом скрине). Остальные, более содержательные исходы match_drama_reason()
# («Дьюс на тоненького», «Решилось в последней партии» и т.д.) не трогаем —
# фразы-пула под них нет и не нужно, они и так информативны.
BLOWOUT_PHRASES = [
    "Ноль партий отдано — ноль шансов было",
    "Снёс на лёгкой сложности",
    "Разгромил вхламину",
    "Счёт говорит громче слов",
    "Одна нога здесь, другая уже в раздевалке",
    "GG уже после первой партии",
    "Это больше похоже на казнь, чем на матч",
]

PLAIN_WIN_PHRASES = [
    "Без шоу, но по делу",
    "Рабочая победа, ничего лишнего",
    "Победил без нервов",
    "Спокойно закрыли вопрос",
    "Без сюрпризов — но кто сказал, что это плохо",
    "Уверенно, без пафоса",
    "Никакой драмы — просто хорошая игра",
]

# Фразы-заготовки для драматичной ветки match_report (v2.118.0) — раньше на
# каждый фактор (камбэк/марафон/дьюс/апсет) была ровно ОДНА фраза без вариаций,
# в отличие от BLOWOUT/PLAIN_WIN_PHRASES выше. Индекс каждого пула — m.id СО
# СВОИМ смещением (см. match_report) — если бы все 4 пула индексировались
# одним и тем же m.id, они выпадали бы синхронно и реальных комбинаций было бы
# меньше, чем кажется.
COMEBACK_OPENERS = [
    "{name} влетел в яму 0:2 по партиям — казалось, разговор окончен. ",
    "{name} провалил старт 0:2, но сдаваться не входило в планы. ",
    "После 0:2 по партиям все уже мысленно попрощались с {name} — зря. ",
    "{name} умудрился отыграться с 0:2 — редкий случай настоящего камбэка. ",
    "0:2 — соперник почти дожал {name}, но матч на этом не закончился. ",
]

MARATHON_FRAGMENTS = [
    "дошло до пятой — тут все успели заскучать и снова заинтересоваться",
    "матч растянулся на все 5 партий — обе стороны возможно за*бались",
    "добрались до решающей пятой — марафон, а не матч",
    "потребовалось все 5 партий, чтобы выявить победителя",
]

DEUCE_FRAGMENTS = [
    "а решающая партия ушла на дьюс",
    "а решающая партия тянулась на дьюсе до последнего",
    "решающая партия — чистый дьюс, нервы напердели",
    "а концовка решающей партии — сплошной дьюс",
]

UPSET_FRAGMENT_TEMPLATES = [
    "и +{delta} pts апсета под занавес",
    "и жирный апсет на +{delta} pts в довесок",
    "а сверху ещё +{delta} pts апсета",
    "плюс неожиданные +{delta} pts апсета",
]


def match_report(m: Match, winner_name: str) -> str:
    """Расширенный «репортаж» для топ-матча периода (день/неделя/месяц/квартал/
    год) и карточки победы — абзац из фраз-кусочков по факторам драмы (камбэк
    с 0:2, марафон, дьюс на решающей, апсет), вместо короткой причины
    match_drama_reason().

    Если ни один из четырёх факторов не сработал — сначала пробует
    match_drama_reason (там может найтись что-то содержательное вроде «Дьюс
    на тоненького»/«Решилось в последней партии»); а если и та упала на ОДНУ
    из двух совсем плоских строк («Уверенный разгром»/«Напряжённый матч») —
    берёт фразу из BLOWOUT_PHRASES/PLAIN_WIN_PHRASES (индекс по m.id, как у
    match_phrase — стабильно в рамках матча, не «прыгает» при перерисовке).

    Если хотя бы один фактор сработал — открывающая фраза (камбэк) и каждый
    фрагмент хвоста (марафон/дьюс/апсет) берутся из СВОЕГО пула (v2.118.0,
    раньше — фиксированная фраза без вариаций на каждый фактор). Индекс —
    m.id со своим смещением на каждый пул, чтобы разные факторы одного матча
    не выпадали синхронно на одном и том же m.id.

    Камбэк математически всегда означает и марафон (нет более короткого пути
    отыграться с 0:2, кроме как дойдя минимум до 5 партий) — эти два фактора
    не независимы, но проверяются раздельно для ясности кода, а не потому,
    что комбинация «камбэк без марафона» реально бывает.

    winner_name передаётся явно, а не через relationship m.winner — та
    прогружена не во всех запросах дайджестов; вызывающий уже знает имя
    через свой собственный name_map.
    """
    sets = m.sets_data or []
    if not sets or m.winner_id is None:
        return match_drama_reason(m)

    comeback = len(sets) >= 2 and sets[0]["w"] < sets[0]["l"] and sets[1]["w"] < sets[1]["l"]
    marathon = len(sets) >= 5
    deuce_decider = min(sets[-1]["w"], sets[-1]["l"]) >= 10
    upset = (m.rating_change or 0) >= 20

    if not any((comeback, marathon, deuce_decider, upset)):
        reason = match_drama_reason(m)
        if reason in ("Уверенный разгром", "Напряжённый матч"):
            w_sets = sum(1 for s in sets if s["w"] > s["l"])
            l_sets = len(sets) - w_sets
            pool = BLOWOUT_PHRASES if l_sets == 0 else PLAIN_WIN_PHRASES
            return pool[m.id % len(pool)]
        return reason

    opener = ""
    if comeback:
        opener = COMEBACK_OPENERS[m.id % len(COMEBACK_OPENERS)].format(name=h(winner_name))

    tail_fragments = []
    if marathon:
        tail_fragments.append(MARATHON_FRAGMENTS[(m.id + 1) % len(MARATHON_FRAGMENTS)])
    if deuce_decider:
        tail_fragments.append(DEUCE_FRAGMENTS[(m.id + 2) % len(DEUCE_FRAGMENTS)])
    if upset:
        template = UPSET_FRAGMENT_TEMPLATES[(m.id + 3) % len(UPSET_FRAGMENT_TEMPLATES)]
        tail_fragments.append(template.format(delta=round(m.rating_change, 1)))

    tail = ""
    if tail_fragments:
        joined = ", ".join(tail_fragments)
        tail = joined[0].upper() + joined[1:] + "."

    return (opener + tail).strip()


# ── Контекст «прошлый матч с этим соперником» на карточке победы (v2.105.0) ────
# Три сценария по исходу ПРЕДЫДУЩЕГО (не текущего) очного матча между той же
# парой: реванш (в прошлый раз проиграл именно этому сопернику), продолжение
# серии (выиграл у него и в прошлый раз), была ничья. Если прошлых очных
# матчей нет вообще — строку не показываем (звать вызывающему код нет смысла).
# Индекс по match_id ТЕКУЩЕГО матча — тот же принцип стабильности, что у
# match_phrase (не «прыгает» при повторном показе одной и той же карточки).

H2H_REVENGE_PHRASES = [
    "В прошлый раз этот соперник задолжал — реванш взят",
    "Долг за прошлое поражение закрыт",
    "В прошлый раз он тебя вынес. Но в этот раз не фартануло",
    "Прошлый раз был его. Этот — твой",
    "Взял своё обратно",
    "Ответочка прилетела точно в цель",
]

H2H_STREAK_PHRASES = [
    "Уверенно нанёс очередное поражение сопернику",
    "Второй раз подряд — уже не совпадение",
    "Красиво продолжил победы",
    "Стабильность — признак мастерства",
    "Опять по накатанной",
    "Уже не в первый раз для него это заканчивается вот так",
]

H2H_DRAW_PHRASES = [
    "В прошлый раз закончили вничью — сегодня без компромиссов",
    "Ничья в прошлый раз намекала на реванш. Вот и он",
    "Ничейка в прошлый раз и победа в этот",
]


def previous_h2h_line(prev_match: Match, winner_id: int, match_id: int) -> str:
    """Строка контекста по исходу ПРЕДЫДУЩЕГО очного матча между той же парой.

    prev_match — предыдущий матч (не текущий), например h2h_matches[0] из
    get_h2h_matches(session, winner_id, loser_id, exclude_match_id=текущий).
    match_id — id ТЕКУЩЕГО матча, для стабильного индекса в пуле.
    """
    if prev_match.winner_id is None:
        pool = H2H_DRAW_PHRASES
    elif prev_match.winner_id == winner_id:
        pool = H2H_STREAK_PHRASES
    else:
        pool = H2H_REVENGE_PHRASES
    return pool[match_id % len(pool)]


def pick_match_of_day(matches: list[Match]) -> Match | None:
    """Выбирает самый драматичный матч из списка. None — если все слишком тривиальны."""
    scored = [(match_drama_score(m), m) for m in matches]
    scored = [(s, m) for s, m in scored if s >= DRAMA_THRESHOLD]
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1].completed_at or datetime.min), reverse=True)
    return scored[0][1]


def match_score_challenger_first(m: Match) -> str:
    """Счёт партий в перспективе challenger'а: 'challenger:challenged, ...'."""
    sets = m.sets_data or []
    if not sets:
        return ""
    # Победа: sets хранятся в перспективе победителя; ничья — в перспективе challenger.
    if m.winner_id is None or m.winner_id == m.challenger_id:
        return ", ".join(f"{s['w']}:{s['l']}" for s in sets)
    return ", ".join(f"{s['l']}:{s['w']}" for s in sets)


def _my_opp_points(m: Match, s: dict, viewer_id: int) -> tuple[int, int]:
    """Очки (мои, соперника) в партии s с перспективы viewer_id."""
    if m.winner_id is None:
        # ничья: sets хранятся в перспективе challenger
        if m.challenger_id == viewer_id:
            return s["w"], s["l"]
        return s["l"], s["w"]
    if m.winner_id == viewer_id:
        return s["w"], s["l"]
    return s["l"], s["w"]


def compute_h2h(matches: list[Match], viewer_id: int, opponent_id: int) -> dict:
    """Статистика личных встреч viewer против opponent.

    matches — завершённые матчи между этими двумя игроками,
    отсортированные по убыванию completed_at (свежие первыми).
    """
    wins = losses = draws = 0
    my_sets = opp_sets = 0
    rating_delta = 0.0
    best_win: float | None = None
    first_date = None

    for m in matches:
        if m.winner_id is None:
            draws += 1
        elif m.winner_id == viewer_id:
            wins += 1
        else:
            losses += 1

        for s in (m.sets_data or []):
            mp, op = _my_opp_points(m, s, viewer_id)
            if mp > op:
                my_sets += 1
            elif op > mp:
                opp_sets += 1

        d = match_rating_delta(m, viewer_id)
        rating_delta += d
        if m.winner_id == viewer_id and (best_win is None or d > best_win):
            best_win = d

        if m.completed_at and (first_date is None or m.completed_at < first_date):
            first_date = m.completed_at

    # Текущая серия в этом противостоянии (matches уже desc по дате)
    streak_desc: str | None = None
    if matches:
        latest = matches[0]
        if latest.winner_id == viewer_id:
            n = 0
            for m in matches:
                if m.winner_id == viewer_id:
                    n += 1
                else:
                    break
            if n >= 2:
                streak_desc = f"ты ведёшь — {n} побед подряд"
        elif latest.winner_id is not None:
            n = 0
            for m in matches:
                if m.winner_id is not None and m.winner_id != viewer_id:
                    n += 1
                else:
                    break
            if n >= 2:
                streak_desc = f"ты проигрываешь — {n} подряд"

    return {
        "total": len(matches),
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "my_sets": my_sets,
        "opp_sets": opp_sets,
        "rating_delta": round(rating_delta, 1),
        "best_win": round(best_win, 1) if best_win is not None else None,
        "first_date": first_date,
        "streak_desc": streak_desc,
    }


def favor_icon(rating_diff: float) -> str:
    """Иконка сложности соперника по разнице рейтинга (opponent - viewer).

    Общий хелпер для списка вызова (players_list_kb) и «С кем сыграть?»
    (show_my_matches) — раньше на втором экране показывались сырые "+123 pts"
    без перевода в понятный вид, тогда как первый экран уже переводил ту же
    разницу в понятные иконки. Единая логика убирает расхождение между экранами.

    5 уровней (v2.117.0, было 3) — 35 pts как и раньше отделяет «примерно
    равны» от заметной разницы, 120 pts (примерно один диапазон IT-звания,
    см. RANK_TITLE_BANDS) — от откровенно разного уровня.
    """
    if rating_diff > 120:
        return "💀 "
    if rating_diff > 35:
        return "💪 "
    if rating_diff < -120:
        return "🤣 "
    if rating_diff < -35:
        return "😊 "
    return "⚡ "


# ── Разнообразие текста на экране вызова (v2.117.0) ─────────────────────────────
# Чисто косметика — по просьбе пользователя, чтобы кнопка/заголовок не
# примелькались за месяцы использования. Пулы, а не одна случайная строка на
# всё приложение — рендерятся заново при каждом показе экрана/клавиатуры.

CHALLENGE_BUTTON_LABELS = [
    "🏓 Вызвать на матч",
    "🏓 Сыграть партейку",
    "🏓 Отдохнуть от работы",
    "🏓 Перекурить",
    "🏓 Настучать по шарам",
    "🏓 Помахать ракеткой",
    "🏓 Неистово сразиться",
]

CHALLENGE_HEADER_GREETINGS = [
    "Кого хочешь вызвать? 🏓",
    "🎮 Choose your destiny",
    "Кого сегодня вынесем? 🏓",
    "С кем будем биться? 🏓",
]


def random_challenge_button_label() -> str:
    return random.choice(CHALLENGE_BUTTON_LABELS)


def random_challenge_greeting() -> str:
    return random.choice(CHALLENGE_HEADER_GREETINGS)


def get_rec_signal(
    viewer_rating: float,
    viewer_id: int,
    opponent_rating: float,
    opponent_id: int,
    h2h: list,
    now: datetime,
) -> str:
    """Сигнал-рекомендация для соперника на экране 'С кем сыграть?'.

    h2h: завершённые матчи viewer vs opponent, desc по completed_at.
    Приоритет: серия поражений ≥ 2 → последний матч проигран → давно не играли (3+ дн.)
    → соперник сильнее на 30+ pts → нет сигнала.
    """
    if not h2h:
        return "ещё не встречались"

    streak = 0
    for m in h2h:
        if m.winner_id == opponent_id:
            streak += 1
        else:
            break

    if streak >= 2:
        return f"серия поражений — {streak} подряд"

    if h2h[0].winner_id == opponent_id:
        return "ты проиграл последний матч"

    if h2h[0].completed_at:
        days = (now - h2h[0].completed_at).days
        if days >= 3:
            return f"не играли {pluralize_days(days)}"

    if opponent_rating - viewer_rating >= 30:
        return f"он сильнее на +{int(opponent_rating - viewer_rating)}"

    return ""


def compute_alltime_streak(matches_asc: list, player_id: int) -> int:
    """Максимальная серия побед игрока за всё время. matches_asc — от старых к новым."""
    best = cur = 0
    for m in matches_asc:
        if m.winner_id == player_id:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


def match_rating_delta(match: Match, player_id: int) -> float:
    """Возвращает изменение рейтинга игрока в матче (+ или -).

    Для ничьей rating_change хранит challenger_delta (знаковый).
    Для победы/поражения rating_change всегда положительный.
    """
    if match.rating_change is None:
        return 0.0
    if match.winner_id is None:
        # ничья
        return match.rating_change if match.challenger_id == player_id else -match.rating_change
    return match.rating_change if match.winner_id == player_id else -match.rating_change


# ── Мемная фраза под прогнозом на экране матча ────────────────────────────────
# Подбирается по шансам СМОТРЯЩЕГО: фаворит (>65%), равны (35–65%), андердог (<35%).
# Стабильна в рамках матча (индекс по match.id), чтобы не «прыгала» при перерисовке.

FAVORITE_PHRASES = [
    "Изи катка.",
    "Difficulty: Easy.",
    "Хьюстон, у соперника проблемы.",
    "Это будет быстро и безболезненно. Почти.",
    "Готовься страдать.",
    "Расходимся, тут всё ясно.",
    "Это будет короткий разговор.",
    "Размажу как масло по хлебу.",
    "Ферзём по пешке.",
    "Мне даже неинтересно.",
    "Это не матч, а формальность.",
    "По классике. 2:0",
    "Того рот топтал.",
    "Зипда ему.",
    "Это будет избиение.",
]

EVEN_PHRASES = [
    "Это будет легендарно.",
    "Fight!",
    "Ставки сделаны, ставок больше нет.",
    "Пристегните ремни — будет жарко.",
    "Победит достойнейший.",
    "Надо собраться!",
    "Да пребудет с тобой сила.",
    "Не ссы. Но соберись.",
    "Пан или пропан",
    "Один мяч решит всё.",
    "Да начнётся бойня.",
    "Это будет красиво.",
    "Ни шагу назад.",
]

UNDERDOG_PHRASES = [
    "Это. Спарта!",
    "I'll be back.",
    "Чудес не бывает? Сейчас проверим.",
    "Терять нечего — это и есть свобода.",
    "Главное — ввязаться в драку, а там видно будет.",
    "Падал, но вставал.",
    "Ты не пройдёшь!",
    "Надо собраться, бл*ть!",
    "Не время умирать.",
    "Рискну. Чем чёрт не шутит.",
    "Сейчас или никогда.",
    "Соберись, тряпка!",
    "Главное не обосраться.",
    "Абать ты смелый.",
]


def match_phrase(win_chance: int, match_id: int) -> str:
    """Мемная фраза под прогнозом. win_chance — шанс смотрящего (0–100)."""
    if win_chance > 65:
        pool = FAVORITE_PHRASES
    elif win_chance < 35:
        pool = UNDERDOG_PHRASES
    else:
        pool = EVEN_PHRASES
    return pool[match_id % len(pool)]


# ── График рейтинга (quickchart.io) ───────────────────────────────────────────

CHART_MAX_POINTS = 40  # сколько последних матчей показывать на графике


def build_rating_series(
    matches: list[Match], player_id: int, current_rating: float, limit: int = CHART_MAX_POINTS
) -> tuple[list[str], list[float]]:
    """Строит ряд рейтинга игрока для графика.

    matches — завершённые матчи игрока с rating_change, отсортированные по
    completed_at (старые первыми). Возвращает (labels, values), где values[i] —
    рейтинг ПОСЛЕ матча i. Ряд восстанавливается НАЗАД от current_rating через
    дельты: последняя точка точно равна текущему рейтингу, недавние точки точны.
    Пол рейтинга (1000/900) при откате не учитывается — это приближение, как и в
    ▲▼ лидерборда; для давних точек возможен небольшой дрейф.
    """
    recent = list(matches[-limit:]) if limit else list(matches)
    n = len(recent)
    values = [0.0] * n
    post = current_rating
    for i in range(n - 1, -1, -1):
        values[i] = round(post, 1)
        post -= match_rating_delta(recent[i], player_id)
    labels = [
        (m.completed_at.strftime("%d.%m") if m.completed_at else str(i + 1))
        for i, m in enumerate(recent)
    ]
    return labels, values


_AXIS_STEPS = (25, 50, 100, 200, 250, 500, 1000, 2000)
_AXIS_REFERENCE_RATING = 1000.0  # стартовый/новичковый пол — ориентир на графике


def _rating_axis_bounds(values: list[float]) -> tuple[float, float, float]:
    """Подбирает (min, max, stepSize) для оси Y графика рейтинга.

    Без фиксированной границы 0..1400 весь реальный разброс игрока (обычно
    900..1400) сжимался в верхнюю треть графика — динамика была не видна.
    Граница считается от фактических значений ряда с отступом, а не жёстко
    (900..1400 может вырасти со временем — рейтинг не ограничен сверху).
    Опорный рейтинг 1000.0 всегда включён в диапазон, чтобы пунктирная
    линия-ориентир была видна даже у игрока, который всегда ниже/выше неё.
    """
    pts = [*values, _AXIS_REFERENCE_RATING]
    lo, hi = min(pts), max(pts)
    pad = max(50.0, (hi - lo) * 0.15)
    lo_padded, hi_padded = lo - pad, hi + pad

    span = hi_padded - lo_padded
    step = next((s for s in _AXIS_STEPS if span / s <= 8), _AXIS_STEPS[-1])

    axis_min = math.floor(lo_padded / step) * step
    axis_max = math.ceil(hi_padded / step) * step
    return axis_min, axis_max, step


def rating_chart_url(name: str, labels: list[str], values: list[float]) -> str:
    """Формирует URL картинки графика рейтинга через quickchart.io.

    Картинку скачивает сам Telegram при send_photo(photo=url) — собственный
    HTTP-клиент не нужен. Конфиг — Chart.js (line), компактный JSON в query.
    """
    axis_min, axis_max, step = _rating_axis_bounds(values)
    config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "Рейтинг",
                    "data": values,
                    "borderColor": "rgb(54,162,235)",
                    "backgroundColor": "rgba(54,162,235,0.15)",
                    "fill": True,
                    "tension": 0.3,
                    "pointRadius": 2,
                },
                {
                    "label": "Старт",
                    "data": [_AXIS_REFERENCE_RATING] * len(values),
                    "borderColor": "rgba(150,150,150,0.7)",
                    "borderDash": [6, 4],
                    "fill": False,
                    "pointRadius": 0,
                },
            ],
        },
        "options": {
            "title": {"display": True, "text": f"Рейтинг — {name}"},
            "legend": {"display": False},
            "scales": {
                "yAxes": [
                    {"ticks": {"min": axis_min, "max": axis_max, "stepSize": step}}
                ]
            },
        },
    }
    encoded = urllib.parse.quote(json.dumps(config, separators=(",", ":"), ensure_ascii=False))
    return f"https://quickchart.io/chart?w=700&h=420&bkg=white&c={encoded}"


# ── Тепловая карта активности (quickchart.io) ─────────────────────────────────
# v2.107.0. Окно 90 дней, не год — у клуба несколько месяцев реальной истории
# (на сентябрь 2026), full-year сетка была бы наполовину пустой.
#
# quickchart.io НЕ поддерживает тип графика "matrix"/"heatmap" на публичном
# инстансе (проверено вручную: "Chart error: 'matrix' is not a registered
# controller") — сетка эмулируется через стандартный bubble-chart: x = номер
# недели, y = день недели (0=Вс снизу..6=Пн сверху), цвет точки = интенсивность.
# Радиус фиксирован (задаётся один раз на датасет, не на каждую точку) — тот же
# принцип, что у GitHub-графика контрибуций (цвет — сигнал, не размер).
#
# 4 датасета по тиру интенсивности (не backgroundColor на каждую точку) —
# ощутимо короче итоговый URL при идентичном визуальном результате: цвет
# повторяется 4 раза вместо N (N = число дней в окне).

HEATMAP_DAYS = 90

_HEATMAP_TIER_COLORS = [
    "rgba(230,230,230,1)",  # 0 матчей — ⬜
    "rgba(255,205,30,1)",   # 1        — 🟨
    "rgba(255,149,20,1)",   # 2–3      — 🟧
    "rgba(224,49,49,1)",    # 4+       — 🟥
]


def _heatmap_tier(count: int) -> int:
    if count == 0:
        return 0
    if count == 1:
        return 1
    if count <= 3:
        return 2
    return 3


def activity_counts_by_day(matches: list[Match]) -> dict:
    """Число матчей по дням (МСК) — общий хелпер для личной и клубной карты
    активности. Ключи — datetime.date."""
    counts: dict = {}
    for m in matches:
        if not m.completed_at:
            continue
        day = (as_naive(m.completed_at) + MSK_OFFSET).date()
        counts[day] = counts.get(day, 0) + 1
    return counts


def activity_heatmap_url(title: str, counts: dict, days: int = HEATMAP_DAYS) -> str:
    """Тепловая карта активности за последние `days` дней (см. общий комментарий
    к разделу выше про технику отрисовки через bubble-chart)."""
    today = (datetime.now(timezone.utc).replace(tzinfo=None) + MSK_OFFSET).date()
    start = today - timedelta(days=days - 1)
    start_monday = start - timedelta(days=start.weekday())  # выравниваем колонки недель

    tiers: list[list[dict]] = [[], [], [], []]
    d = start_monday
    week = 0
    while d <= today:
        dow = d.weekday()  # 0=Пн..6=Вс
        if d >= start:
            tiers[_heatmap_tier(counts.get(d, 0))].append({"x": week, "y": 6 - dow})
        if dow == 6:
            week += 1
        d += timedelta(days=1)

    datasets = [
        {"data": tiers[i], "backgroundColor": _HEATMAP_TIER_COLORS[i], "radius": 9}
        for i in range(4) if tiers[i]
    ]
    config = {
        "type": "bubble",
        "data": {"datasets": datasets},
        "options": {
            "title": {"display": True, "text": title},
            "legend": {"display": False},
            "scales": {
                "xAxes": [{"ticks": {"display": False}, "gridLines": {"display": False}}],
                "yAxes": [{"ticks": {"display": False, "min": -0.5, "max": 6.5}, "gridLines": {"display": False}}],
            },
        },
    }
    encoded = urllib.parse.quote(json.dumps(config, separators=(",", ":"), ensure_ascii=False))
    return f"https://quickchart.io/chart?w=700&h=220&bkg=white&c={encoded}"
