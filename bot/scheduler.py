import logging
import os
from datetime import datetime, timedelta, timezone
from html import escape as h

from aiogram import Bot
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import selectinload

from bot.db.database import DATABASE_URL, async_session
from bot.db.models import Match, MatchStatus, Player
from bot.keyboards.inline import active_match_kb
from bot.utils import (
    MSK_OFFSET,
    compute_alltime_streak,
    env_int,
    match_drama_reason,
    match_rating_delta,
    match_score_challenger_first,
    msk_day_start,
    pick_match_of_day,
    pluralize_matches,
    pluralize_sets,
    pluralize_wins,
)

logger = logging.getLogger(__name__)


# ── Общие блоки для итогов недели/месяца ──────────────────────────────────────

def _most_played_pair(matches: list, name_map: dict) -> str | None:
    """«Чаще всего самбовались» — самая играющая пара за период (от 2 матчей)."""
    pair: dict[tuple[int, int], int] = {}
    for m in matches:
        key = (min(m.challenger_id, m.challenged_id), max(m.challenger_id, m.challenged_id))
        pair[key] = pair.get(key, 0) + 1
    if not pair:
        return None
    (a, b), n = max(pair.items(), key=lambda kv: kv[1])
    if n < 2:
        return None
    return (
        f"🤼 Чаще всего самбовались — <b>{h(name_map.get(a, '?'))}</b> vs "
        f"<b>{h(name_map.get(b, '?'))}</b>: {pluralize_matches(n)}"
    )


def _longest_streak(matches: list, name_map: dict, period: str) -> str | None:
    """«Нагибатель периода» — самая длинная серия побед внутри периода (от 2)."""
    by_player: dict[int, list] = {}
    for m in sorted(matches, key=lambda m: m.completed_at or datetime.min):
        for pid in (m.challenger_id, m.challenged_id):
            by_player.setdefault(pid, []).append(m)
    best_pid, best_n = None, 0
    for pid, ms in by_player.items():
        n = compute_alltime_streak(ms, pid)
        if n > best_n:
            best_n, best_pid = n, pid
    if best_pid is None or best_n < 2:
        return None
    return (
        f"🔥 Нагибатель {period} — <b>{h(name_map.get(best_pid, '?'))}</b>: "
        f"{pluralize_wins(best_n)} подряд"
    )


# ── Напоминание о незавершённых матчах ────────────────────────────────────────

async def send_match_reminders(bot: Bot) -> None:
    """Раз в час ищет принятые матчи старше 24 часов и напоминает игрокам."""
    async with async_session() as session:
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)

        result = await session.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.accepted,
                Match.reminder_sent == False,  # noqa: E712
                # accepted_at для новых записей, created_at как fallback для старых
                or_(
                    and_(Match.accepted_at.isnot(None), Match.accepted_at <= threshold),
                    and_(Match.accepted_at.is_(None), Match.created_at <= threshold),
                ),
            )
            .options(selectinload(Match.challenger), selectinload(Match.challenged))
        )
        matches = result.scalars().all()

        for match in matches:
            for player, opponent in [
                (match.challenger, match.challenged),
                (match.challenged, match.challenger),
            ]:
                try:
                    await bot.send_message(
                        player.telegram_id,
                        f"⏰ <b>Напоминание о матче</b>\n\n"
                        f"У тебя с <b>{h(opponent.display_name)}</b> есть незавершённый матч "
                        f"уже больше 24 часов.\n"
                        f"Сыграйте и внесите результат! 🏓\n\n"
                        f"<i>Напиши счёт прямо сюда — например: <code>11:7 9:11 11:5</code></i>",
                        reply_markup=active_match_kb(match.id),
                    )
                except Exception:
                    pass

            match.reminder_sent = True

        if matches:
            await session.commit()
            logger.info("Отправлено напоминаний: %d", len(matches))


# ── Еженедельный дайджест ─────────────────────────────────────────────────────


async def send_weekly_digest(bot: Bot) -> None:
    """Каждый понедельник в 9:00 МСК отправляет игрокам итоги недели."""
    async with async_session() as session:
        week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        two_weeks_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=14)

        players_result = await session.execute(select(Player))
        players = players_result.scalars().all()

        # Ранжирование по рейтингу
        sorted_players = sorted(players, key=lambda p: p.rating, reverse=True)
        rank_map = {p.id: i + 1 for i, p in enumerate(sorted_players)}
        player_name_map = {p.id: p.display_name for p in players}

        # ── Герои недели — агрегируем все матчи за неделю одним запросом ─────
        all_week_r = await session.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.completed,
                Match.completed_at >= week_ago,
            )
        )
        all_week_matches = all_week_r.scalars().all()

        if not all_week_matches:
            logger.info("Еженедельный дайджест: за неделю матчей не было, пропускаем")
            return

        # Матчи за позапрошлую неделю — для сравнения активности
        prev_week_r = await session.execute(
            select(func.count()).select_from(Match)
            .where(
                Match.status == MatchStatus.completed,
                Match.completed_at >= two_weeks_ago,
                Match.completed_at < week_ago,
            )
        )
        prev_week_count = prev_week_r.scalar()

        # ── Клубные агрегаты за неделю ─────────────────────────────────────────
        match_count: dict[int, int] = {}
        wins_count: dict[int, int] = {}
        losses_count: dict[int, int] = {}
        draws_count: dict[int, int] = {}
        delta_sum: dict[int, float] = {}
        for m in all_week_matches:
            for pid in (m.challenger_id, m.challenged_id):
                match_count[pid] = match_count.get(pid, 0) + 1
                delta_sum[pid] = delta_sum.get(pid, 0.0) + match_rating_delta(m, pid)
            if m.winner_id is None:
                draws_count[m.challenger_id] = draws_count.get(m.challenger_id, 0) + 1
                draws_count[m.challenged_id] = draws_count.get(m.challenged_id, 0) + 1
            else:
                wins_count[m.winner_id] = wins_count.get(m.winner_id, 0) + 1
                lid = m.challenged_id if m.winner_id == m.challenger_id else m.challenger_id
                losses_count[lid] = losses_count.get(lid, 0) + 1

        total_sets = sum(len(m.sets_data) if m.sets_data else 0 for m in all_week_matches)
        cur_count = len(all_week_matches)

        # ── Топ недели (клубный стендинг — компактно, вместо длинного списка матчей) ─
        medals = ["🥇", "🥈", "🥉"]
        standings = ["🏆 <b>Топ недели:</b>"]
        sorted_ids = sorted(
            match_count,
            key=lambda pid: (wins_count.get(pid, 0), match_count.get(pid, 0)),
            reverse=True,
        )
        for i, pid in enumerate(sorted_ids):
            prefix = medals[i] if i < 3 else f"{i + 1}."
            w = wins_count.get(pid, 0)
            lo = losses_count.get(pid, 0)
            d = draws_count.get(pid, 0)
            wr = int(w / match_count[pid] * 100) if match_count[pid] else 0
            draws_str = f"–{d}🤝" if d else ""
            standings.append(
                f"{prefix} <b>{h(player_name_map.get(pid, '?'))}</b> — "
                f"{w}–{lo}{draws_str}  <i>({wr}%)</i>"
            )

        # ── Герои недели ───────────────────────────────────────────────────────
        if prev_week_count > 0:
            diff = cur_count - prev_week_count
            diff_str = f"+{diff}" if diff >= 0 else str(diff)
            activity_line = (
                f"⚡ Сыграно за неделю: <b>{pluralize_matches(cur_count)}</b>, "
                f"<b>{pluralize_sets(total_sets)}</b>  <i>({diff_str} к прошлой)</i>"
            )
        else:
            activity_line = (
                f"⚡ Сыграно за неделю: <b>{pluralize_matches(cur_count)}</b>, "
                f"<b>{pluralize_sets(total_sets)}</b>"
            )

        hero_lines = ["🏆 <b>Герои недели:</b>", activity_line]

        most_active_id = max(match_count, key=match_count.get)
        hero_lines.append(
            f"🏅 Главный теннисист недели — <b>{h(player_name_map[most_active_id])}</b> "
            f"({pluralize_matches(match_count[most_active_id])})"
        )
        if wins_count:
            most_wins_id = max(wins_count, key=wins_count.get)
            hero_lines.append(
                f"🥇 Больше всех побед — <b>{h(player_name_map[most_wins_id])}</b> "
                f"({wins_count[most_wins_id]})"
            )
        best_gain_id = max(delta_sum, key=delta_sum.get)
        if delta_sum[best_gain_id] > 0:
            hero_lines.append(
                f"📈 Лучший рост — <b>{h(player_name_map[best_gain_id])}</b> "
                f"(+{round(delta_sum[best_gain_id], 1)} pts)"
            )
        worst_id = min(delta_sum, key=delta_sum.get)
        if delta_sum[worst_id] < 0:
            hero_lines.append(
                f"📉 Отрицательный рост — <b>{h(player_name_map[worst_id])}</b> "
                f"({round(delta_sum[worst_id], 1)} pts)"
            )
        derby = _most_played_pair(all_week_matches, player_name_map)
        if derby:
            hero_lines.append(derby)
        streak = _longest_streak(all_week_matches, player_name_map, "недели")
        if streak:
            hero_lines.append(streak)

        # ── Матч недели ────────────────────────────────────────────────────────
        match_week = ""
        mod = pick_match_of_day(all_week_matches)
        if mod:
            mch = player_name_map.get(mod.challenger_id, "?")
            mcd = player_name_map.get(mod.challenged_id, "?")
            match_week = (
                f"\n\n🌟 <b>Матч недели</b>\n"
                f"<b>{h(mch)}</b> vs <b>{h(mcd)}</b> — {match_score_challenger_first(mod)}\n"
                f"<i>{match_drama_reason(mod)}</i>"
            )

        club_block = "\n".join(standings) + "\n\n" + "\n".join(hero_lines) + match_week

        # ── Персональные сообщения: личная шапка + общий клубный блок ───────────
        for player in players:
            matches_result = await session.execute(
                select(Match).where(
                    or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
                    Match.status == MatchStatus.completed,
                    Match.completed_at >= week_ago,
                )
            )
            matches = matches_result.scalars().all()

            rank = rank_map.get(player.id, 0)
            wins = sum(1 for m in matches if m.winner_id == player.id)
            draws = sum(1 for m in matches if m.winner_id is None)
            losses = len(matches) - wins - draws
            rating_delta = sum(match_rating_delta(m, player.id) for m in matches)
            sign = "+" if rating_delta >= 0 else ""

            if not matches:
                last_r = await session.execute(
                    select(Match)
                    .where(
                        or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
                        Match.status == MatchStatus.completed,
                    )
                    .order_by(desc(Match.completed_at))
                    .limit(1)
                )
                last_m = last_r.scalar_one_or_none()
                vanished = (
                    last_m is not None
                    and last_m.completed_at is not None
                    and last_m.completed_at < two_weeks_ago
                )
                vanished_line = "👻 Куда пропал? Тебя давно не видели за столом!\n" if vanished else ""
                header = (
                    f"📊 <b>Итоги недели</b>\n\n"
                    f"На этой неделе матчей не было.\n"
                    f"Твой рейтинг: <b>{round(player.rating, 1)}</b> pts — #{rank}\n"
                    f"{vanished_line}"
                    f"<i>«Ты либо занят жизнью, либо занят умиранием.»</i>\n"
                )
            else:
                draws_str = f"  |  🤝 Ничьих: <b>{draws}</b>" if draws > 0 else ""
                header = (
                    f"📊 <b>Итоги недели</b>\n\n"
                    f"🏆 Побед: <b>{wins}</b>{draws_str}  |  💔 Поражений: <b>{losses}</b>\n"
                    f"📈 Рейтинг: <b>{round(player.rating, 1)}</b> pts "
                    f"({sign}{round(rating_delta, 1)}) — #{rank}\n"
                )

            text = header + "\n" + club_block
            try:
                await bot.send_message(player.telegram_id, text)
            except Exception:
                pass

    logger.info("Еженедельный дайджест отправлен")


# ── Итоги дня (21:30 МСК) ─────────────────────────────────────────────────────


async def send_daily_summary(bot: Bot) -> None:
    """Каждый день в 21:30 МСК отправляет игрокам сводку за день + «матч дня»."""
    async with async_session() as session:
        msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + MSK_OFFSET
        day_start = msk_day_start()   # граница дня по МСК в UTC-naive

        r = await session.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.completed,
                Match.completed_at >= day_start,
            )
            .options(selectinload(Match.challenger), selectinload(Match.challenged))
            .order_by(Match.completed_at)
        )
        matches = r.scalars().all()

        if not matches:
            logger.info("Итоги дня: матчей не было, пропускаем")
            return

        players_r = await session.execute(select(Player))
        players = players_r.scalars().all()
        name_map = {p.id: p.display_name for p in players}

        # ── Агрегаты по игрокам ────────────────────────────────────────────────
        stats: dict[int, dict] = {}
        delta_sum: dict[int, float] = {}
        for m in matches:
            for pid in (m.challenger_id, m.challenged_id):
                st = stats.setdefault(pid, {"w": 0, "l": 0, "d": 0, "total": 0})
                st["total"] += 1
                delta_sum[pid] = delta_sum.get(pid, 0.0) + match_rating_delta(m, pid)
            if m.winner_id is None:
                stats[m.challenger_id]["d"] += 1
                stats[m.challenged_id]["d"] += 1
            else:
                stats[m.winner_id]["w"] += 1
                lid = m.challenged_id if m.winner_id == m.challenger_id else m.challenger_id
                stats[lid]["l"] += 1

        # Полоска формы за день: исход каждого матча игрока в хронологии (matches asc)
        player_form: dict[int, list[str]] = {}
        for m in matches:
            for pid in (m.challenger_id, m.challenged_id):
                if m.winner_id is None:
                    icon = "🟨"
                elif m.winner_id == pid:
                    icon = "🟩"
                else:
                    icon = "🟥"
                player_form.setdefault(pid, []).append(icon)

        total_sets = sum(len(m.sets_data) if m.sets_data else 0 for m in matches)
        date_str = msk_now.strftime("%d.%m")

        lines = [
            f"📅 <b>Итоги дня — {date_str}</b>\n",
            f"⚡ Сыграно: <b>{pluralize_matches(len(matches))}</b>, <b>{pluralize_sets(total_sets)}</b>\n",
            "🏆 <b>Топ дня:</b>",
        ]

        medals = ["🥇", "🥈", "🥉"]
        sorted_players = sorted(
            stats.items(), key=lambda x: (x[1]["w"], x[1]["total"]), reverse=True
        )
        for i, (pid, st) in enumerate(sorted_players):
            prefix = medals[i] if i < 3 else f"{i + 1}."
            draws_str = f"–{st['d']}🤝" if st["d"] else ""
            # Полоска формы — максимум 7 последних, чтобы строка не переносилась
            form = "".join(player_form.get(pid, [])[-7:])
            lines.append(
                f"{prefix} <b>{h(name_map.get(pid, '?'))}</b> — "
                f"{st['w']}–{st['l']}{draws_str}  {form}"
            )

        # Рост рейтинга за день: лучший (+) и отрицательный (−)
        if delta_sum:
            best_id = max(delta_sum, key=delta_sum.get)
            worst_id = min(delta_sum, key=delta_sum.get)
            growth_lines = []
            if delta_sum[best_id] > 0:
                growth_lines.append(
                    f"📈 Лучший рост: <b>{h(name_map.get(best_id, '?'))}</b> "
                    f"(+{round(delta_sum[best_id], 1)} pts)"
                )
            if delta_sum[worst_id] < 0:
                growth_lines.append(
                    f"📉 Отрицательный рост: <b>{h(name_map.get(worst_id, '?'))}</b> "
                    f"({round(delta_sum[worst_id], 1)} pts)"
                )
            if growth_lines:
                lines.append("")
                lines.extend(growth_lines)

        # Нагибатель дня — самая длинная серия побед за день
        run_pid = None
        run_best = 0
        for pid, outcomes in player_form.items():
            run = cur = 0
            for o in outcomes:
                if o == "🟩":
                    cur += 1
                    run = max(run, cur)
                else:
                    cur = 0
            if run > run_best:
                run_best = run
                run_pid = pid
        if run_pid and run_best >= 2:
            lines.append(
                f"\n🔥 Нагибатель дня — <b>{h(name_map.get(run_pid, '?'))}</b>: "
                f"{pluralize_wins(run_best)} подряд"
            )

        # Чаще всего самбовались — пара, сыгравшая больше всех за день
        pair_today: dict[tuple[int, int], int] = {}
        for m in matches:
            key = (min(m.challenger_id, m.challenged_id), max(m.challenger_id, m.challenged_id))
            pair_today[key] = pair_today.get(key, 0) + 1
        if pair_today:
            (pa, pb), pn = max(pair_today.items(), key=lambda kv: kv[1])
            if pn >= 2:
                lines.append(
                    f"🤼 Чаще всего самбовались — <b>{h(name_map.get(pa, '?'))}</b> vs "
                    f"<b>{h(name_map.get(pb, '?'))}</b>: {pluralize_matches(pn)}"
                )

        # Матч дня
        mod = pick_match_of_day(matches)
        if mod:
            ch = name_map.get(mod.challenger_id, "?")
            cd = name_map.get(mod.challenged_id, "?")
            score_str = match_score_challenger_first(mod)
            reason = match_drama_reason(mod)
            lines.append(
                f"\n🌟 <b>Матч дня</b>\n"
                f"<b>{h(ch)}</b> vs <b>{h(cd)}</b> — {score_str}\n"
                f"<i>{reason}</i>"
            )

        # Матчи дня — общий лог клуба (нейтрально, счёт в перспективе challenger, победитель жирным)
        log_lines = ["\n📋 <b>Матчи дня:</b>"]
        for m in matches:
            mch = h(name_map.get(m.challenger_id, "?"))
            mcd = h(name_map.get(m.challenged_id, "?"))
            score = match_score_challenger_first(m)
            if m.winner_id == m.challenger_id:
                pair = f"<b>{mch}</b> vs {mcd}"
            elif m.winner_id == m.challenged_id:
                pair = f"{mch} vs <b>{mcd}</b>"
            else:
                pair = f"{mch} vs {mcd} 🤝"
            log_lines.append(f"{pair}  <i>{score}</i>")
        lines.append("\n".join(log_lines))

        text = "\n".join(lines)
        for p in players:
            try:
                await bot.send_message(p.telegram_id, text)
            except Exception:
                pass

    logger.info("Итоги дня отправлены")


# ── Еженедельный offsite-бэкап БД админу в личку ─────────────────────────────

async def send_db_backup(bot: Bot) -> None:
    """Раз в неделю шлёт файл БД админу в Telegram.

    Offsite-страховка: серверные бэкапы лежат на том же VPS, что и база, —
    при потере сервера пропадает всё. Файл маленький (десятки КБ).
    ADMIN_ID читаем лениво — на момент импорта .env может быть ещё не загружен.
    """
    admin_id = env_int("ADMIN_ID")
    if not admin_id:
        return
    db_path = DATABASE_URL.split("///")[-1]
    if not os.path.exists(db_path):
        logger.warning("Бэкап БД: файл %s не найден", db_path)
        return
    date_str = (datetime.now(timezone.utc) + MSK_OFFSET).strftime("%Y-%m-%d")
    try:
        await bot.send_document(
            admin_id,
            FSInputFile(db_path, filename=f"bottennis_{date_str}.db"),
            caption=f"💾 Еженедельный бэкап базы — {date_str}",
        )
        logger.info("Бэкап БД отправлен админу")
    except Exception:
        logger.exception("Не удалось отправить бэкап БД админу")


MONTH_NAMES_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


# ── Итоги месяца (1-е число, 10:00 МСК) ──────────────────────────────────────

async def send_monthly_summary(bot: Bot) -> None:
    """1-го числа в 10:00 МСК отправляет всем игрокам итоги прошлого месяца."""
    async with async_session() as session:
        msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + MSK_OFFSET
        # Граница: начало текущего месяца по МСК = конец прошлого
        month_end_msk = msk_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_last_day = month_end_msk - timedelta(days=1)
        month_start_msk = prev_last_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        month_start_utc = month_start_msk - MSK_OFFSET
        month_end_utc = month_end_msk - MSK_OFFSET

        month_label = f"{MONTH_NAMES_GEN[month_start_msk.month]} {month_start_msk.year}"

        matches_r = await session.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.completed,
                Match.completed_at >= month_start_utc,
                Match.completed_at < month_end_utc,
            )
            .options(selectinload(Match.challenger), selectinload(Match.challenged))
        )
        matches = matches_r.scalars().all()

        if not matches:
            logger.info("Итоги месяца %s: матчей не было, пропускаем", month_label)
            return

        players_r = await session.execute(select(Player))
        players = players_r.scalars().all()
        name_map = {p.id: p.display_name for p in players}

        wins: dict[int, int] = {}
        losses: dict[int, int] = {}
        draws: dict[int, int] = {}
        match_count: dict[int, int] = {}
        delta_sum: dict[int, float] = {}

        for m in matches:
            for pid in (m.challenger_id, m.challenged_id):
                match_count[pid] = match_count.get(pid, 0) + 1
                delta_sum[pid] = delta_sum.get(pid, 0.0) + match_rating_delta(m, pid)
            if m.winner_id is None:
                draws[m.challenger_id] = draws.get(m.challenger_id, 0) + 1
                draws[m.challenged_id] = draws.get(m.challenged_id, 0) + 1
            else:
                wins[m.winner_id] = wins.get(m.winner_id, 0) + 1
                lid = m.challenged_id if m.winner_id == m.challenger_id else m.challenger_id
                losses[lid] = losses.get(lid, 0) + 1

        total_sets = sum(len(m.sets_data) if m.sets_data else 0 for m in matches)

        lines = [
            f"📆 <b>Итоги месяца — {month_label}</b>\n",
            f"⚡ Сыграно: <b>{pluralize_matches(len(matches))}</b>, <b>{pluralize_sets(total_sets)}</b>\n",
            "🏆 <b>Топ месяца:</b>",
        ]

        sorted_ids = sorted(
            match_count,
            key=lambda pid: (wins.get(pid, 0), match_count.get(pid, 0)),
            reverse=True,
        )
        medals = ["🥇", "🥈", "🥉"]
        for i, pid in enumerate(sorted_ids):
            prefix = medals[i] if i < 3 else f"{i + 1}."
            w = wins.get(pid, 0)
            lo = losses.get(pid, 0)
            d = draws.get(pid, 0)
            total = match_count[pid]
            wr = int(w / total * 100) if total else 0
            draws_str = f"–{d}🤝" if d else ""
            lines.append(
                f"{prefix} <b>{h(name_map.get(pid, '?'))}</b> — "
                f"{w}–{lo}{draws_str}  <i>({wr}%)</i>"
            )

        if delta_sum:
            best_id = max(delta_sum, key=delta_sum.get)
            if delta_sum[best_id] > 0:
                lines.append(
                    f"\n📈 Лучший рост — <b>{h(name_map.get(best_id, '?'))}</b>: "
                    f"+{round(delta_sum[best_id], 1)} pts"
                )
            worst_id = min(delta_sum, key=delta_sum.get)
            if delta_sum[worst_id] < 0:
                lines.append(
                    f"📉 Отрицательный рост — <b>{h(name_map.get(worst_id, '?'))}</b>: "
                    f"{round(delta_sum[worst_id], 1)} pts"
                )

        most_active_id = max(match_count, key=match_count.get)
        lines.append(
            f"🏓 Главный теннисист — <b>{h(name_map.get(most_active_id, '?'))}</b>: "
            f"{pluralize_matches(match_count[most_active_id])}"
        )
        derby = _most_played_pair(matches, name_map)
        if derby:
            lines.append(derby)
        streak = _longest_streak(matches, name_map, "месяца")
        if streak:
            lines.append(streak)

        mod = pick_match_of_day(matches)
        if mod:
            ch = name_map.get(mod.challenger_id, "?")
            cd = name_map.get(mod.challenged_id, "?")
            score_str = match_score_challenger_first(mod)
            reason = match_drama_reason(mod)
            lines.append(
                f"\n🌟 <b>Матч месяца</b>\n"
                f"<b>{h(ch)}</b> vs <b>{h(cd)}</b> — {score_str}\n"
                f"<i>{reason}</i>"
            )

        text = "\n".join(lines)
        for p in players:
            try:
                await bot.send_message(p.telegram_id, text)
            except Exception:
                pass

    logger.info("Итоги месяца за %s отправлены", month_label)


# ── Инициализация планировщика ────────────────────────────────────────────────

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Проверка незавершённых матчей — каждый час
    scheduler.add_job(
        send_match_reminders,
        IntervalTrigger(hours=1),
        args=[bot],
        id="match_reminders",
    )

    # Еженедельный дайджест — каждый понедельник в 9:00 МСК (06:00 UTC)
    scheduler.add_job(
        send_weekly_digest,
        CronTrigger(day_of_week="mon", hour=6, minute=0),
        args=[bot],
        id="weekly_digest",
    )

    # Итоги дня — каждый день в 21:30 МСК (18:30 UTC)
    scheduler.add_job(
        send_daily_summary,
        CronTrigger(hour=18, minute=30),
        args=[bot],
        id="daily_summary",
    )

    # Offsite-бэкап БД админу — каждый понедельник в 9:30 МСК (06:30 UTC)
    scheduler.add_job(
        send_db_backup,
        CronTrigger(day_of_week="mon", hour=6, minute=30),
        args=[bot],
        id="db_backup",
    )

    # Итоги месяца — 1-го числа в 10:00 МСК (07:00 UTC)
    scheduler.add_job(
        send_monthly_summary,
        CronTrigger(day=1, hour=7, minute=0),
        args=[bot],
        id="monthly_summary",
    )

    return scheduler
